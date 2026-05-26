"""Contract tests for every dops-mode intent renderer.

The structural lesson from the 2026-05-26 liblz4 corruption:
``test_change_makefile_emits_mk_var_op`` asserted the exact
string ``mk.var.append`` appeared in the overlay — but never ran
the overlay through the engine parser. The renderer was emitting
invalid grammar; the test was self-confirming the bug.

This file exists to make that class of failure impossible: every
renderer's output is appended to a minimal valid overlay and the
combined text is parsed through ``engine.parse_dsl``. If the
parser rejects, the test fails — independent of what string the
renderer happens to emit.

When you add a new intent type or modify an existing dops
renderer, add or extend the case here. The maintenance cost is
one assertion per intent type; the value is that renderer bugs
can't ship invisibly anymore.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from dportsv3.agent.edit_intent.translator import Translator
from dportsv3.engine.api import parse_dsl


def _make_workspace(tmp_path: Path, origin: str = "devel/foo") -> Path:
    """Tiny git-backed workspace mirroring real DeltaPorts layout."""
    ws = tmp_path / "DeltaPorts"
    port = ws / "ports" / origin
    port.mkdir(parents=True)
    subprocess.run(["git", "-C", str(ws), "init", "-q", "-b", "main"],
                   check=True)
    subprocess.run(["git", "-C", str(ws), "config", "user.email", "t@t"],
                   check=True)
    subprocess.run(["git", "-C", str(ws), "config", "user.name", "t"],
                   check=True)
    (ws / "README").write_text("baseline\n")
    subprocess.run(["git", "-C", str(ws), "add", "README"], check=True)
    subprocess.run(["git", "-C", str(ws), "commit", "-qm", "init"],
                   check=True)
    return ws


_MIN_HEADER = (
    "port devel/foo\n"
    "type port\n"
    "target @any\n"
    'reason "contract test"\n'
    "\n"
)


@pytest.fixture
def t(tmp_path):
    ws = _make_workspace(tmp_path)
    # Seed the port with a minimal valid overlay header so the
    # renderers have something to append to.
    overlay = ws / "ports" / "devel" / "foo" / "overlay.dops"
    overlay.write_text(_MIN_HEADER)
    return Translator(ws, "devel/foo", "dops")


def _parse_overlay(t) -> None:
    """Parse the current overlay.dops via engine.parse_dsl; assert ok.

    Surfaces parser diagnostics on failure so a renderer regression
    points the reader directly at the bad statement.
    """
    overlay_text = t.port_path("overlay.dops").read_text()
    result = parse_dsl(overlay_text)
    assert result.ok, (
        f"renderer output failed engine parse:\n"
        f"--- overlay.dops ---\n{overlay_text}\n"
        f"--- diagnostics ---\n"
        + "\n".join(f"  {d.code}: {d.message}" for d in result.diagnostics)
    )


# --------------------------------------------------------------------
# Per-intent contract tests
# --------------------------------------------------------------------


class TestDopsRendererContract:

    def test_change_makefile_set_parses(self, t):
        t.apply({
            "type": "change_makefile",
            "path": "Makefile.DragonFly",
            "key": "DFLY_PATCH", "value": "build/foo", "op": "set",
        })
        _parse_overlay(t)

    def test_change_makefile_append_parses(self, t):
        """`append` op maps to dops `add` action; verify the parser
        accepts it. This is the case that historically broke."""
        t.apply({
            "type": "change_makefile",
            "path": "Makefile.DragonFly",
            "key": "USES", "value": "ssl", "op": "append",
        })
        _parse_overlay(t)

    def test_change_makefile_remove_parses(self, t):
        t.apply({
            "type": "change_makefile",
            "path": "Makefile.DragonFly",
            "key": "USES", "value": "ssl", "op": "remove",
        })
        _parse_overlay(t)

    def test_change_makefile_value_with_quotes_parses(self, t):
        """Escapes survive the round-trip through the lexer."""
        t.apply({
            "type": "change_makefile",
            "path": "Makefile.DragonFly",
            "key": "CFG", "value": 'has "quote" inside', "op": "set",
        })
        _parse_overlay(t)

    def test_change_makefile_value_with_backslash_parses(self, t):
        t.apply({
            "type": "change_makefile",
            "path": "Makefile.DragonFly",
            "key": "CFG", "value": "a\\b", "op": "set",
        })
        _parse_overlay(t)

    def test_bump_portrevision_parses(self, t):
        t.apply({"type": "bump_portrevision", "port": "devel/foo"})
        _parse_overlay(t)

    def test_drop_patch_parses(self, t):
        """drop_patch removes a `patch apply` line; the remaining
        overlay must still parse cleanly."""
        # Seed an overlay with a patch apply line so drop has
        # something to remove.
        overlay = t.port_path("overlay.dops")
        overlay.write_text(
            _MIN_HEADER + "patch apply dragonfly/patch-old.c\n"
        )
        t.apply({
            "type": "drop_patch",
            "target": "dragonfly/patch-old.c", "reason": "x",
        })
        _parse_overlay(t)

    def test_add_patch_parses(self, t):
        t.apply({
            "type": "add_patch",
            "target": "dragonfly/patch-new.c",
            "diff": "--- a\n+++ b\n@@ -1 +1 @@\n-x\n+y\n",
        })
        _parse_overlay(t)

    def test_add_file_resource_parses(self, t):
        t.apply({
            "type": "add_file",
            "dest": "files/post-install.sh", "kind": "resource",
            "content": "#!/bin/sh\nexit 0\n",
        })
        _parse_overlay(t)

    def test_add_file_materialize_parses(self, t):
        t.apply({
            "type": "add_file",
            "dest": "dragonfly/patch-from-source.c",
            "kind": "materialize",
            "source": "src/utils/foo.c",
        })
        _parse_overlay(t)

    def test_replace_in_dops_block_parses(self, t):
        """C-4 intent: edits inside `mk target set` heredoc body
        must leave the overlay still parseable by the engine."""
        overlay = t.port_path("overlay.dops")
        overlay.write_text(
            _MIN_HEADER
            + "mk target set dfly-patch <<'MK'\n"
            "\t${REINPLACE_CMD} 's|A|B|' ${WRKSRC}/../../Makefile\n"
            "MK\n"
        )
        t.apply({
            "type": "replace_in_dops_block",
            "block_name": "dfly-patch",
            "find": "${WRKSRC}/../../Makefile",
            "replace": "${WRKSRC}/Makefile",
        })
        _parse_overlay(t)

    def test_replace_in_patch_parses(self, t):
        """replace_in_patch in dops mode appends a `text replace-once`
        directive (deferred at compose time). The directive itself
        must parse."""
        t.apply({
            "type": "replace_in_patch",
            "target": "dragonfly/patch-foo.c",
            "find": "OLD", "replace": "NEW",
        })
        _parse_overlay(t)


# --------------------------------------------------------------------
# Sanity: an intentionally-broken statement makes the parser fail.
# Without this, a future "always ok" parser regression would silently
# turn every contract assertion into a no-op.
# --------------------------------------------------------------------


def test_contract_test_actually_fails_on_invalid_grammar(tmp_path):
    ws = _make_workspace(tmp_path)
    overlay = ws / "ports" / "devel" / "foo" / "overlay.dops"
    # The exact broken form `change_makefile` used to emit.
    overlay.write_text(_MIN_HEADER + "mk.var.set var=USES value=ssl\n")
    result = parse_dsl(overlay.read_text())
    assert not result.ok
