"""Tests for the Step 47 Phase 2 ``Makefile.diff`` → ``mk`` translator
(``dportsv3.migration.absorb_makefile``).

Covers the pure hunk-classification + op-emission logic and the
content-exact (whitespace) normalizer. The compose-parity gate
(``absorb_makefile_gated``) is exercised against real ports in the
migration run, not here.
"""

from __future__ import annotations

from pathlib import Path

from dportsv3.migration.absorb_makefile import (
    absorb_makefile,
    hunk_to_mk_ops,
    parse_hunks,
)
from dportsv3.migration.parity import makefile_whitespace_normalizer as wsnorm


def _hunks(diff: str):
    return parse_hunks(diff)


def test_token_add_emits_mk_add():
    diff = "--- Makefile.orig\n+++ Makefile\n@@ -1,1 +1,1 @@\n-USE_GNOME=\tgtk20\n+USE_GNOME=\tgtk20 gdkpixbufextra\n"
    ops = hunk_to_mk_ops(_hunks(diff)[0])
    assert ops == ["mk add USE_GNOME gdkpixbufextra"]


def test_trailing_token_remove_emits_mk_remove():
    # removal of a trailing token → mk remove
    diff = "--- a\n+++ b\n@@ -1,1 +1,1 @@\n-USE_PYTHON=\tpy3kplist optsuffix\n+USE_PYTHON=\tpy3kplist\n"
    ops = hunk_to_mk_ops(_hunks(diff)[0])
    assert ops == ["mk remove USE_PYTHON optsuffix"]


def test_nonsuffix_token_change_falls_back_to_mk_set():
    # leading/middle token removal isn't a clean suffix-diff → mk set
    # (still correct; the self-check verifies content-exact)
    diff = "--- a\n+++ b\n@@ -1,1 +1,1 @@\n-USE_PYTHON=\toptsuffix py3kplist\n+USE_PYTHON=\tpy3kplist\n"
    ops = hunk_to_mk_ops(_hunks(diff)[0])
    assert ops == ['mk set USE_PYTHON "py3kplist"']


def test_value_change_emits_mk_set():
    diff = "--- a\n+++ b\n@@ -1,1 +1,1 @@\n-PORTEPOCH=\t1\n+PORTEPOCH=\t2\n"
    ops = hunk_to_mk_ops(_hunks(diff)[0])
    assert ops == ['mk set PORTEPOCH "2"']


def test_pure_insertion_escalates():
    # no removed line → placement risk → not deterministically handled
    diff = "--- a\n+++ b\n@@ -0,0 +1 @@\n+BROKEN=\tuses pulseaudio\n"
    assert hunk_to_mk_ops(_hunks(diff)[0]) is None


def test_recipe_hunk_escalates():
    diff = (
        "--- a\n+++ b\n@@ -1,3 +1,3 @@\n do-configure:\n"
        "-\t@cd ${WRKDIR}; ./configure\n+\t@cd ${WRKDIR}; ${SETENV} ./configure\n"
    )
    assert hunk_to_mk_ops(_hunks(diff)[0]) is None


def test_conditional_hunk_escalates():
    diff = "--- a\n+++ b\n@@ -1,1 +1,1 @@\n-.if !exists(/usr/include/jail.h)\n+.if !exists(/usr/include/sys/jail.h)\n"
    assert hunk_to_mk_ops(_hunks(diff)[0]) is None


def test_operator_change_escalates():
    # = vs += is a semantic change the deterministic path won't guess
    diff = "--- a\n+++ b\n@@ -1,1 +1,1 @@\n-CFLAGS=\t-O2\n+CFLAGS+=\t-O2\n"
    assert hunk_to_mk_ops(_hunks(diff)[0]) is None


def test_parse_hunks_skips_context_only():
    diff = "--- a\n+++ b\n@@ -1,2 +1,2 @@\n CONTEXT1\n CONTEXT2\n"
    assert parse_hunks(diff) == []  # no +/- lines → dropped


def test_whitespace_normalizer_collapses_only_makefile():
    assert wsnorm("Makefile", "A=\tx  y\n") == "A= x y"
    # non-Makefile passes through untouched
    assert wsnorm("pkg-plist", "a\t b\n") == "a\t b\n"


def test_escalation_emits_deferred_patch_and_does_not_mutate(tmp_path: Path):
    port = tmp_path / "ports" / "ports-mgmt" / "thing"
    (port / "diffs").mkdir(parents=True)
    # a recipe hunk → non-deterministic → escalate
    diff = (
        "--- Makefile.orig\n+++ Makefile\n@@ -1,2 +1,2 @@\n do-configure:\n"
        "-\t./configure\n+\t${SETENV} ./configure\n"
    )
    (port / "diffs" / "Makefile.diff").write_text(diff)
    upstream = tmp_path / "up" / "Makefile"
    upstream.parent.mkdir(parents=True)
    upstream.write_text("do-configure:\n\t./configure\n")

    result = absorb_makefile(port, origin="ports-mgmt/thing", upstream_makefile=upstream)

    assert result.escalated and result.ok
    dp = result.deferred
    assert dp is not None
    assert dp.path == "diffs/Makefile.diff"
    assert dp.target_file == "Makefile"
    assert dp.backing_file == "diffs/Makefile.diff"
    assert dp.original_content == diff
    # escalation must not mutate the port
    assert (port / "diffs" / "Makefile.diff").is_file()
    assert not (port / "overlay.dops").exists()
