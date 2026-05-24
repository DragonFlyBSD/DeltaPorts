"""Tests for the agent's port-scoped dops classifier (Step 20a).

Builds tiny fake port trees on disk and asserts the agent-facing
state vocabulary matches the underlying ``migration.classify``
buckets correctly.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dportsv3.agent.dops import assess, classify


def _make_port(root: Path, origin: str) -> Path:
    port_dir = root / "ports" / origin
    port_dir.mkdir(parents=True)
    # Every port needs a Makefile or the migration record-building
    # logic doesn't matter — but inventory only checks for overlay
    # artifacts, not the port Makefile, so we don't need one.
    return port_dir


def test_classify_not_in_scope_when_no_overlay(tmp_path: Path) -> None:
    """Port with no overlay artifacts at all isn't in conversion
    scope. The agent should do nothing for these."""
    _make_port(tmp_path, "devel/plain")
    assert classify("devel/plain", tmp_path) == "not_in_scope"


def test_classify_not_in_scope_when_port_missing(tmp_path: Path) -> None:
    """Bogus origin → ``not_in_scope``, never an exception."""
    assert classify("category/does-not-exist", tmp_path) == "not_in_scope"


def test_classify_converted_when_only_dops(tmp_path: Path) -> None:
    """``overlay.dops`` present, no legacy artifacts → converted."""
    port = _make_port(tmp_path, "devel/already-dops")
    (port / "overlay.dops").write_text(
        'target @main\nport devel/already-dops\ntype port\nreason "test"\n'
    )
    assert classify("devel/already-dops", tmp_path) == "converted"


def test_classify_auto_safe_pending_with_simple_makefile(tmp_path: Path) -> None:
    """Plain ``Makefile.DragonFly`` (only assignments) → the
    deterministic converter can handle it. Agent flags
    ``auto_safe_pending`` and does not need to run."""
    port = _make_port(tmp_path, "devel/auto-safe")
    (port / "Makefile.DragonFly").write_text(
        "USES+=pkgconfig\nCONFIGURE_ARGS+=--with-foo\n"
    )
    assert classify("devel/auto-safe", tmp_path) == "auto_safe_pending"


def test_classify_needs_judgment_with_conditional_block(tmp_path: Path) -> None:
    """``Makefile.DragonFly`` with a ``.if`` conditional → the
    deterministic converter bails, agent needs to weigh in."""
    port = _make_port(tmp_path, "devel/with-conditional")
    (port / "Makefile.DragonFly").write_text(
        ".if ${OPSYS} == DragonFly\n"
        "USES+=pkgconfig\n"
        ".endif\n"
    )
    assert classify("devel/with-conditional", tmp_path) == "needs_judgment"


def test_classify_needs_judgment_with_dragonfly_patches(tmp_path: Path) -> None:
    """Modern DeltaPorts layout: static patches live under
    ``ports/<origin>/dragonfly/patch-*``. The classifier must
    detect them — earlier versions missed this directory entirely
    because the underlying migration inventory was written for the
    older legacy-program layout."""
    port = _make_port(tmp_path, "devel/libuv-like")
    dragonfly = port / "dragonfly"
    dragonfly.mkdir()
    (dragonfly / "patch-Makefile.in").write_text(
        "--- a/Makefile.in\n+++ b/Makefile.in\n@@ -1 +1 @@\n-old\n+new\n"
    )
    assert classify("devel/libuv-like", tmp_path) == "needs_judgment"


def test_classify_converted_when_dops_plus_dragonfly_peers(tmp_path: Path) -> None:
    """``dragonfly/*`` patch files ALONGSIDE ``overlay.dops`` is the
    end state for ports that retain upstream-source patches — the
    dops references them via ``file materialize``. These files
    are NOT a migration debt; classify must return ``converted``.

    (Regression test for the convert→resume loop we hit on libuv:
    every triage→convert→resume cycle re-fired because the
    classifier mistakenly treated dragonfly/ as legacy
    artifacts even when overlay.dops was already in place.)"""
    port = _make_port(tmp_path, "devel/dops-with-dragonfly")
    (port / "overlay.dops").write_text(
        'target @main\nport devel/dops-with-dragonfly\ntype port\nreason "x"\n'
        'file materialize dragonfly/patch-Makefile -> dragonfly/patch-Makefile\n'
    )
    dragonfly = port / "dragonfly"
    dragonfly.mkdir()
    (dragonfly / "patch-Makefile").write_text("--- a/M\n+++ b/M\n")
    assert classify("devel/dops-with-dragonfly", tmp_path) == "converted"


def test_classify_needs_judgment_when_dops_plus_makefile_dragonfly(
    tmp_path: Path,
) -> None:
    """``Makefile.DragonFly`` alongside ``overlay.dops`` IS a
    migration debt — those framework changes should be expressed
    as ``mk`` ops in the dops. The agent should still finish the
    migration."""
    port = _make_port(tmp_path, "devel/half-migrated-mkfile")
    (port / "overlay.dops").write_text(
        'target @main\nport devel/half-migrated-mkfile\ntype port\nreason "x"\n'
    )
    (port / "Makefile.DragonFly").write_text("USES+=pkgconfig\n")
    state = classify("devel/half-migrated-mkfile", tmp_path)
    assert state != "converted"
    assert state in ("auto_safe_pending", "needs_judgment")
    assessment = assess("devel/half-migrated-mkfile", tmp_path)
    assert assessment.action == "surface_invariant"
    assert "dops_with_unmigrated_makefile_dragonfly" in assessment.invariant_violations
    assert "Makefile.DragonFly" in assessment.unmigrated_artifacts


def test_classify_in_scope_with_targeted_makefile_dragonfly(tmp_path: Path) -> None:
    """``Makefile.DragonFly.@2026Q2`` and similar per-target
    variants are in scope too — they're the target-specific framework
    layer."""
    port = _make_port(tmp_path, "devel/targeted")
    (port / "Makefile.DragonFly.@2026Q2").write_text("USES+=pkgconfig\n")
    state = classify("devel/targeted", tmp_path)
    assert state in ("auto_safe_pending", "needs_judgment")


def test_classify_needs_judgment_with_raw_diffs(tmp_path: Path) -> None:
    """``diffs/`` directory → fallback-only bucket → agent must
    classify each diff as framework vs source vs complex."""
    port = _make_port(tmp_path, "devel/with-diffs")
    diffs = port / "diffs"
    diffs.mkdir()
    # Diffs are detected by suffix (.diff/.patch) to match
    # compose_discovery's per-port logic.
    (diffs / "patch-foo.c.diff").write_text(
        "--- a/foo.c\n+++ b/foo.c\n@@ -1 +1 @@\n-old\n+new\n"
    )
    assert classify("devel/with-diffs", tmp_path) == "needs_judgment"


def test_classify_needs_judgment_with_newport(tmp_path: Path) -> None:
    """``newport/`` directory → review-needed bucket."""
    port = _make_port(tmp_path, "devel/with-newport")
    newport = port / "newport"
    newport.mkdir()
    (newport / "Makefile").write_text("PORTNAME=foo\n")
    assert classify("devel/with-newport", tmp_path) == "needs_judgment"


def test_assess_converted_when_dops_plus_diffs_peers(tmp_path: Path) -> None:
    """diffs/*.diff can be referenced by dops and is allowed beside it."""
    port = _make_port(tmp_path, "devel/dops-with-diffs")
    (port / "overlay.dops").write_text(
        'target @main\nport devel/dops-with-diffs\ntype port\nreason "x"\n'
        'patch apply diffs/fix.diff\n'
    )
    diffs = port / "diffs"
    diffs.mkdir()
    (diffs / "fix.diff").write_text("--- a/x\n+++ b/x\n")

    assessment = assess("devel/dops-with-diffs", tmp_path)
    assert assessment.state == "converted"
    assert assessment.action == "proceed_triage"
    assert "diffs_allowed_with_dops" in [r.code for r in assessment.rules]


def test_classify_needs_judgment_when_dops_and_legacy_coexist(tmp_path: Path) -> None:
    """A port partway through migration (``overlay.dops`` AND
    ``Makefile.DragonFly`` both present) is not done — the agent
    should finish the job, not declare it converted."""
    port = _make_port(tmp_path, "devel/half-migrated")
    (port / "overlay.dops").write_text(
        'target @main\nport devel/half-migrated\ntype port\nreason "test"\n'
    )
    (port / "Makefile.DragonFly").write_text("USES+=pkgconfig\n")
    state = classify("devel/half-migrated", tmp_path)
    # Either ``auto_safe_pending`` (if classifier picks that) or
    # ``needs_judgment`` is acceptable — what matters is it's NOT
    # ``converted``, because the legacy file still has to be
    # removed.
    assert state != "converted"
    assert state in ("auto_safe_pending", "needs_judgment")


@pytest.mark.parametrize(
    "state",
    ["converted", "auto_safe_pending", "needs_judgment", "stale", "not_in_scope"],
)
def test_classification_states_constant_is_complete(state: str) -> None:
    """Guard against drift between the documented state set and the
    constant the rest of the agent imports."""
    from dportsv3.agent.dops import CLASSIFICATION_STATES
    assert state in CLASSIFICATION_STATES
