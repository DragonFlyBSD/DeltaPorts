"""Tests for the edit-intent DSL library (plan Step 25b).

The library is pure — no LLM, no subprocess (other than git for the
diff helper, which we mock). Each test sets up a minimal tmp_path
workspace with `ports/<origin>/` files, instantiates Translator,
and asserts the result of one or more intent applications.

Covers: validation, per-intent compat renderers, per-intent dops
renderers, half-migration invariant, convert-mode restrictions,
IntentLog size caps, mode-restricted intents.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

from dportsv3.agent.edit_intent import (
    AddFile, AddPatch, BumpPortrevision, ChangeMakefile, DropPatch,
    EditResult, IntentError, IntentLog, ReplaceInPatch, Translator,
    parse_intent, schema_for,
)


# --------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------


def _make_workspace(tmp_path: Path, origin: str = "devel/foo") -> Path:
    """Build a minimal workspace with an initialized git repo so
    Translator.git_diff has something to diff against."""
    ws = tmp_path / "DeltaPorts"
    port = ws / "ports" / origin
    port.mkdir(parents=True)
    subprocess.run(["git", "-C", str(ws), "init", "-q", "-b", "main"],
                   check=True)
    subprocess.run(["git", "-C", str(ws), "config", "user.email", "t@example.com"],
                   check=True)
    subprocess.run(["git", "-C", str(ws), "config", "user.name", "t"],
                   check=True)
    # One baseline file so HEAD exists.
    (ws / "README").write_text("baseline\n")
    subprocess.run(["git", "-C", str(ws), "add", "README"], check=True)
    subprocess.run(["git", "-C", str(ws), "commit", "-qm", "init"],
                   check=True)
    return ws


# --------------------------------------------------------------------
# Schema / parse_intent
# --------------------------------------------------------------------


class TestParseIntent:

    def test_drop_patch_round_trips(self):
        intent = parse_intent({
            "type": "drop_patch",
            "target": "dragonfly/patch-foo.c",
            "reason": "obsolete in upstream",
        })
        assert isinstance(intent, DropPatch)
        assert intent.target == "dragonfly/patch-foo.c"
        assert intent.reason == "obsolete in upstream"

    def test_accepts_json_string(self):
        intent = parse_intent(json.dumps({
            "type": "drop_patch",
            "target": "dragonfly/patch-foo.c",
            "reason": "x",
        }))
        assert isinstance(intent, DropPatch)

    def test_invalid_json_rejected(self):
        with pytest.raises(IntentError, match="not valid JSON"):
            parse_intent("not actually json")

    def test_unknown_type_rejected(self):
        with pytest.raises(IntentError, match="unknown or missing"):
            parse_intent({"type": "do_something_weird"})

    def test_missing_required_field_rejected(self):
        with pytest.raises(IntentError, match="failed schema"):
            parse_intent({"type": "drop_patch", "target": "x"})  # no reason

    def test_additional_properties_rejected(self):
        with pytest.raises(IntentError, match="failed schema"):
            parse_intent({
                "type": "drop_patch", "target": "x", "reason": "y",
                "extra_field": "no",
            })

    def test_replace_in_patch_with_defaults(self):
        intent = parse_intent({
            "type": "replace_in_patch",
            "target": "dragonfly/patch-foo.c",
            "find": "OLD", "replace": "NEW",
        })
        assert intent.occurrence == 1

    def test_replace_in_patch_refuses_dops_target(self):
        """Regression: agent tried replace_in_patch(target=overlay.dops)
        as a text editor; renderer appended escalating
        text.replace_once directives, corrupting the overlay
        (devel_gperf-20260526-064013Z). Validator now refuses any
        .dops target — replace_in_patch is for patch hunks only."""
        with pytest.raises(IntentError, match="refuses target"):
            parse_intent({
                "type": "replace_in_patch",
                "target": "overlay.dops",
                "find": "old", "replace": "new",
            })

    def test_replace_in_patch_refuses_any_dops_target(self):
        """Any *.dops file is refused, not just overlay.dops literally."""
        with pytest.raises(IntentError, match="refuses target"):
            parse_intent({
                "type": "replace_in_patch",
                "target": "ports/devel/foo/other.dops",
                "find": "x", "replace": "y",
            })

    def test_replace_in_patch_allows_patch_targets(self):
        """Non-.dops targets (the intended use case) still validate."""
        intent = parse_intent({
            "type": "replace_in_patch",
            "target": "dragonfly/patch-lib_getopt.c",
            "find": "OLD", "replace": "NEW",
        })
        assert intent.target == "dragonfly/patch-lib_getopt.c"

    def test_add_patch_anyof_requires_diff_or_dupe(self):
        with pytest.raises(IntentError, match="failed schema"):
            parse_intent({"type": "add_patch", "target": "x"})

    def test_add_file_requires_content_for_resource(self):
        with pytest.raises(IntentError, match="failed schema"):
            parse_intent({"type": "add_file", "dest": "x", "kind": "resource"})

    def test_add_file_requires_source_for_materialize(self):
        with pytest.raises(IntentError, match="failed schema"):
            parse_intent({"type": "add_file", "dest": "x", "kind": "materialize"})

    def test_change_makefile_rejects_lowercase_key(self):
        with pytest.raises(IntentError, match="failed schema"):
            parse_intent({
                "type": "change_makefile", "path": "Makefile",
                "key": "lowercase", "value": "x", "op": "set",
            })

    def test_change_makefile_rejects_bad_op(self):
        with pytest.raises(IntentError, match="failed schema"):
            parse_intent({
                "type": "change_makefile", "path": "Makefile",
                "key": "USES", "value": "x", "op": "delete",  # invalid
            })


class TestSchemaFor:

    def test_returns_valid_schema(self):
        s = schema_for("drop_patch")
        assert s["title"] == "drop_patch"
        assert "type" in s["required"]

    def test_unknown_type_raises(self):
        with pytest.raises(IntentError):
            schema_for("nope")


# --------------------------------------------------------------------
# Translator — compat mode renderers
# --------------------------------------------------------------------


class TestCompatRenderers:

    @pytest.fixture
    def t(self, tmp_path):
        ws = _make_workspace(tmp_path)
        return Translator(ws, "devel/foo", "compat")

    def test_replace_in_patch_happy(self, t):
        patch = t.port_path("dragonfly/patch-foo.c")
        patch.parent.mkdir(parents=True)
        patch.write_text("ORIGINAL CONTENT here\n")

        result = t.apply({
            "type": "replace_in_patch",
            "target": "dragonfly/patch-foo.c",
            "find": "ORIGINAL", "replace": "NEW",
        })
        assert result.ok is True
        assert patch.read_text() == "NEW CONTENT here\n"
        assert "+NEW CONTENT" in result.substrate_diff

    def test_replace_in_patch_missing_target(self, t):
        result = t.apply({
            "type": "replace_in_patch",
            "target": "dragonfly/nope.c",
            "find": "x", "replace": "y",
        })
        assert result.ok is False
        assert "does not exist" in result.error

    def test_replace_in_patch_find_not_found(self, t):
        patch = t.port_path("dragonfly/patch-foo.c")
        patch.parent.mkdir(parents=True)
        patch.write_text("hello\n")

        result = t.apply({
            "type": "replace_in_patch",
            "target": "dragonfly/patch-foo.c",
            "find": "WORLD", "replace": "x",
        })
        assert result.ok is False
        assert "find string not found" in result.error

    def test_replace_in_patch_nth_occurrence(self, t):
        patch = t.port_path("dragonfly/patch-foo.c")
        patch.parent.mkdir(parents=True)
        patch.write_text("X Y X Y X\n")

        result = t.apply({
            "type": "replace_in_patch",
            "target": "dragonfly/patch-foo.c",
            "find": "X", "replace": "Z", "occurrence": 2,
        })
        assert result.ok is True
        assert patch.read_text() == "X Y Z Y X\n"

    def test_drop_patch_happy(self, t):
        patch = t.port_path("dragonfly/patch-old.c")
        patch.parent.mkdir(parents=True)
        patch.write_text("--- a/x\n+++ b/x\n")
        subprocess.run(["git", "-C", str(t.workspace), "add",
                        "ports/devel/foo/dragonfly/patch-old.c"], check=True)
        subprocess.run(["git", "-C", str(t.workspace), "commit", "-qm", "add"],
                       check=True)

        result = t.apply({
            "type": "drop_patch",
            "target": "dragonfly/patch-old.c",
            "reason": "obsolete",
        })
        assert result.ok is True
        assert not patch.exists()
        assert "deleted file" in result.substrate_diff

    def test_drop_patch_missing_target(self, t):
        result = t.apply({
            "type": "drop_patch",
            "target": "dragonfly/never-existed.c",
            "reason": "x",
        })
        assert result.ok is False
        assert "does not exist" in result.error

    def test_add_patch_happy_with_inline_diff(self, t):
        result = t.apply({
            "type": "add_patch",
            "target": "dragonfly/patch-new.c",
            "diff": "--- a/src/x.c\n+++ b/src/x.c\n@@ -1 +1 @@\n-1\n+2\n",
        })
        assert result.ok is True
        target = t.port_path("dragonfly/patch-new.c")
        assert target.exists()
        assert "new file" in result.substrate_diff

    def test_add_patch_refuses_existing_target(self, t):
        existing = t.port_path("dragonfly/patch-here.c")
        existing.parent.mkdir(parents=True)
        existing.write_text("--- a\n+++ b\n")
        result = t.apply({
            "type": "add_patch",
            "target": "dragonfly/patch-here.c",
            "diff": "--- a\n+++ b\n",
        })
        assert result.ok is False
        assert "already exists" in result.error

    def test_add_file_resource_happy(self, t):
        result = t.apply({
            "type": "add_file",
            "dest": "files/post-install.sh",
            "kind": "resource",
            "content": "#!/bin/sh\necho hello\n",
        })
        assert result.ok is True
        target = t.port_path("files/post-install.sh")
        assert target.read_text().startswith("#!/bin/sh")

    def test_add_file_resource_refuses_existing(self, t):
        existing = t.port_path("files/here.txt")
        existing.parent.mkdir(parents=True)
        existing.write_text("x")
        result = t.apply({
            "type": "add_file",
            "dest": "files/here.txt", "kind": "resource", "content": "y",
        })
        assert result.ok is False
        assert "already exists" in result.error

    def test_add_file_materialize_stubbed_in_25b(self, t):
        """Materialize in compat mode is a 25b stub; explicit error
        so the agent doesn't silently succeed."""
        result = t.apply({
            "type": "add_file",
            "dest": "files/from-dfly.c",
            "kind": "materialize",
            "source": "lib/foo.c",
        })
        assert result.ok is False
        assert "not yet supported" in result.error

    def test_change_makefile_set_creates_file(self, t):
        result = t.apply({
            "type": "change_makefile",
            "path": "Makefile.DragonFly",
            "key": "USES", "value": "ssl", "op": "set",
        })
        assert result.ok is True
        target = t.port_path("Makefile.DragonFly")
        assert "USES=\tssl" in target.read_text()

    def test_change_makefile_append_to_existing(self, t):
        target = t.port_path("Makefile.DragonFly")
        target.write_text("USES=\tpkgconfig\n")
        result = t.apply({
            "type": "change_makefile",
            "path": "Makefile.DragonFly",
            "key": "USES", "value": "ssl", "op": "append",
        })
        assert result.ok is True
        assert "pkgconfig ssl" in target.read_text()

    def test_change_makefile_remove_token(self, t):
        target = t.port_path("Makefile.DragonFly")
        target.write_text("USES=\tpkgconfig ssl readline\n")
        result = t.apply({
            "type": "change_makefile",
            "path": "Makefile.DragonFly",
            "key": "USES", "value": "ssl", "op": "remove",
        })
        assert result.ok is True
        new = target.read_text()
        assert "ssl" not in new.split("=", 1)[1]
        assert "pkgconfig readline" in new

    def test_change_makefile_remove_missing_file_errors(self, t):
        result = t.apply({
            "type": "change_makefile",
            "path": "Makefile.DragonFly",
            "key": "USES", "value": "x", "op": "remove",
        })
        assert result.ok is False

    def test_bump_portrevision_increments(self, t):
        target = t.port_path("Makefile")
        target.write_text(
            "PORTNAME=\tfoo\nPORTVERSION=\t1.0\nPORTREVISION=\t3\n"
        )
        result = t.apply({"type": "bump_portrevision"})
        assert result.ok is True
        assert "PORTREVISION=\t4" in target.read_text()

    def test_bump_portrevision_inserts_when_missing(self, t):
        target = t.port_path("Makefile")
        target.write_text("PORTNAME=\tfoo\nPORTVERSION=\t1.0\n")
        result = t.apply({"type": "bump_portrevision"})
        assert result.ok is True
        assert "PORTREVISION=\t1" in target.read_text()


# --------------------------------------------------------------------
# Translator — dops mode renderers
# --------------------------------------------------------------------


class TestDopsRenderers:

    @pytest.fixture
    def t(self, tmp_path):
        ws = _make_workspace(tmp_path)
        return Translator(ws, "devel/foo", "dops")

    def test_replace_in_patch_emits_dops_statement(self, t):
        result = t.apply({
            "type": "replace_in_patch",
            "target": "dragonfly/patch-foo.c",
            "find": "OLD", "replace": "NEW",
        })
        assert result.ok is True
        overlay = t.port_path("overlay.dops")
        assert overlay.exists()
        contents = overlay.read_text()
        # Correct dops grammar: `text replace-once file <path> from "X" to "Y"`.
        # The prior form `text.replace_once file=...` was invalid
        # (engine parser rejects dots + named args).
        assert "text replace-once file dragonfly/patch-foo.c" in contents
        assert 'from "OLD"' in contents
        assert 'to "NEW"' in contents
        # Negative: the broken form must not appear.
        assert "text.replace_once" not in contents

    def test_drop_patch_removes_dops_statement(self, t):
        overlay = t.port_path("overlay.dops")
        overlay.write_text(
            "target @main\nport devel/foo\ntype port\n"
            'reason "x"\n\n'
            "patch apply dragonfly/patch-old.c\n"
            "patch apply dragonfly/patch-keep.c\n"
        )
        result = t.apply({
            "type": "drop_patch",
            "target": "dragonfly/patch-old.c",
            "reason": "obsolete",
        })
        assert result.ok is True
        new = overlay.read_text()
        assert "patch apply dragonfly/patch-old.c" not in new
        assert "patch apply dragonfly/patch-keep.c" in new

    def test_drop_patch_no_match_errors(self, t):
        overlay = t.port_path("overlay.dops")
        overlay.write_text("target @main\nport devel/foo\ntype port\n")
        result = t.apply({
            "type": "drop_patch",
            "target": "dragonfly/never-referenced.c", "reason": "x",
        })
        assert result.ok is False
        # Error names BOTH shapes so the agent doesn't get a
        # misleading "no patch apply" when the overlay would use
        # file materialize.
        assert "no `patch apply" in result.error
        assert "file materialize" in result.error

    def test_drop_patch_removes_file_materialize_and_deletes_file(self, t):
        """Regression: convert-produced overlays install patches via
        `file materialize` rather than `patch apply`. drop_patch must
        recognize this shape and delete both the overlay line and the
        referenced patch file (gperf 2026-05-26 trap)."""
        patch_file = t.port_path("dragonfly/patch-lib_getopt.c")
        patch_file.parent.mkdir(parents=True, exist_ok=True)
        patch_file.write_text("--- a\n+++ b\n@@ -1 +1 @@\n-x\n+y\n")
        overlay = t.port_path("overlay.dops")
        overlay.write_text(
            "target @main\nport devel/foo\ntype port\n"
            'reason "x"\n\n'
            "file materialize dragonfly/patch-lib_getopt.c -> "
            "dragonfly/patch-lib_getopt.c\n"
            "file materialize dragonfly/patch-keep.c -> "
            "dragonfly/patch-keep.c\n"
        )
        result = t.apply({
            "type": "drop_patch",
            "target": "dragonfly/patch-lib_getopt.c",
            "reason": "obsolete upstream",
        })
        assert result.ok is True, result.error
        new = overlay.read_text()
        # Line removed from overlay.
        assert "patch-lib_getopt.c" not in new
        # Sibling materialize statement kept.
        assert "patch-keep.c" in new
        # Patch file deleted.
        assert not patch_file.exists()
        # paths_changed names both the overlay AND the deleted file.
        assert any("overlay.dops" in p for p in result.paths_changed)
        assert any("patch-lib_getopt.c" in p for p in result.paths_changed)

    def test_drop_patch_only_matches_patch_shaped_file_materialize(self, t):
        """`file materialize` for non-patch destinations (e.g. source
        replacements like file materialize src/foo -> some/dest) must
        NOT match drop_patch — the looks-like-patch guard requires
        dragonfly/patch-* prefix."""
        overlay = t.port_path("overlay.dops")
        overlay.write_text(
            "file materialize src/replacement.c -> work/foo/main.c\n"
        )
        result = t.apply({
            "type": "drop_patch",
            "target": "work/foo/main.c",
            "reason": "x",
        })
        assert result.ok is False
        assert "no `patch apply" in result.error

    def test_add_file_refuses_makefile_dragonfly_in_dops_mode(self, t):
        """archivers/liblz4 2026-05-26: agent created Makefile.DragonFly
        via add_file on a dops port, hit substrate_invariant on the
        very next intent, and self-induced a deadlock. The renderer
        now refuses this at the substrate boundary with a clear
        message pointing at change_makefile as the alternative."""
        result = t.apply({
            "type": "add_file",
            "dest": "Makefile.DragonFly", "kind": "resource",
            "content": "USES+=ssl\n",
        })
        assert result.ok is False
        assert "half-migrated" in result.error
        assert "change_makefile" in result.error

    def test_add_file_refuses_makefile_dragonfly_variants_in_dops_mode(self, t):
        """Suffix variants like Makefile.DragonFly.@main are also
        invariant violators."""
        result = t.apply({
            "type": "add_file",
            "dest": "Makefile.DragonFly.@main", "kind": "resource",
            "content": "",
        })
        assert result.ok is False
        assert "half-migrated" in result.error

    def test_add_file_allows_non_makefile_dest_in_dops_mode(self, t):
        """Other dests (resources, materialized files) keep working."""
        result = t.apply({
            "type": "add_file",
            "dest": "files/post-install.sh", "kind": "resource",
            "content": "#!/bin/sh\n",
        })
        assert result.ok is True

    def test_drop_patch_no_match_diagnostic_when_mk_target_set_present(self, t):
        """Diagnostic improvement: when the overlay has an mk target
        block referencing the target (liblz4-shaped heredoc patch),
        the refusal message points at MANUAL escalation, not at
        change_makefile / add_file workarounds."""
        overlay = t.port_path("overlay.dops")
        overlay.write_text(
            "port devel/foo\ntype port\ntarget @any\n"
            'reason "x"\n\n'
            "mk target set post-extract <<MK\n"
            "    @${REINPLACE_CMD} -e 's,foo,bar,' dfly-patch\n"
            "MK\n"
        )
        result = t.apply({
            "type": "drop_patch",
            "target": "dfly-patch", "reason": "obsolete",
        })
        assert result.ok is False
        assert "mk target" in result.error
        assert "escalate to MANUAL" in result.error
        assert "change_makefile" in result.error  # warns against the workaround

    def test_drop_patch_tolerates_whitespace_around_arrow(self, t):
        """`file materialize <src> -> <dest>` should match whether
        the agent emitted tight or loose spacing around the arrow."""
        patch_file = t.port_path("dragonfly/patch-x.c")
        patch_file.parent.mkdir(parents=True, exist_ok=True)
        patch_file.write_text("--- a\n+++ b\n")
        overlay = t.port_path("overlay.dops")
        overlay.write_text(
            # Extra spaces before/after the arrow.
            "file materialize dragonfly/patch-x.c   ->   dragonfly/patch-x.c\n"
        )
        result = t.apply({
            "type": "drop_patch",
            "target": "dragonfly/patch-x.c", "reason": "x",
        })
        assert result.ok is True
        assert not patch_file.exists()

    def test_add_patch_writes_file_and_statement(self, t):
        result = t.apply({
            "type": "add_patch",
            "target": "dragonfly/patch-new.c",
            "diff": "--- a\n+++ b\n",
        })
        assert result.ok is True
        target = t.port_path("dragonfly/patch-new.c")
        overlay = t.port_path("overlay.dops")
        assert target.exists()
        assert "patch apply dragonfly/patch-new.c" in overlay.read_text()

    def test_change_makefile_emits_valid_mk_grammar(self, t):
        """change_makefile in dops mode must emit grammar the
        engine parser actually accepts: `mk <action> VAR "value"`
        (space-separated tokens, quoted string value), NOT the
        prior `mk.var.<op> var=K value=V` form which was invalid
        dops and got silently appended to overlay.dops, breaking
        materialize_dports (archivers/liblz4 2026-05-26)."""
        result = t.apply({
            "type": "change_makefile",
            "path": "Makefile.DragonFly",
            "key": "USES", "value": "ssl", "op": "append",
        })
        assert result.ok is True
        overlay_text = t.port_path("overlay.dops").read_text()
        # `append` op → dops `add` action.
        assert 'mk add USES "ssl"' in overlay_text
        # Negative: the old broken form must not appear.
        assert "mk.var" not in overlay_text
        assert "var=USES" not in overlay_text

    def test_change_makefile_set_op_quotes_value(self, t):
        result = t.apply({
            "type": "change_makefile",
            "path": "Makefile.DragonFly",
            "key": "DFLY_PATCH", "value": "/some/path with space",
            "op": "set",
        })
        assert result.ok is True
        overlay_text = t.port_path("overlay.dops").read_text()
        assert 'mk set DFLY_PATCH "/some/path with space"' in overlay_text

    def test_change_makefile_escapes_quotes_and_backslashes(self, t):
        result = t.apply({
            "type": "change_makefile",
            "path": "Makefile.DragonFly",
            "key": "CFG", "value": 'a"b\\c', "op": "set",
        })
        assert result.ok is True
        overlay_text = t.port_path("overlay.dops").read_text()
        assert 'mk set CFG "a\\"b\\\\c"' in overlay_text

    def test_change_makefile_remove_op_emits_mk_remove(self, t):
        result = t.apply({
            "type": "change_makefile",
            "path": "Makefile.DragonFly",
            "key": "USES", "value": "ssl", "op": "remove",
        })
        assert result.ok is True
        overlay_text = t.port_path("overlay.dops").read_text()
        assert 'mk remove USES "ssl"' in overlay_text


# --------------------------------------------------------------------
# Half-migration invariant — enforced at worker.apply_intent (25c)
# via assess_dops.action='surface_invariant', NOT inside the
# Translator (the in-transaction tracker that earlier drafts
# carried turned out to be unreachable: mode is fixed at construction
# and renderers can't cross modes). The real guard is in
# test_refuses_substrate_in_half_migrated_state in
# test_apply_intent_tool.py.
# --------------------------------------------------------------------


# --------------------------------------------------------------------
# Mode restrictions
# --------------------------------------------------------------------


class TestModeRestrictions:

    def test_convert_to_dops_in_patch_mode_rejected(self, tmp_path):
        ws = _make_workspace(tmp_path)
        t = Translator(ws, "devel/foo", "compat")
        result = t.apply({"type": "convert_to_dops"})
        assert result.ok is False
        assert "only the convert agent" in result.error

    def test_invalid_mode_at_construction(self, tmp_path):
        ws = _make_workspace(tmp_path)
        with pytest.raises(ValueError, match="invalid mode"):
            Translator(ws, "devel/foo", "bogus")  # type: ignore[arg-type]


# --------------------------------------------------------------------
# Path safety
# --------------------------------------------------------------------


class TestPathSafety:

    @pytest.fixture
    def t(self, tmp_path):
        ws = _make_workspace(tmp_path)
        return Translator(ws, "devel/foo", "compat")

    def test_dotdot_in_target_rejected(self, t):
        result = t.apply({
            "type": "replace_in_patch",
            "target": "../escape.c", "find": "x", "replace": "y",
        })
        assert result.ok is False
        assert "must be a relative path" in result.error

    def test_absolute_target_rejected(self, t):
        result = t.apply({
            "type": "drop_patch",
            "target": "/etc/passwd", "reason": "no",
        })
        assert result.ok is False


# --------------------------------------------------------------------
# IntentLog
# --------------------------------------------------------------------


class TestIntentLog:

    def test_append_records_entry(self):
        log = IntentLog(origin="devel/foo", target="@main",
                        mode_at_apply="compat", baseline_commit="abc")
        log.append({"type": "drop_patch", "target": "x", "reason": "y"},
                   ok=True, substrate_diff="diff")
        assert len(log.intents) == 1
        assert log.intents[0].seq == 0
        assert log.intents[0].ok is True

    def test_serialize_round_trips(self):
        log = IntentLog(origin="devel/foo", target="@main",
                        mode_at_apply="compat", baseline_commit="abc")
        log.append({"type": "drop_patch", "target": "x", "reason": "y"},
                   ok=True)
        doc = json.loads(log.to_json())
        assert doc["schema_version"] == 1
        assert doc["origin"] == "devel/foo"
        assert doc["mode_at_apply"] == "compat"
        assert len(doc["intents"]) == 1

    def test_count_cap_default_100(self):
        log = IntentLog(origin="devel/foo", target="@main",
                        mode_at_apply="compat", baseline_commit="abc")
        for i in range(100):
            log.append({"type": "drop_patch", "target": f"x{i}",
                        "reason": "y"}, ok=True)
        with pytest.raises(IntentError, match="exceeds 100 entries"):
            log.append({"type": "drop_patch", "target": "overflow",
                        "reason": "z"}, ok=True)

    def test_count_cap_env_override(self, monkeypatch):
        monkeypatch.setenv("DP_HARNESS_INTENT_MAX_COUNT", "3")
        log = IntentLog(origin="devel/foo", target="@main",
                        mode_at_apply="compat", baseline_commit="abc")
        for i in range(3):
            log.append({"type": "drop_patch", "target": f"x{i}",
                        "reason": "y"}, ok=True)
        with pytest.raises(IntentError, match="exceeds 3 entries"):
            log.append({"type": "drop_patch", "target": "y", "reason": "z"},
                       ok=True)

    def test_size_cap_rejects_huge_diff(self, monkeypatch):
        monkeypatch.setenv("DP_HARNESS_INTENT_MAX_BYTES", "1000")
        log = IntentLog(origin="devel/foo", target="@main",
                        mode_at_apply="compat", baseline_commit="abc")
        # First entry within budget.
        log.append({"type": "drop_patch", "target": "small", "reason": "x"},
                   ok=True, substrate_diff="x")
        # Second entry would blow the budget.
        big_diff = "x" * 2000
        with pytest.raises(IntentError, match="size would exceed"):
            log.append({"type": "drop_patch", "target": "big", "reason": "y"},
                       ok=True, substrate_diff=big_diff)


# --------------------------------------------------------------------
# add_patch from_dupe
# --------------------------------------------------------------------


class TestFromDupe:

    def test_from_dupe_picks_basename_match(self, tmp_path):
        ws = _make_workspace(tmp_path)
        t = Translator(ws, "devel/foo", "compat")
        genpatch = ws / ".genpatch-out"
        genpatch.mkdir()
        (genpatch / "patch-src_main.c").write_text("--- a/x\n+++ b/x\n")

        result = t.apply({
            "type": "add_patch",
            "target": "dragonfly/patch-src_main.c",
            "from_dupe": True,
        })
        assert result.ok is True
        target = t.port_path("dragonfly/patch-src_main.c")
        assert target.read_text() == "--- a/x\n+++ b/x\n"

    def test_from_dupe_no_match_errors(self, tmp_path):
        ws = _make_workspace(tmp_path)
        t = Translator(ws, "devel/foo", "compat")
        result = t.apply({
            "type": "add_patch",
            "target": "dragonfly/patch-missing.c",
            "from_dupe": True,
        })
        assert result.ok is False
        assert "no file matching basename" in result.error
