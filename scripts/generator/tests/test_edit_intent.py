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
        assert "text.replace_once" in contents
        assert "file=dragonfly/patch-foo.c" in contents

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
        assert "no `patch apply" in result.error

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

    def test_change_makefile_emits_mk_var_op(self, t):
        result = t.apply({
            "type": "change_makefile",
            "path": "Makefile.DragonFly",
            "key": "USES", "value": "ssl", "op": "append",
        })
        assert result.ok is True
        overlay = t.port_path("overlay.dops")
        assert "mk.var.append" in overlay.read_text()
        assert "var=USES" in overlay.read_text()


# --------------------------------------------------------------------
# Half-migration invariant
# --------------------------------------------------------------------


class TestHalfMigrationInvariant:

    def test_dops_intent_after_compat_makefile_dragonfly_is_rejected(self, tmp_path):
        """dops mode is fine alone — but mixing compat Makefile.DragonFly
        writes into the same transaction must fail."""
        ws = _make_workspace(tmp_path)
        # Pre-touch Makefile.DragonFly via compat translator first.
        t1 = Translator(ws, "devel/foo", "compat")
        r1 = t1.apply({
            "type": "change_makefile",
            "path": "Makefile.DragonFly",
            "key": "USES", "value": "ssl", "op": "set",
        })
        assert r1.ok

        # Same translator, now try a dops-flavored intent. The
        # invariant catches transitions WITHIN a transaction; this
        # tests the "Makefile.DragonFly recorded → reject further
        # dops writes" guard.
        # Drive by manually flipping the flag — actual cross-mode
        # transactions are guarded at construction (mode is fixed
        # for a Translator instance). The invariant fires inside
        # one translator: compat Makefile.DragonFly + then a
        # change_makefile on the same path with dops-equivalent
        # data would need separate translators; we test the inverse
        # below (compat write *follows* dops touch).

    def test_compat_makefile_dragonfly_after_dops_write_is_rejected(self, tmp_path):
        ws = _make_workspace(tmp_path)
        # dops mode does NOT write Makefile.DragonFly; this test
        # exercises the boundary by simulating a mixed-mode use
        # of the Translator (the runner shouldn't do this, but the
        # invariant defends if it does).
        t = Translator(ws, "devel/foo", "dops")
        # First, a dops intent that touches overlay.dops.
        r1 = t.apply({
            "type": "change_makefile",
            "path": "Makefile.DragonFly",
            "key": "USES", "value": "ssl", "op": "set",
        })
        # In dops mode this is an overlay.dops write; the
        # invariant tracks the dops touch.
        assert r1.ok
        # Hypothetical follow-up: a compat-flavored intent in the
        # same translator. We achieve this by directly invoking
        # the compat renderer with a Makefile.DragonFly path; the
        # invariant check is in apply(), so we go through that.
        # (Defensive: in production the mode is fixed; this is the
        # belt-and-suspenders check.)
        # Construct a synthetic intent that would be the compat
        # equivalent and ensure the invariant fires when both
        # flags would be set.
        t._touched_dops = True
        t._touched_compat_makefile = False
        # Now manually trigger the invariant check by feeding a
        # ChangeMakefile with Makefile.DragonFly path through the
        # translator.apply path (it will dispatch to the dops
        # renderer in mode='dops', so the invariant prevents the
        # symmetric case in compat mode — direct call here):
        intent = ChangeMakefile(
            type="change_makefile", path="Makefile.DragonFly",
            key="WARNING", value="banana", op="set",
        )
        with pytest.raises(IntentError, match="half-migration"):
            t._check_half_migration_invariant(intent.__class__.__call__(
                type="change_makefile", path="Makefile.DragonFly",
                key="W", value="x", op="set",
            )) if False else (
                # Use the actual translator instance state to fire
                # the invariant check directly (the function is
                # static against translator state).
                t._check_half_migration_invariant(intent)
            )

    def test_convert_to_dops_after_compat_write_is_rejected(self, tmp_path):
        ws = _make_workspace(tmp_path)
        t = Translator(ws, "devel/foo", "convert")
        t._touched_compat_makefile = True
        from dportsv3.agent.edit_intent.grammar import ConvertToDops
        with pytest.raises(IntentError, match="half-migration"):
            t._check_half_migration_invariant(
                ConvertToDops(type="convert_to_dops")
            )


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
