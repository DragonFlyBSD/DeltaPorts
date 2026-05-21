"""Tests for dportsv3.agent.manual_handoff (Step 3, manual-escalation plan).

Covers:
- ``render_handoff`` produces the right operator question per reason
  (manual_tier, retry_cap, patch_budget_exhausted, patch_gave_up).
- Truncation: changes-diff bytes, files-touched list, errors tail.
- ``build_handoff_ctx`` reads bundle artifacts via the injected
  ``read_bundle_text`` callable and fills the right fields.
- patch_result wins over patch_audit.json when both are available.
- decision_extra fills classification/confidence even when triage.md
  is missing; triage.md backfills them when decision_extra omits them.
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


from dportsv3.agent import manual_handoff as mh  # noqa: E402


# --- helpers -----------------------------------------------------------------


def _ctx(**kwargs) -> mh.HandoffCtx:
    base = dict(
        origin="devel/foo",
        target="@main",
        reason=mh.REASON_MANUAL_TIER,
        bundle_id="b-1",
        classification="compile-error",
        confidence="medium",
    )
    base.update(kwargs)
    return mh.HandoffCtx(**base)


def _read_from_dict(artifacts: dict[str, str]):
    def reader(bundle_dir, bundle_id, relpath):
        return artifacts.get(relpath)
    return reader


# --- render: per-reason operator question ------------------------------------


def test_render_manual_tier_with_suggested_fix_mentions_try_again():
    out = mh.render_handoff(_ctx(
        reason=mh.REASON_MANUAL_TIER,
        suggested_fix="Bump WRKSRC and rerun configure.",
    ))
    assert "# Manual Handoff" in out
    assert "triage classified as MANUAL" in out
    assert "Suggested Fix" in out
    assert "Bump WRKSRC" in out
    assert "Try again with this context" in out


def test_render_manual_tier_without_suggested_fix_asks_for_approach():
    out = mh.render_handoff(_ctx(
        reason=mh.REASON_MANUAL_TIER,
        suggested_fix="",
    ))
    assert "no concrete fix path" in out
    assert "What approach should the agent take" in out


def test_render_retry_cap_includes_window_and_counts():
    out = mh.render_handoff(_ctx(
        reason=mh.REASON_RETRY_CAP,
        recent_failures=5,
        max_attempts=3,
        window_hours=2,
    ))
    assert "retry cap reached" in out
    assert "5 times" in out
    assert "in the last 2h" in out
    assert "Recent failures: 5" in out
    assert "Cap: 3 in last 2h" in out


def test_render_patch_budget_includes_tokens_and_attempts():
    out = mh.render_handoff(_ctx(
        reason=mh.REASON_PATCH_BUDGET,
        patch_attempts=4,
        patch_status="patch_budget_exhausted",
        tokens_used=120000,
    ))
    assert "patch budget exhausted" in out
    assert "4 attempt(s)" in out
    assert "120000 tokens" in out
    assert "Should the budget be raised" in out


def test_render_patch_gave_up_suggests_dops_or_reinplace():
    out = mh.render_handoff(_ctx(
        reason=mh.REASON_PATCH_GAVE_UP,
        patch_attempts=2,
        patch_status="needs-help",
    ))
    assert "patch agent gave up" in out
    assert "gave up after 2 attempt(s)" in out
    assert "dops" in out
    assert "REINPLACE_CMD" in out


def test_render_includes_reason_detail_when_present():
    out = mh.render_handoff(_ctx(
        reason=mh.REASON_PATCH_GAVE_UP,
        reason_detail="harness raised: connection reset",
    ))
    assert "- **Detail:** harness raised: connection reset" in out


def test_render_omits_empty_sections():
    """A bare ctx with only origin+reason set should render header +
    operator question, nothing else."""
    out = mh.render_handoff(mh.HandoffCtx(
        origin="devel/foo", reason=mh.REASON_MANUAL_TIER,
    ))
    assert "## Triage" not in out
    assert "## Attempt History" not in out
    assert "## Last Patch Attempt" not in out
    assert "## Changes Diff" not in out
    assert "## Last Failing Build" not in out
    assert "## Operator Question" in out


# --- render: truncation ------------------------------------------------------


def test_render_truncates_files_touched_list():
    files = [f"ports/devel/foo/file{i:03d}.c" for i in range(30)]
    out = mh.render_handoff(_ctx(
        patch_status="needs-help", patch_attempts=1,
        files_touched=files,
    ))
    # Only first MAX_FILES_LIST shown; remainder counted.
    assert "file000.c" in out
    assert "file019.c" in out
    assert "file020.c" not in out
    assert "and 10 more" in out


def test_render_diff_summary_block_uses_fenced_code():
    out = mh.render_handoff(_ctx(
        changes_diff_summary="diff --git a/x b/x\n+foo\n",
    ))
    assert "```diff" in out
    assert "+foo" in out


def test_render_errors_tail_block_uses_plain_fence():
    out = mh.render_handoff(_ctx(errors_tail="cc: fatal error\nmake: ***"))
    assert "## Last Failing Build (tail)" in out
    assert "cc: fatal error" in out


# --- build_handoff_ctx -------------------------------------------------------


def test_build_ctx_extracts_triage_classification_confidence_and_fix():
    triage_md = (
        "## Classification\n"
        "compile-error\n\n"
        "## Confidence\n"
        "high\n\n"
        "## Suggested Fix\n"
        "Add `-fcommon` to CFLAGS.\n\n"
        "## Notes\n"
        "Trailing section.\n"
    )
    reader = _read_from_dict({"analysis/triage.md": triage_md})
    ctx = mh.build_handoff_ctx(
        origin="devel/foo",
        reason=mh.REASON_MANUAL_TIER,
        read_bundle_text=reader,
    )
    assert ctx.classification == "compile-error"
    assert ctx.confidence == "high"
    assert "Add `-fcommon`" in ctx.suggested_fix
    # The "Notes" trailing section must not bleed into Suggested Fix.
    assert "Trailing" not in ctx.suggested_fix


def test_build_ctx_decision_extra_overrides_triage_for_classification():
    triage_md = (
        "## Classification\nplist-error\n\n## Confidence\nmedium\n"
    )
    reader = _read_from_dict({"analysis/triage.md": triage_md})
    ctx = mh.build_handoff_ctx(
        origin="devel/foo",
        reason=mh.REASON_RETRY_CAP,
        decision_extra={
            "classification": "compile-error",
            "confidence": "low",
            "recent_failures": 4,
            "max_attempts": 3,
            "window_hours": 2,
        },
        read_bundle_text=reader,
    )
    # decision_extra wins.
    assert ctx.classification == "compile-error"
    assert ctx.confidence == "low"
    assert ctx.recent_failures == 4
    assert ctx.max_attempts == 3
    assert ctx.window_hours == 2


def test_build_ctx_reads_patch_audit_when_no_patch_result():
    audit = {
        "status": "patch_budget_exhausted",
        "attempts": [{"attempt": 1}, {"attempt": 2}],
        "tokens_used": {"total": 87654, "prompt": 1, "completion": 2},
    }
    reader = _read_from_dict({
        "analysis/patch_audit.json": json.dumps(audit),
    })
    ctx = mh.build_handoff_ctx(
        origin="devel/foo",
        reason=mh.REASON_PATCH_BUDGET,
        read_bundle_text=reader,
    )
    assert ctx.patch_attempts == 2
    assert ctx.patch_status == "patch_budget_exhausted"
    assert ctx.tokens_used == 87654


def test_build_ctx_patch_result_wins_over_audit():
    @dataclass
    class Usage:
        total_tokens: int
        prompt_tokens: int = 0
        completion_tokens: int = 0

    @dataclass
    class Result:
        status: str
        attempts: list
        usage: Usage

    result = Result(
        status="needs-help",
        attempts=[1, 2, 3],
        usage=Usage(total_tokens=999),
    )
    audit = {
        "status": "patch_budget_exhausted",
        "attempts": [{"attempt": 1}],
        "tokens_used": {"total": 1},
    }
    reader = _read_from_dict({
        "analysis/patch_audit.json": json.dumps(audit),
    })
    ctx = mh.build_handoff_ctx(
        origin="devel/foo",
        reason=mh.REASON_PATCH_GAVE_UP,
        read_bundle_text=reader,
        patch_result=result,
    )
    # patch_result takes precedence; audit ignored.
    assert ctx.patch_status == "needs-help"
    assert ctx.patch_attempts == 3
    assert ctx.tokens_used == 999


def test_build_ctx_parses_diff_files_and_truncates_diff():
    diff = (
        "diff --git a/ports/devel/foo/Makefile b/ports/devel/foo/Makefile\n"
        "--- a/ports/devel/foo/Makefile\n"
        "+++ b/ports/devel/foo/Makefile\n"
        "@@ -1 +1 @@\n"
        "-old\n+new\n"
        "diff --git a/ports/devel/foo/distinfo b/ports/devel/foo/distinfo\n"
        "+++ b/ports/devel/foo/distinfo\n"
        "+SHA256\n"
    )
    reader = _read_from_dict({"analysis/changes.diff": diff})
    ctx = mh.build_handoff_ctx(
        origin="devel/foo",
        reason=mh.REASON_PATCH_GAVE_UP,
        read_bundle_text=reader,
    )
    assert ctx.files_touched == [
        "ports/devel/foo/Makefile",
        "ports/devel/foo/distinfo",
    ]
    assert "diff --git" in ctx.changes_diff_summary
    assert ctx.changes_diff_summary.count("\n") < diff.count("\n") + 5


def test_build_ctx_truncates_diff_over_byte_limit():
    diff = "+" + "x" * 5000 + "\n"
    reader = _read_from_dict({"analysis/changes.diff": diff})
    ctx = mh.build_handoff_ctx(
        origin="devel/foo",
        reason=mh.REASON_PATCH_GAVE_UP,
        read_bundle_text=reader,
    )
    assert "[truncated]" in ctx.changes_diff_summary
    assert len(ctx.changes_diff_summary.encode("utf-8")) < 4500


def test_build_ctx_tails_errors_log():
    body = "\n".join(f"line {i}" for i in range(200)) + "\n"
    reader = _read_from_dict({"errors.txt": body})
    ctx = mh.build_handoff_ctx(
        origin="devel/foo",
        reason=mh.REASON_PATCH_GAVE_UP,
        read_bundle_text=reader,
    )
    tail = ctx.errors_tail.splitlines()
    assert len(tail) == 40
    assert tail[-1] == "line 199"
    assert tail[0] == "line 160"


def test_build_ctx_handles_missing_artifacts_gracefully():
    reader = _read_from_dict({})
    ctx = mh.build_handoff_ctx(
        origin="devel/foo",
        reason=mh.REASON_MANUAL_TIER,
        read_bundle_text=reader,
    )
    assert ctx.suggested_fix == ""
    assert ctx.classification == ""
    assert ctx.patch_attempts == 0
    assert ctx.files_touched == []
    assert ctx.errors_tail == ""


def test_build_ctx_handles_invalid_patch_audit_json():
    reader = _read_from_dict({"analysis/patch_audit.json": "not json"})
    ctx = mh.build_handoff_ctx(
        origin="devel/foo",
        reason=mh.REASON_PATCH_BUDGET,
        read_bundle_text=reader,
    )
    assert ctx.patch_attempts == 0
    assert ctx.patch_status == ""


# --- end-to-end render via build_handoff_ctx --------------------------------


def test_render_via_build_ctx_full_payload():
    triage_md = (
        "## Classification\ncompile-error\n\n"
        "## Confidence\nmedium\n\n"
        "## Suggested Fix\nUse `-Wno-error` for now.\n"
    )
    audit = {
        "status": "patch_gave_up",
        "attempts": [{"attempt": 1}, {"attempt": 2}],
        "tokens_used": {"total": 45000},
    }
    diff = (
        "diff --git a/ports/devel/foo/Makefile b/ports/devel/foo/Makefile\n"
        "+++ b/ports/devel/foo/Makefile\n"
        "+CFLAGS+= -Wno-error\n"
    )
    errors = "cc: error: implicit declaration of function 'foo'\n"
    reader = _read_from_dict({
        "analysis/triage.md": triage_md,
        "analysis/patch_audit.json": json.dumps(audit),
        "analysis/changes.diff": diff,
        "errors.txt": errors,
    })
    ctx = mh.build_handoff_ctx(
        origin="devel/foo",
        target="@main",
        reason=mh.REASON_PATCH_GAVE_UP,
        reason_detail="needs-help after 2 attempts",
        bundle_id="b-42",
        read_bundle_text=reader,
    )
    out = mh.render_handoff(ctx)
    # All sections present.
    assert "`devel/foo`" in out
    assert "`@main`" in out
    assert "`b-42`" in out
    assert "compile-error" in out
    assert "Use `-Wno-error`" in out
    assert "Status: `patch_gave_up`" in out
    assert "Attempts: 2" in out
    assert "Tokens used: 45000" in out
    assert "ports/devel/foo/Makefile" in out
    assert "cc: error" in out
    assert "Operator Question" in out
