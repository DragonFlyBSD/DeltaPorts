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
            "target": "files/extra-config.in",
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

    def test_replace_in_patch_refuses_dragonfly_target(self):
        """Patch files under dragonfly/ are output artifacts, not edit
        targets. Text-editing a diff produces a patch that lies about
        its own bytes (devel_jwasm-20260602-204312Z anti-pattern).
        Correct recovery from a failing patch is drop_patch + add_patch
        (with corrected diff) or add_patch from_dupe=true."""
        with pytest.raises(IntentError, match="refuses target"):
            parse_intent({
                "type": "replace_in_patch",
                "target": "dragonfly/patch-src_foo.c",
                "find": "x", "replace": "y",
            })

    def test_replace_in_patch_refuses_any_dragonfly_subpath(self):
        """Refusal is path-prefix based; nested dragonfly/ targets also
        rejected."""
        with pytest.raises(IntentError, match="refuses target"):
            parse_intent({
                "type": "replace_in_patch",
                "target": "dragonfly/extra/patch-x.c",
                "find": "x", "replace": "y",
            })

    def test_replace_in_patch_allows_non_dragonfly_targets(self):
        """Targets outside dragonfly/ and not ending in .dops still
        validate — replace_in_patch is reserved for in-port files
        that have no dedicated edit intent."""
        intent = parse_intent({
            "type": "replace_in_patch",
            "target": "files/extra-config.in",
            "find": "OLD", "replace": "NEW",
        })
        assert intent.target == "files/extra-config.in"

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
            "target": "files/extra-config.in",
            "find": "OLD", "replace": "NEW",
        })
        assert result.ok is True
        overlay = t.port_path("overlay.dops")
        assert overlay.exists()
        contents = overlay.read_text()
        # Correct dops grammar: `text replace-once file <path> from "X" to "Y"`.
        # The prior form `text.replace_once file=...` was invalid
        # (engine parser rejects dots + named args).
        assert "text replace-once file files/extra-config.in" in contents
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

    def test_drop_patch_removes_patch_apply_and_deletes_file(self, t):
        """Symmetric to file_materialize cleanup: dropping a
        `patch apply` install directive must also delete the patch
        file on disk. Without this symmetry, a previous `add_patch`
        leaves an orphan that blocks the next `add_patch` with
        'patch already exists' (devel_jwasm-20260602-204312Z trap)."""
        patch_file = t.port_path("dragonfly/patch-src_H_memalloc.h")
        patch_file.parent.mkdir(parents=True, exist_ok=True)
        patch_file.write_text("--- a\n+++ b\n@@ -1 +1 @@\n-x\n+y\n")
        overlay = t.port_path("overlay.dops")
        overlay.write_text(
            "target @any\nport devel/foo\ntype port\n"
            'reason "x"\n\n'
            "patch apply dragonfly/patch-src_H_memalloc.h\n"
            "patch apply dragonfly/patch-keep.c\n"
        )
        result = t.apply({
            "type": "drop_patch",
            "target": "dragonfly/patch-src_H_memalloc.h",
            "reason": "diff was malformed; will re-add",
        })
        assert result.ok is True, result.error
        new = overlay.read_text()
        assert "patch-src_H_memalloc.h" not in new
        assert "patch-keep.c" in new
        # File deleted on disk so subsequent add_patch is not blocked.
        assert not patch_file.exists()
        assert any("overlay.dops" in p for p in result.paths_changed)
        assert any("patch-src_H_memalloc.h" in p for p in result.paths_changed)

    def test_drop_patch_patch_apply_without_file_on_disk_still_ok(self, t):
        """If the install directive exists but the file is missing
        (e.g. someone hand-deleted it), drop_patch must still succeed
        — the directive removal is the primary effect; file deletion
        is best-effort cleanup."""
        overlay = t.port_path("overlay.dops")
        overlay.write_text(
            "target @any\nport devel/foo\n"
            "patch apply dragonfly/patch-missing.c\n"
        )
        result = t.apply({
            "type": "drop_patch",
            "target": "dragonfly/patch-missing.c",
            "reason": "cleanup",
        })
        assert result.ok is True, result.error
        assert "patch-missing.c" not in overlay.read_text()

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

    def test_add_file_materialize_refuses_dotdot_dest(self, t):
        """Path-safety gap surfaced in Step C review: kind=materialize
        previously skipped port_path validation (only kind=resource
        triggered it because that branch wrote the file). An intent
        like add_file(kind='materialize', dest='../../etc/foo', ...)
        could write `file materialize ... -> ../../etc/foo` to
        overlay.dops, escaping the port subtree at compose time."""
        result = t.apply({
            "type": "add_file",
            "dest": "../escape.c", "kind": "materialize",
            "source": "src/x",
        })
        assert result.ok is False
        assert "must be a relative path" in result.error

    def test_add_file_materialize_refuses_absolute_dest(self, t):
        result = t.apply({
            "type": "add_file",
            "dest": "/etc/passwd", "kind": "materialize",
            "source": "src/x",
        })
        assert result.ok is False

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

    def test_change_makefile_set_op_re_emits_accumulate(self, t):
        """Step 38e: sequential set-ops for the same key accumulate as
        separate ``mk set KEY ...`` lines on disk. The engine processes
        them in declaration order (last-wins) so the composed Makefile
        gets the final value — functionally correct — but the substrate
        carries every re-emit.

        Pre-38e, an implicit prefilter (``_strip_existing_mk_set``)
        scrubbed prior ``mk set KEY`` lines before appending each new
        one. The prefilter was removed for two reasons: it was
        scope-blind (would have corrupted multi-target overlays once
        38d enabled per-target emission) and it baked cross-intent
        state mutation into a renderer's body, violating the "each
        intent does exactly one thing" principle.

        Cleanup of redundant lines is the agent's explicit
        responsibility via the Family A delete intents (see
        docs/intent-surface-gaps-plan.md).
        """
        r1 = t.apply({
            "type": "change_makefile", "path": "Makefile",
            "key": "BINARY_ALIAS", "value": "gmd5sum=md5 -r", "op": "set",
        })
        assert r1.ok is True
        r2 = t.apply({
            "type": "change_makefile", "path": "Makefile",
            "key": "BINARY_ALIAS", "value": "gmd5sum=md5", "op": "set",
        })
        assert r2.ok is True
        r3 = t.apply({
            "type": "change_makefile", "path": "Makefile",
            "key": "BINARY_ALIAS", "value": "gmd5sum=md5 -r", "op": "set",
        })
        assert r3.ok is True

        overlay_text = t.port_path("overlay.dops").read_text()
        mk_set_lines = [
            line for line in overlay_text.splitlines()
            if line.strip().startswith("mk set BINARY_ALIAS")
        ]
        # All three emissions land as separate lines, in order. The
        # engine plays them sequentially and the last write wins at
        # compose time.
        assert mk_set_lines == [
            'mk set BINARY_ALIAS "gmd5sum=md5 -r"',
            'mk set BINARY_ALIAS "gmd5sum=md5"',
            'mk set BINARY_ALIAS "gmd5sum=md5 -r"',
        ], (
            f"expected three accumulated mk set lines, got: "
            f"{mk_set_lines!r}\n"
            f"full overlay:\n{overlay_text}"
        )

    def test_change_makefile_unset_emits_mk_unset(self, t):
        """op=unset emits the dops ``mk unset KEY`` statement and
        accepts a payload that omits ``value`` entirely (the JSON
        schema marks it optional for this op)."""
        result = t.apply({
            "type": "change_makefile",
            "path": "Makefile",
            "key": "LICENSE_FILE",
            "op": "unset",
        })
        assert result.ok is True, result.error
        overlay_text = t.port_path("overlay.dops").read_text()
        assert "mk unset LICENSE_FILE" in overlay_text
        # No stray quoted-value tail (mk unset takes no value).
        assert "mk unset LICENSE_FILE \"" not in overlay_text

    def test_change_makefile_unset_ignores_value_when_provided(self, t):
        """An LLM passing ``value`` alongside ``op: "unset"`` should
        still produce a value-less ``mk unset`` statement — the
        translator ignores ``value`` on unset."""
        result = t.apply({
            "type": "change_makefile",
            "path": "Makefile",
            "key": "FOO",
            "value": "ignored-by-translator",
            "op": "unset",
        })
        assert result.ok is True
        overlay_text = t.port_path("overlay.dops").read_text()
        assert "mk unset FOO\n" in overlay_text + "\n"  # exact line
        assert "ignored-by-translator" not in overlay_text

    def test_change_makefile_unset_preserves_prior_mk_set(self, t):
        """unset-after-set on the same key keeps BOTH lines in the
        overlay. The engine processes ops in order with last-wins,
        so set-then-unset produces the right end state (FOO is set
        then deleted from the composed Makefile). Scrubbing the
        prior set would leave only ``mk unset FOO`` which fails at
        compose with ``assignment not found`` when upstream doesn't
        define FOO either — turning a logically-correct sequence
        into a compose error. The aesthetic concern of carrying
        both lines on disk is dominated by the correctness gain."""
        r1 = t.apply({
            "type": "change_makefile", "path": "Makefile",
            "key": "BAR", "value": "old", "op": "set",
        })
        assert r1.ok is True
        r2 = t.apply({
            "type": "change_makefile", "path": "Makefile",
            "key": "BAR", "op": "unset",
        })
        assert r2.ok is True
        overlay_text = t.port_path("overlay.dops").read_text()
        # Both lines remain — the engine handles ordering.
        assert 'mk set BAR "old"' in overlay_text
        assert "mk unset BAR" in overlay_text
        # And specifically: unset comes AFTER set (the order the
        # engine planner relies on for last-wins semantics).
        set_pos = overlay_text.index('mk set BAR "old"')
        unset_pos = overlay_text.index("mk unset BAR")
        assert set_pos < unset_pos

    def test_change_makefile_rejects_op_unset_with_required_value(self):
        """Negative: set/append/remove still require value. Schema
        enforces this via an allOf if/then so set/append/remove
        keep their original strictness while unset is permissive."""
        from dportsv3.agent.edit_intent import parse_intent, IntentError
        # value-less set must fail
        for op in ("set", "append", "remove"):
            try:
                parse_intent({
                    "type": "change_makefile", "path": "Makefile",
                    "key": "X", "op": op,
                })
            except IntentError as exc:
                assert "value" in str(exc), f"{op}: {exc}"
            else:
                raise AssertionError(f"{op} without value should have failed schema")

    def test_change_makefile_set_op_does_not_touch_unrelated_keys(self, t):
        """Step 38e: each ``op=set`` emission lands as a single line
        and never touches lines from prior intents — including ones
        for different keys (which would have been even more wrong
        than the same-key prefilter behavior, but the same principle
        applies: no cross-intent state mutation in the renderer)."""
        t.apply({
            "type": "change_makefile", "path": "Makefile",
            "key": "FOO", "value": "v1", "op": "set",
        })
        t.apply({
            "type": "change_makefile", "path": "Makefile",
            "key": "BAR", "value": "v2", "op": "set",
        })
        t.apply({
            "type": "change_makefile", "path": "Makefile",
            "key": "FOO", "value": "v3", "op": "set",
        })
        overlay_text = t.port_path("overlay.dops").read_text()
        # All three lines coexist; BAR is untouched by FOO emissions.
        assert 'mk set FOO "v1"' in overlay_text
        assert 'mk set BAR "v2"' in overlay_text
        assert 'mk set FOO "v3"' in overlay_text
        assert overlay_text.count("mk set FOO") == 2
        assert overlay_text.count("mk set BAR") == 1

    def test_change_makefile_append_op_still_accumulates(self, t):
        """`append` op (mk add) is a list op — repeated calls must
        keep all lines. Only `set` collapses."""
        t.apply({
            "type": "change_makefile", "path": "Makefile",
            "key": "USES", "value": "ssl", "op": "append",
        })
        t.apply({
            "type": "change_makefile", "path": "Makefile",
            "key": "USES", "value": "cmake", "op": "append",
        })
        overlay_text = t.port_path("overlay.dops").read_text()
        assert 'mk add USES "ssl"' in overlay_text
        assert 'mk add USES "cmake"' in overlay_text

    def test_change_makefile_set_op_does_not_touch_mk_target_set(self, t):
        """`mk target set NAME` (3 tokens before name) is a distinct
        directive from `mk set VAR` (2 tokens). A change_makefile
        ``op=set`` emission for VAR=FOO must not interfere with an
        existing `mk target set FOO <<TAG ... TAG` heredoc block —
        same key name, completely different op.

        Pre-38e this guarded the prefilter regex; post-38e there is no
        prefilter to confuse, but the same property must hold by
        construction (the renderer only emits its own statement, it
        doesn't inspect or rewrite existing lines)."""
        overlay = t.port_path("overlay.dops")
        overlay.parent.mkdir(parents=True, exist_ok=True)
        overlay.write_text(
            'target @main\nport cat/x\ntype port\nreason "x"\n'
            'mk target set FOO <<MK\nbody\nMK\n'
        )
        t.apply({
            "type": "change_makefile", "path": "Makefile",
            "key": "FOO", "value": "v1", "op": "set",
        })
        overlay_text = overlay.read_text()
        assert "mk target set FOO <<MK" in overlay_text
        assert 'mk set FOO "v1"' in overlay_text

    def test_replace_in_dops_block_edits_heredoc_body(self, t):
        """Step C-4: replace text inside an `mk target set <name>`
        heredoc body. The convert agent produces these for ports
        whose Makefile.DragonFly had a multi-line target recipe
        (e.g. archivers/liblz4 dfly-patch with sed commands)."""
        overlay = t.port_path("overlay.dops")
        overlay.write_text(
            "port devel/foo\ntype port\ntarget @main\n"
            'reason "test"\n\n'
            "mk target set dfly-patch <<'MK1'\n"
            "\t${REINPLACE_CMD} 's|GNU FreeBSD|GNU FreeBSD DragonFly|' \\\n"
            "\t\t${WRKSRC}/../../Makefile\n"
            "MK1\n"
        )
        result = t.apply({
            "type": "replace_in_dops_block",
            "block_name": "dfly-patch",
            "find": "${WRKSRC}/../../Makefile",
            "replace": "${WRKSRC}/Makefile",
        })
        assert result.ok is True, result.error
        new_text = overlay.read_text()
        assert "${WRKSRC}/Makefile" in new_text
        assert "${WRKSRC}/../../Makefile" not in new_text
        # Block structure preserved.
        assert "mk target set dfly-patch <<'MK1'" in new_text
        assert "\nMK1\n" in new_text

    def test_replace_in_dops_block_refuses_when_block_missing(self, t):
        overlay = t.port_path("overlay.dops")
        overlay.write_text(
            "port devel/foo\ntype port\ntarget @main\n"
            'reason "test"\n\n'
            "mk target set post-extract <<'MK'\n\techo x\nMK\n"
        )
        result = t.apply({
            "type": "replace_in_dops_block",
            "block_name": "nonexistent",
            "find": "x", "replace": "y",
        })
        assert result.ok is False
        assert "no `mk target" in result.error

    def test_replace_in_dops_block_refuses_when_find_absent(self, t):
        overlay = t.port_path("overlay.dops")
        overlay.write_text(
            "port devel/foo\ntype port\ntarget @main\n"
            'reason "test"\n\n'
            "mk target set foo <<'MK'\n\tline-a\n\tline-b\nMK\n"
        )
        result = t.apply({
            "type": "replace_in_dops_block",
            "block_name": "foo",
            "find": "line-c", "replace": "line-d",
        })
        assert result.ok is False
        assert "find string not present" in result.error

    def test_replace_in_dops_block_refuses_unbounded_block(self, t):
        """Overlay with `<<TAG` but no closing TAG line — refuse,
        don't blindly edit through the end of file."""
        overlay = t.port_path("overlay.dops")
        overlay.write_text(
            "port devel/foo\ntype port\ntarget @main\n"
            'reason "test"\n\n'
            "mk target set foo <<'MK'\n\tline-a\n"
            # no closing MK
        )
        result = t.apply({
            "type": "replace_in_dops_block",
            "block_name": "foo",
            "find": "line-a", "replace": "line-b",
        })
        assert result.ok is False
        assert "no closing line" in result.error

    def test_replace_in_dops_block_occurrence_picks_nth(self, t):
        overlay = t.port_path("overlay.dops")
        overlay.write_text(
            "port devel/foo\ntype port\ntarget @main\n"
            'reason "test"\n\n'
            "mk target set foo <<'MK'\n"
            "\tdupe-target dupe-target dupe-target\n"
            "MK\n"
        )
        result = t.apply({
            "type": "replace_in_dops_block",
            "block_name": "foo",
            "find": "dupe-target",
            "replace": "ZAP",
            "occurrence": 2,
        })
        assert result.ok is True, result.error
        body = overlay.read_text()
        # Second occurrence replaced; first and third intact.
        assert "dupe-target ZAP dupe-target" in body

    def test_replace_in_dops_block_does_not_touch_outside_block(self, t):
        """Critical safety: the find string also appears outside the
        target block; the renderer must only edit inside."""
        overlay = t.port_path("overlay.dops")
        overlay.write_text(
            "port devel/foo\ntype port\ntarget @main\n"
            'reason "test"\n\n'
            "patch apply dragonfly/some.c\n"
            "# OUTSIDE: ${WRKSRC}/../../Makefile mentioned here\n"
            "mk target set foo <<'MK'\n"
            "\tINSIDE: ${WRKSRC}/../../Makefile\n"
            "MK\n"
            "# AFTER: ${WRKSRC}/../../Makefile mentioned again\n"
        )
        t.apply({
            "type": "replace_in_dops_block",
            "block_name": "foo",
            "find": "${WRKSRC}/../../Makefile",
            "replace": "${WRKSRC}/Makefile",
        })
        new = overlay.read_text()
        # Inside replaced once.
        assert "INSIDE: ${WRKSRC}/Makefile" in new
        # The two outside-block mentions stay.
        assert new.count("${WRKSRC}/../../Makefile") == 2


    def test_replace_in_dops_block_refuses_noop_find_equals_replace(self, t):
        """Self-confirming no-op: agent emits find == replace and
        gets ok=True with empty diff, reading that as progress.
        Observed in archivers/liblz4 2026-05-26 thrash where the
        agent degraded its find/replace pair across attempts.
        Refuse with a message that tells the agent how to confirm
        prior intents landed without re-emitting."""
        overlay = t.port_path("overlay.dops")
        overlay.write_text(
            "port devel/foo\ntype port\ntarget @main\n"
            'reason "test"\n\n'
            "mk target set foo <<'MK'\n\tline-a\n\tline-b\nMK\n"
        )
        result = t.apply({
            "type": "replace_in_dops_block",
            "block_name": "foo",
            "find": "line-a", "replace": "line-a",
        })
        assert result.ok is False
        assert "no-op" in result.error
        assert "identical" in result.error

    def test_substrate_diff_is_per_intent_not_cumulative(self, t):
        """Each intent's substrate_diff must show only what THAT
        intent changed, not the cumulative working-tree state since
        HEAD. Prior git-diff implementation reported cumulative
        state, so after N intents on overlay.dops every entry's
        substrate_diff showed every prior change — making the
        intent log unreadable forensically and giving the agent
        false-progress signals (archivers/liblz4 2026-05-26)."""
        r1 = t.apply({
            "type": "change_makefile",
            "path": "Makefile.DragonFly",
            "key": "USES", "value": "ssl", "op": "append",
        })
        assert r1.ok is True
        r2 = t.apply({
            "type": "change_makefile",
            "path": "Makefile.DragonFly",
            "key": "USES", "value": "compiler:c++17-lang", "op": "append",
        })
        assert r2.ok is True
        # r2's diff mentions the ssl line only as context (or not at
        # all if difflib elides it); it must NOT show the ssl line
        # as a fresh `+` insertion. The `+` insertion in r2 is the
        # c++17-lang line, exclusively.
        added_in_r2 = [
            line for line in r2.substrate_diff.splitlines()
            if line.startswith("+") and not line.startswith("+++")
        ]
        assert any("c++17-lang" in line for line in added_in_r2), added_in_r2
        assert not any('"ssl"' in line for line in added_in_r2), added_in_r2

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

    def test_convert_to_dops_intent_no_longer_exists(self, tmp_path):
        """Post-Step-C cleanup: convert_to_dops was dead code (no
        production path constructed Translator(mode='convert')).
        The intent type, schema, renderer, and convert mode were
        all removed. parse_intent now refuses the wire-format
        type."""
        from dportsv3.agent.edit_intent import parse_intent
        with pytest.raises(IntentError, match="unknown"):
            parse_intent({"type": "convert_to_dops"})

    def test_invalid_mode_at_construction(self, tmp_path):
        ws = _make_workspace(tmp_path)
        # Only "dops" is valid post-Step-C.
        with pytest.raises(ValueError, match="invalid mode"):
            Translator(ws, "devel/foo", "bogus")  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="invalid mode"):
            Translator(ws, "devel/foo", "compat")  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="invalid mode"):
            Translator(ws, "devel/foo", "convert")  # type: ignore[arg-type]


# --------------------------------------------------------------------
# Path safety
# --------------------------------------------------------------------


class TestPathSafety:

    @pytest.fixture
    def t(self, tmp_path):
        ws = _make_workspace(tmp_path)
        return Translator(ws, "devel/foo", "dops")

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
                        mode_at_apply="dops", baseline_commit="abc")
        log.append({"type": "drop_patch", "target": "x", "reason": "y"},
                   ok=True, substrate_diff="diff")
        assert len(log.intents) == 1
        assert log.intents[0].seq == 0
        assert log.intents[0].ok is True

    def test_serialize_round_trips(self):
        log = IntentLog(origin="devel/foo", target="@main",
                        mode_at_apply="dops", baseline_commit="abc")
        log.append({"type": "drop_patch", "target": "x", "reason": "y"},
                   ok=True)
        doc = json.loads(log.to_json())
        assert doc["schema_version"] == 1
        assert doc["origin"] == "devel/foo"
        assert doc["mode_at_apply"] == "dops"
        assert len(doc["intents"]) == 1

    def test_count_cap_default_100(self):
        log = IntentLog(origin="devel/foo", target="@main",
                        mode_at_apply="dops", baseline_commit="abc")
        for i in range(100):
            log.append({"type": "drop_patch", "target": f"x{i}",
                        "reason": "y"}, ok=True)
        with pytest.raises(IntentError, match="exceeds 100 entries"):
            log.append({"type": "drop_patch", "target": "overflow",
                        "reason": "z"}, ok=True)

    def test_count_cap_env_override(self, monkeypatch):
        monkeypatch.setenv("DP_HARNESS_INTENT_MAX_COUNT", "3")
        log = IntentLog(origin="devel/foo", target="@main",
                        mode_at_apply="dops", baseline_commit="abc")
        for i in range(3):
            log.append({"type": "drop_patch", "target": f"x{i}",
                        "reason": "y"}, ok=True)
        with pytest.raises(IntentError, match="exceeds 3 entries"):
            log.append({"type": "drop_patch", "target": "y", "reason": "z"},
                       ok=True)

    def test_size_cap_rejects_huge_diff(self, monkeypatch):
        monkeypatch.setenv("DP_HARNESS_INTENT_MAX_BYTES", "1000")
        log = IntentLog(origin="devel/foo", target="@main",
                        mode_at_apply="dops", baseline_commit="abc")
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
        t = Translator(ws, "devel/foo", "dops")
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
        t = Translator(ws, "devel/foo", "dops")
        result = t.apply({
            "type": "add_patch",
            "target": "dragonfly/patch-missing.c",
            "from_dupe": True,
        })
        assert result.ok is False
        assert "no file matching basename" in result.error
