"""Byte-parity test for build_patch_payload across the Phase-4 cutover.

Phase 4 Step 3. Same pattern as the triage parity test: synthetic
bundle fixtures, call ``build_patch_payload``, assert against
hand-derived expected strings that pin the legacy
``parts.append(...)`` semantics.

Fixture variants:
- minimal: build errors + Makefile + Automation Context (always
  renders).
- with_triage: minimal + analysis/triage.md so the Triage Summary
  appears.
- with_siblings: minimal + a sibling bundle (no intro paragraph,
  unlike the triage payload).
- with_prior_attempts: minimal + 3 historical patch bundles.
- snippet_round: a follow-up round with snippet feedback + content.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dportsv3.agent import runner


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def _minimal_bundle(tmp_path: Path) -> Path:
    bdir = tmp_path / "bundle-min"
    _write(bdir / "logs" / "errors.txt", "synthetic compile error\n")
    _write(bdir / "port" / "Makefile",
           "PORTNAME=foo\nPORTVERSION=1.0\n")
    return bdir


@pytest.fixture(autouse=True)
def _no_artifact_store(monkeypatch):
    """Same stubs as the triage parity test."""
    monkeypatch.setattr(runner, "artifact_store_get", lambda *a, **kw: None)
    monkeypatch.setattr(runner, "tracker_artifact_get", lambda *a, **kw: None)
    monkeypatch.setattr(runner, "bundle_artifact_list", lambda *a, **kw: [])
    monkeypatch.setattr(runner, "port_bundle_history", lambda *a, **kw: [])
    monkeypatch.setattr(runner, "load_kedb", lambda *a, **kw: "")
    monkeypatch.setattr(runner, "get_user_context", lambda *a, **kw: (None, 0))
    # Default: no recent failures (sections that ask see 0).
    from dportsv3.agent.decision import PortHistory
    monkeypatch.setattr(
        runner, "_load_port_history",
        lambda target, origin, window_hours: PortHistory.empty(target, origin),
    )


# --- minimal -----------------------------------------------------------------


def test_minimal_patch_parity(tmp_path, monkeypatch):
    """The minimal patch payload: Automation Context + Build Errors +
    Port Files header + Makefile + the multi-line patch footer."""
    bdir = _minimal_bundle(tmp_path)
    monkeypatch.setenv("DP_HARNESS_ATTEMPT_WINDOW_HOURS", "2")
    monkeypatch.setenv("DP_HARNESS_MAX_PATCH_ATTEMPTS", "3")
    job = {
        "origin": "devel/foo",
        "iteration": "1",
        "max_iterations": "3",
        "tier": "ASSIST",
        "snippet_round": "0",
        "has_snippets": "false",
    }

    automation_body = (
        "- You are the patch agent in an automated DragonFly ports fix loop.\n"
        "- This is iteration 1/3 for this patch job (tier=ASSIST).\n"
        "- The same origin has produced 0 failure bundle(s) "
        "in the last 2 hour(s); the runner caps at "
        "3 before forcing MANUAL.\n"
        "- Your goal: either make dsynth_build report rebuild_ok=true, or "
        "emit your best proposed fix with `Rebuild Status: gave-up` and a "
        "concrete next-step recommendation in Patch Log. Either is a valid "
        "outcome — burning the budget without trying anything is not.\n"
        "- The Triage Summary below contains a `Suggested Fix` section. "
        "**Apply it first.** Only explore further if the suggested fix has "
        "already been tried (check Prior Attempts) or doesn't work."
    )

    expected = (
        "## Automation Context\n"
        f"{automation_body}\n\n"
        "## Build Errors\n"
        "synthetic compile error\n\n"
        "\n"
        "## Port Files\n"
        "### Makefile\n"
        "```makefile\n"
        "PORTNAME=foo\nPORTVERSION=1.0\n\n"
        "```\n\n"
        "---\n"
        "Use the dports tools to apply fixes in the shared workspace and rebuild the target origin.\n"
        "Return a report with these exact sections:\n"
        "- ## Patch Log\n"
        "- ## Rebuild Status\n"
        "- ## Patch Plan (JSON) with a ```json block\n"
        "- ## Rebuild Proof (JSON) with a ```json block"
    )

    actual = runner.build_patch_payload(bdir, None, job)
    assert actual == expected, (
        "byte-parity mismatch for minimal patch.\n"
        f"--- expected ({len(expected)} bytes) ---\n{expected!r}\n"
        f"--- actual   ({len(actual)} bytes) ---\n{actual!r}"
    )


# --- with triage summary -----------------------------------------------------


def test_with_triage_summary(tmp_path):
    bdir = _minimal_bundle(tmp_path)
    _write(bdir / "analysis" / "triage.md",
           "## Classification\nplist-error\n\n## Confidence\nhigh\n")
    job = {
        "origin": "devel/foo",
        "iteration": "1",
        "max_iterations": "3",
        "tier": "AUTO",
        "snippet_round": "0",
        "has_snippets": "false",
    }
    actual = runner.build_patch_payload(bdir, None, job)
    assert "## Triage Summary" in actual
    assert "plist-error" in actual
    assert actual.index("## Triage Summary") < actual.index("## Build Errors")


# --- with siblings (no intro paragraph) --------------------------------------


def test_patch_siblings_no_intro(tmp_path, monkeypatch):
    """The patch payload's sibling section must NOT include the
    triage intro paragraph — that's the parameterized difference."""
    bdir = _minimal_bundle(tmp_path)

    def fake_read(bd, bid, relpath):
        if bd is not None:
            p = bd / relpath
            return p.read_text() if p.exists() else None
        if bid == "sib-a":
            return "sibling boom\n"
        return None
    monkeypatch.setattr(runner, "read_bundle_text", fake_read)

    job = {
        "origin": "devel/foo",
        "iteration": "1",
        "max_iterations": "3",
        "tier": "ASSIST",
        "snippet_round": "0",
        "has_snippets": "false",
        "sibling_bundle_ids": "sib-a",
    }
    actual = runner.build_patch_payload(bdir, None, job)

    assert "## Sibling Pending Failures (this batch)" in actual
    # Crucial: the triage-version intro line must NOT appear in patch.
    assert "These bundles failed for the same origin and were queued" not in actual
    assert "### Bundle sib-a" in actual
    assert "sibling boom" in actual


# --- with prior attempts -----------------------------------------------------


def test_with_prior_attempts(tmp_path, monkeypatch):
    bdir = _minimal_bundle(tmp_path)

    def fake_history(origin):
        return [
            {"bundle_id": "past-a"},
            {"bundle_id": "past-b"},
            {"bundle_id": "past-c"},
        ]
    monkeypatch.setattr(runner, "port_bundle_history", fake_history)

    def fake_read(bd, bid, relpath):
        if bd is not None:
            p = bd / relpath
            return p.read_text() if p.exists() else None
        if bid == "past-a" and relpath == "analysis/patch_plan.json":
            return '{"summary": "tried X"}\n'
        if bid == "past-a" and relpath == "analysis/patch.log":
            return "X failed because Y\n"
        if bid == "past-a" and relpath == "analysis/rebuild_status.txt":
            return "rebuild_ok=false\n"
        return None
    monkeypatch.setattr(runner, "read_bundle_text", fake_read)

    job = {
        "origin": "devel/foo",
        "iteration": "2",
        "max_iterations": "3",
        "tier": "ASSIST",
        "snippet_round": "0",
        "has_snippets": "false",
    }
    actual = runner.build_patch_payload(bdir, None, job)

    assert "## Prior Attempts (most recent 3)" in actual
    assert "### Bundle past-a" in actual
    assert "#### Patch Plan" in actual
    assert "```json" in actual
    assert "#### Patch Log" in actual
    assert "X failed because Y" in actual
    assert "#### Rebuild Status" in actual


# --- snippet round -----------------------------------------------------------


def test_snippet_round_includes_feedback_and_content(tmp_path, monkeypatch):
    bdir = _minimal_bundle(tmp_path)

    monkeypatch.setattr(
        runner, "build_snippet_feedback",
        lambda bundle_dir, round_num: "## Snippet Feedback\n(patch synthetic)",
    )
    monkeypatch.setattr(
        runner, "load_snippets_content",
        lambda bundle_dir, round_num: "## Extracted Snippets\n(patch synthetic)",
    )

    job = {
        "origin": "devel/foo",
        "iteration": "1",
        "max_iterations": "3",
        "tier": "AUTO",
        "snippet_round": "1",
        "has_snippets": "true",
    }
    actual = runner.build_patch_payload(bdir, None, job)
    assert "## Snippet Feedback" in actual
    assert "## Extracted Snippets" in actual
    # Snippet sections come before Automation Context.
    assert actual.index("## Snippet Feedback") < actual.index("## Automation Context")


def test_patch_footer_format(tmp_path):
    """The patch footer is multi-line and bullet-style; lock the
    exact text in case anyone tries to "simplify" it later."""
    bdir = _minimal_bundle(tmp_path)
    job = {"origin": "devel/foo", "snippet_round": "0", "has_snippets": "false"}
    actual = runner.build_patch_payload(bdir, None, job)
    assert actual.endswith(
        "---\n"
        "Use the dports tools to apply fixes in the shared workspace and rebuild the target origin.\n"
        "Return a report with these exact sections:\n"
        "- ## Patch Log\n"
        "- ## Rebuild Status\n"
        "- ## Patch Plan (JSON) with a ```json block\n"
        "- ## Rebuild Proof (JSON) with a ```json block"
    )
