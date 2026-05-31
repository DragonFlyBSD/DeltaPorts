"""Step 36-6: convert payload renders triage context when present.

Closes the python311 class — convert was running blind to the
actual build failure (substrate-only classifier in the payload) and
producing speculative overlays against layers it can't fix (e.g.
plist drift). With the typed ``TriageResult`` plumbed in,
``build_convert_payload`` surfaces classification, confidence, root
cause, and evidence so the agent can either keep the overlay
minimal or escalate when the failure is out of scope for substrate
conversion.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dportsv3.agent.convert import build_convert_payload
from dportsv3.agent.phase_result import TriageResult


def _make_repo(tmp_path: Path, origin: str = "lang/python311") -> Path:
    repo = tmp_path / "repo"
    port_dir = repo / "ports" / origin
    port_dir.mkdir(parents=True)
    (port_dir / "Makefile.DragonFly").write_text(
        "OPTIONS_EXCLUDE+= LTO\n"
    )
    return repo


def _baseline_kwargs(repo: Path, origin: str) -> dict:
    return dict(
        origin=origin,
        repo_root=repo,
        classified_record={"bucket": "needs-judgment",
                           "classification_reasons": []},
        deterministic_result={"status": "blocked", "parse_ok": False,
                              "check_ok": False, "plan_ok": False,
                              "deterministic_ok": False},
        dops_quickref_text="(quickref)",
        playbooks_text="",
    )


def test_payload_omits_section_without_triage_result(tmp_path):
    """Operator-fired convert / deterministic-only path passes no
    triage_result; the rendered payload must not include the
    "Original build failure" section."""
    repo = _make_repo(tmp_path)
    payload = build_convert_payload(**_baseline_kwargs(repo, "lang/python311"))
    assert "## Original build failure (from triage)" not in payload


def test_payload_renders_section_with_triage_result(tmp_path):
    """When triage_result is present, the agent sees classification,
    confidence, root cause, and evidence — the python311 fix."""
    repo = _make_repo(tmp_path)
    triage = TriageResult(
        classification="plist-error",
        confidence="high",
        root_cause=(
            "pkg-static fails because pkg-plist references "
            "`_sysconfigdata__freebsd99_*` and unexpanded "
            "`%%PYTHON_EXT_SUFFIX%%` placeholders."
        ),
        evidence_excerpt=(
            "pkg-static: Unable to access file ..."
        ),
        error_signature="abc1234567890def",
        tier="MANUAL",
        classifier_version="triage-v1",
        tokens_prompt=2_500, tokens_completion=700, tokens_total=3_200,
        model="anthropic/claude-sonnet-4",
    )
    payload = build_convert_payload(
        triage_result=triage,
        **_baseline_kwargs(repo, "lang/python311"),
    )
    assert "## Original build failure (from triage)" in payload
    assert "Classification: `plist-error`" in payload
    assert "Confidence: `high`" in payload
    assert "**Root cause:**" in payload
    assert "PYTHON_EXT_SUFFIX" in payload
    assert "**Evidence excerpt:**" in payload
    assert "pkg-static" in payload
    # Steer text that warns against speculative overlays on
    # unrelated layers — the actual point of plumbing this through.
    assert "minimal" in payload.lower()
    # Section is rendered before the deterministic-translator status
    # so the agent reads root-cause context first.
    assert payload.index("## Original build failure (from triage)") < (
        payload.index("## Deterministic translator status")
    )


def test_payload_handles_triage_with_empty_root_cause(tmp_path):
    """A triage result with missing root_cause / evidence still
    renders the header lines but skips the empty sub-sections —
    doesn't emit blank "**Root cause:**" labels."""
    repo = _make_repo(tmp_path)
    triage = TriageResult(
        classification="patch-error",
        confidence="medium",
        root_cause="",
        evidence_excerpt="",
        error_signature=None,
        tier="ASSIST", classifier_version="triage-v1",
        tokens_prompt=0, tokens_completion=0, tokens_total=0,
        model="m",
    )
    payload = build_convert_payload(
        triage_result=triage,
        **_baseline_kwargs(repo, "lang/python311"),
    )
    assert "## Original build failure (from triage)" in payload
    assert "Classification: `patch-error`" in payload
    assert "**Root cause:**" not in payload
    assert "**Evidence excerpt:**" not in payload
