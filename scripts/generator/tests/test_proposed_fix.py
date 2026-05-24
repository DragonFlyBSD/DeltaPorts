"""Tests for dportsv3.agent.proposed_fix (Step 11a).

Covers:
- ``render_proposed_fix`` produces a markdown artifact with all
  the operator-facing sections (Summary, Bundle, Cost, Files,
  Apply, Verify, Audit).
- ``build_proposed_fix_ctx`` reads bundle artifacts via the
  injected ``read_bundle_text`` callable.
- ``patch_result`` wins over ``patch_audit.json`` when both are
  available (same precedence rule as manual_handoff).
- Triage classification/confidence backfill from triage.md when
  caller doesn't supply them.
- Default artifact priority surfaces proposed_fix.md ahead of
  the agent's raw outputs on the bundle detail page.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest


_GEN = Path(__file__).resolve().parents[1]
if str(_GEN) not in sys.path:
    sys.path.insert(0, str(_GEN))


from dportsv3.agent import proposed_fix as pf  # noqa: E402


def _ctx(**kwargs) -> pf.ProposedFixCtx:
    base = dict(
        origin="devel/foo",
        target="@2026Q2",
        bundle_id="b-1",
        model="deepseek/deepseek-v4-pro",
    )
    base.update(kwargs)
    return pf.ProposedFixCtx(**base)


def _read_from_dict(artifacts: dict[str, str]):
    def reader(bundle_dir, bundle_id, relpath):
        return artifacts.get(relpath)
    return reader


# --- render: structure -----------------------------------------------------


def test_render_headline_includes_origin():
    out = pf.render_proposed_fix(_ctx())
    assert "# Proposed Fix — `devel/foo`" in out


def test_render_includes_all_canonical_sections():
    out = pf.render_proposed_fix(_ctx(
        summary="Converted the stale patch to a dops text replace-once.",
        files_touched=["ports/devel/foo/overlay.dops",
                       "ports/devel/foo/dragonfly/patch-foo.c"],
        diff_bytes=1234,
        prompt_tokens=50_000,
        completion_tokens=2_500,
        total_tokens=52_500,
        classification="patch-error",
        confidence="high",
        attempts_total=1,
        attempts_max=4,
    ))
    # The operator-facing sections.
    for heading in ("## Summary", "## Bundle", "## Cost",
                     "## Files touched", "## Apply this fix",
                     "## Verify independently", "## Audit trail"):
        assert heading in out, f"missing section {heading!r}"


def test_render_cost_uses_thousands_separators():
    out = pf.render_proposed_fix(_ctx(
        prompt_tokens=1_234_567,
        completion_tokens=8_910,
        total_tokens=1_243_477,
    ))
    assert "1,234,567" in out
    assert "1,243,477" in out


def test_render_apply_recipe_uses_tracker_url_when_set():
    """When tracker_url is set, the recipe is a one-shot curl that
    pulls the diff from the artifact API."""
    out = pf.render_proposed_fix(_ctx(
        tracker_url="http://192.168.5.98:8080",
        diff_bytes=999,
    ))
    assert "curl -sS http://192.168.5.98:8080" in out
    assert "/api/bundles/b-1/artifacts/analysis/changes.diff" in out
    assert "git apply --3way /tmp/proposed-fix.diff" in out


def test_render_apply_recipe_falls_back_without_tracker_url():
    out = pf.render_proposed_fix(_ctx(tracker_url=""))
    # No curl line, but recipe is still actionable.
    assert "curl " not in out or "/api/bundles" not in out
    assert "git apply --3way" in out


def test_render_includes_signed_off_reminder():
    """Operator signs off; agent doesn't sign."""
    out = pf.render_proposed_fix(_ctx())
    assert "git commit -s" in out
    assert "Signed-off-by" in out or "agent itself does not sign" in out


def test_render_files_touched_truncates():
    files = [f"ports/devel/foo/file{i:03d}" for i in range(30)]
    out = pf.render_proposed_fix(_ctx(files_touched=files))
    assert "file000" in out
    assert "file019" in out
    assert "file020" not in out
    assert "and 10 more" in out


def test_render_omits_empty_sections():
    """A bare success with no patch.md summary still renders without
    blank-paragraph artifacts."""
    out = pf.render_proposed_fix(_ctx(summary="", files_touched=[]))
    assert "## Summary" not in out
    assert "## Files touched" not in out
    # But the operator-action sections are always present.
    assert "## Apply this fix" in out
    assert "## Verify independently" in out


# --- build_proposed_fix_ctx -------------------------------------------------


def test_build_ctx_extracts_summary_from_patch_md():
    patch_md = (
        "## Patch Summary\n"
        "Replaced the stale dragonfly/patch-Makefile.in with a "
        "text replace-once dops op against the upstream Makefile.am.\n"
        "\n"
        "## Patch Log\n"
        "...\n"
    )
    reader = _read_from_dict({"analysis/patch.md": patch_md})
    ctx = pf.build_proposed_fix_ctx(
        origin="devel/foo",
        read_bundle_text=reader,
    )
    assert "Replaced the stale dragonfly/patch-Makefile.in" in ctx.summary
    assert "Patch Log" not in ctx.summary    # didn't bleed into next section


def test_build_ctx_caps_summary_length():
    long_para = "x" * 800
    patch_md = f"## Patch Summary\n{long_para}\n"
    reader = _read_from_dict({"analysis/patch.md": patch_md})
    ctx = pf.build_proposed_fix_ctx(
        origin="devel/foo", read_bundle_text=reader,
    )
    assert len(ctx.summary) <= 501   # 500 chars + the truncation ellipsis


def test_build_ctx_reads_triage_tokens_from_triage_json():
    """Cost section is otherwise patch-only; build_proposed_fix_ctx
    reads analysis/triage.json and surfaces the triage spend so the
    operator sees the full run cost (triage + patch)."""
    audit = {
        "status": "success",
        "tokens_used": {"prompt": 165_000, "completion": 3_000,
                         "total": 168_000},
    }
    triage = {
        "tokens_used": {"prompt": 2_500, "completion": 3_700,
                         "total": 6_200},
    }
    reader = _read_from_dict({
        "analysis/patch_audit.json": json.dumps(audit),
        "analysis/triage.json": json.dumps(triage),
    })
    ctx = pf.build_proposed_fix_ctx(
        origin="devel/foo", read_bundle_text=reader,
    )
    assert ctx.total_tokens == 168_000
    assert ctx.triage_total_tokens == 6_200
    assert ctx.triage_prompt_tokens == 2_500
    assert ctx.triage_completion_tokens == 3_700


def test_render_cost_shows_triage_breakdown_when_present():
    out = pf.render_proposed_fix(_ctx(
        prompt_tokens=165_000, completion_tokens=3_000, total_tokens=168_000,
        triage_prompt_tokens=2_500, triage_completion_tokens=3_700,
        triage_total_tokens=6_200,
    ))
    assert "Triage" in out
    assert "6,200" in out
    assert "174,200" in out  # combined total: 168,000 + 6,200
    assert "Combined total" in out


def test_render_cost_omits_triage_when_zero():
    out = pf.render_proposed_fix(_ctx(
        prompt_tokens=80_000, completion_tokens=5_000, total_tokens=85_000,
        triage_total_tokens=0,
    ))
    assert "Triage" not in out
    assert "Combined total" not in out
    assert "85,000" in out


def test_build_ctx_reads_patch_audit_when_no_patch_result():
    audit = {
        "status": "success",
        "attempts": [{"attempt": 1, "rebuild_ok": True}],
        "tokens_used": {"prompt": 80_000, "completion": 5_000,
                         "total": 85_000},
        "model": "deepseek/deepseek-v4-pro",
    }
    reader = _read_from_dict({"analysis/patch_audit.json": json.dumps(audit)})
    ctx = pf.build_proposed_fix_ctx(
        origin="devel/foo", read_bundle_text=reader,
    )
    assert ctx.prompt_tokens == 80_000
    assert ctx.completion_tokens == 5_000
    assert ctx.total_tokens == 85_000
    assert ctx.attempts_total == 1
    assert ctx.status == "success"


def test_build_ctx_patch_result_wins_over_audit():
    @dataclass
    class Usage:
        prompt_tokens: int
        completion_tokens: int
        total_tokens: int

    @dataclass
    class Attempt:
        rebuild_ok: bool = True

    @dataclass
    class Result:
        status: str
        attempts: list
        usage: Usage

    result = Result(
        status="success",
        attempts=[Attempt(rebuild_ok=True)],
        usage=Usage(50_000, 2_000, 52_000),
    )
    # Audit says different numbers; patch_result should override.
    audit = {"status": "success", "attempts": [{"attempt": 1}],
             "tokens_used": {"prompt": 1, "completion": 1, "total": 2}}
    reader = _read_from_dict({"analysis/patch_audit.json": json.dumps(audit)})

    ctx = pf.build_proposed_fix_ctx(
        origin="devel/foo",
        read_bundle_text=reader,
        patch_result=result,
    )
    assert ctx.prompt_tokens == 50_000
    assert ctx.completion_tokens == 2_000
    assert ctx.total_tokens == 52_000


def test_build_ctx_backfills_triage_fields():
    triage_md = (
        "## Classification\npatch-error\n\n"
        "## Confidence\nhigh\n"
    )
    reader = _read_from_dict({"analysis/triage.md": triage_md})
    ctx = pf.build_proposed_fix_ctx(
        origin="devel/foo",
        read_bundle_text=reader,
    )
    # Backfilled when caller doesn't pass classification/confidence.
    assert ctx.classification == "patch-error"
    assert ctx.confidence == "high"


def test_build_ctx_explicit_classification_wins_over_triage_md():
    triage_md = "## Classification\nplist-error\n"
    reader = _read_from_dict({"analysis/triage.md": triage_md})
    ctx = pf.build_proposed_fix_ctx(
        origin="devel/foo",
        classification="compile-error",
        confidence="medium",
        read_bundle_text=reader,
    )
    assert ctx.classification == "compile-error"
    assert ctx.confidence == "medium"


def test_build_ctx_parses_files_touched_and_bytes():
    diff = (
        "diff --git a/ports/devel/foo/Makefile b/ports/devel/foo/Makefile\n"
        "--- a/ports/devel/foo/Makefile\n"
        "+++ b/ports/devel/foo/Makefile\n"
        "@@ -1 +1 @@\n"
        "-old\n+new\n"
        "diff --git a/ports/devel/foo/overlay.dops b/ports/devel/foo/overlay.dops\n"
        "+++ b/ports/devel/foo/overlay.dops\n"
        "+text replace-once ...\n"
    )
    reader = _read_from_dict({"analysis/changes.diff": diff})
    ctx = pf.build_proposed_fix_ctx(
        origin="devel/foo", read_bundle_text=reader,
    )
    assert ctx.files_touched == [
        "ports/devel/foo/Makefile",
        "ports/devel/foo/overlay.dops",
    ]
    assert ctx.diff_bytes == len(diff.encode("utf-8"))


def test_build_ctx_handles_missing_artifacts():
    reader = _read_from_dict({})
    ctx = pf.build_proposed_fix_ctx(
        origin="devel/foo", read_bundle_text=reader,
    )
    assert ctx.summary == ""
    assert ctx.files_touched == []
    assert ctx.diff_bytes == 0
    assert ctx.prompt_tokens == 0


def test_build_ctx_tolerates_invalid_patch_audit_json():
    reader = _read_from_dict({
        "analysis/patch_audit.json": "{not json",
    })
    ctx = pf.build_proposed_fix_ctx(
        origin="devel/foo", read_bundle_text=reader,
    )
    assert ctx.total_tokens == 0
    assert ctx.attempts_total == 0


# --- integration: tracker default-artifact priority ------------------------


def test_proposed_fix_is_top_of_artifact_priority():
    """The bundle viewer should land on proposed_fix.md by default
    when it exists (and on manual_handoff.md otherwise). This is the
    UX hinge for the operator's first read."""
    from dportsv3.tracker.server import _DEFAULT_ARTIFACT_PRIORITY
    assert _DEFAULT_ARTIFACT_PRIORITY[0] == "analysis/proposed_fix.md"
    assert _DEFAULT_ARTIFACT_PRIORITY[1] == "analysis/manual_handoff.md"
    # Triage/patch come AFTER summaries — they're support material.
    assert _DEFAULT_ARTIFACT_PRIORITY.index("analysis/triage.md") > 1
