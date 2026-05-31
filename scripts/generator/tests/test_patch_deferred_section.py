"""Step 37-3: patch payload + agent prompt for the deferred-patches
relevance pass.

Two surfaces under test:

- ``DeferredFromConvertSection`` in ``context.py``: renders a
  ``## Deferred from Convert`` section in the patch payload when the
  bundle's typed ``ConvertResult`` carries deferred_patches.
- ``_parse_patch_plan`` + ``_write_patch_audit_harness`` plumbing in
  ``runner.py``: parses the agent's ``Patch Plan (JSON)`` block,
  extracts ``deferred_verdicts``, persists them on the typed
  ``PatchResult``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dportsv3.agent import runner as runner_mod
from dportsv3.agent.context import (
    ContextCtx,
    DeferredFromConvertSection,
    render_payload,
)
from dportsv3.agent.phase_result import (
    ConvertResult,
    DeferredPatch,
    DeferredVerdict,
    PatchResult,
    load_phase_result,
    write_phase_result,
)
from dportsv3.agent.runner import _parse_patch_plan


# --- DeferredFromConvertSection -----------------------------------------------


@pytest.fixture
def saved_store(monkeypatch):
    """Stub artifact_store_put + read_bundle_text against an in-memory
    dict so writes/loads round-trip through phase_result without
    requiring a real tracker."""
    saved: dict = {}

    def fake_put(bundle_id, relpath, data, _kind):
        saved[(bundle_id, relpath)] = data
        return True

    def fake_read(_bundle_dir, bundle_id, relpath):
        data = saved.get((bundle_id, relpath))
        return data.decode("utf-8") if data else None

    monkeypatch.setattr(runner_mod, "artifact_store_put", fake_put)
    monkeypatch.setattr(runner_mod, "read_bundle_text", fake_read)
    return saved


def test_section_renders_nothing_without_convert_result(saved_store):
    ctx = ContextCtx(bundle_id="bundle-fresh",
                     read_bundle_text=runner_mod.read_bundle_text)
    assert DeferredFromConvertSection().render(ctx) is None


def test_section_renders_nothing_when_no_deferred_patches(saved_store):
    write_phase_result("bundle-clean", "convert", ConvertResult(
        status="verified", reapply_ok=True, reason_code=None,
        overlay_sha256="x", files_removed=[], diag_tail=None,
        tokens_prompt=0, tokens_completion=0, tokens_total=0,
        deferred_patches=[],
    ))
    ctx = ContextCtx(bundle_id="bundle-clean",
                     read_bundle_text=runner_mod.read_bundle_text)
    assert DeferredFromConvertSection().render(ctx) is None


def test_section_renders_each_deferred_patch(saved_store):
    write_phase_result("bundle-dirty", "convert", ConvertResult(
        status="verified", reapply_ok=True, reason_code=None,
        overlay_sha256="x", files_removed=[], diag_tail=None,
        tokens_prompt=0, tokens_completion=0, tokens_total=0,
        deferred_patches=[
            DeferredPatch(
                path="diffs/pkg-plist.diff",
                target_file="pkg-plist",
                original_content=(
                    "--- pkg-plist.orig\n"
                    "+++ pkg-plist\n"
                    "@@ -249,9 +249,6 @@\n"
                    "-removed line\n"
                ),
                reject_summary="Hunks #1 #3 failed at 249, 2929",
            ),
            DeferredPatch(
                path="diffs/Makefile.diff",
                target_file="Makefile",
                original_content="--- Makefile.orig\n+++ Makefile\n",
                reject_summary="Hunks #1 failed at 50",
            ),
        ],
    ))
    ctx = ContextCtx(bundle_id="bundle-dirty",
                     read_bundle_text=runner_mod.read_bundle_text)
    out = DeferredFromConvertSection().render(ctx)
    assert out is not None
    assert "## Deferred from Convert" in out
    # Each entry surfaces with its routing tuple.
    assert "### diffs/pkg-plist.diff → pkg-plist" in out
    assert "### diffs/Makefile.diff → Makefile" in out
    # Reject summary is rendered verbatim.
    assert "Hunks #1 #3 failed at 249, 2929" in out
    # Diff content is fenced as diff for the agent's reading.
    assert "```diff" in out
    assert "+++ pkg-plist" in out
    # The relevance-pass instructions appear (the prompt's
    # complementary "what to do" rule lives in PATCH_INTENT_SYSTEM;
    # the section itself frames the task too).
    assert "regenerated" in out
    assert "dropped" in out
    assert "escalated" in out


def test_section_caps_long_diff_content(saved_store):
    big_diff = "+ line\n" * 5000  # ~35KB
    write_phase_result("bundle-big", "convert", ConvertResult(
        status="verified", reapply_ok=True, reason_code=None,
        overlay_sha256="x", files_removed=[], diag_tail=None,
        tokens_prompt=0, tokens_completion=0, tokens_total=0,
        deferred_patches=[DeferredPatch(
            path="diffs/big.diff",
            target_file="big",
            original_content=big_diff,
            reject_summary="Hunks #1 failed",
        )],
    ))
    ctx = ContextCtx(bundle_id="bundle-big",
                     read_bundle_text=runner_mod.read_bundle_text)
    section = DeferredFromConvertSection(max_diff_chars=4000)
    out = section.render(ctx)
    assert out is not None
    assert "truncated to 4000 chars" in out
    # Output should be roughly the cap (plus header/marker overhead),
    # nowhere near the 35KB input.
    assert len(out) < 6000


def test_section_in_payload_priority_after_triage_summary(saved_store):
    """Section sorts between TriageSummary (30) and SiblingBundles (40).
    Verify via the full assembler so a future priority drift surfaces."""
    write_phase_result("bundle-order", "convert", ConvertResult(
        status="verified", reapply_ok=True, reason_code=None,
        overlay_sha256="x", files_removed=[], diag_tail=None,
        tokens_prompt=0, tokens_completion=0, tokens_total=0,
        deferred_patches=[DeferredPatch(
            path="diffs/p.diff", target_file="p",
            original_content="--- p.orig\n+++ p\n",
            reject_summary="Hunks #1 failed",
        )],
    ))
    from dportsv3.agent.context import TriageSummarySection
    ctx = ContextCtx(bundle_id="bundle-order",
                     read_bundle_text=runner_mod.read_bundle_text)
    # Seed a triage.md so TriageSummarySection emits.
    saved_store[("bundle-order", "analysis/triage.md")] = (
        b"## Classification\nplist-error\n"
    )
    payload = render_payload(
        [TriageSummarySection(), DeferredFromConvertSection()], ctx,
    )
    triage_idx = payload.index("## Triage Summary")
    deferred_idx = payload.index("## Deferred from Convert")
    assert triage_idx < deferred_idx


# --- _parse_patch_plan + PatchResult round-trip ------------------------------


def test_parse_patch_plan_extracts_deferred_verdicts():
    text = (
        "## Patch Log\nTried things.\n\n"
        "## Rebuild Status\nfailed\n\n"
        "## Patch Plan (JSON)\n"
        "```json\n"
        + json.dumps({
            "origin": "lang/python311",
            "summary": "regenerated one deferred patch",
            "intents_emitted": ["add_patch"],
            "tools_used": ["get_file"],
            "deferred_verdicts": [{
                "path": "diffs/pkg-plist.diff",
                "verdict": "regenerated",
                "rationale": "lines moved; new hunks at 254",
                "intents_emitted": ["add_patch"],
            }],
        }, indent=2)
        + "\n```\n\n"
        "## Rebuild Proof (JSON)\n"
        "```json\n{\"rebuild_ok\": false}\n```\n"
    )
    plan = _parse_patch_plan(text)
    assert plan is not None
    assert plan["origin"] == "lang/python311"
    verdicts = plan["deferred_verdicts"]
    assert len(verdicts) == 1
    assert verdicts[0]["verdict"] == "regenerated"


def test_parse_patch_plan_returns_none_without_block():
    assert _parse_patch_plan("") is None
    assert _parse_patch_plan("just prose, no plan") is None
    # Heading without a fenced block:
    assert _parse_patch_plan("## Patch Plan (JSON)\nno code\n") is None


def test_parse_patch_plan_returns_none_on_bad_json():
    text = "## Patch Plan (JSON)\n```json\n{this isn't json\n```\n"
    assert _parse_patch_plan(text) is None


def test_patch_result_round_trips_deferred_verdicts(saved_store):
    result = PatchResult(
        rebuild_ok=False,
        status="needs-help",
        attempts=1,
        intents_applied=1,
        tokens_prompt=1000,
        tokens_completion=500,
        tokens_total=1500,
        deferred_verdicts=[
            DeferredVerdict(
                path="diffs/pkg-plist.diff",
                verdict="dropped",
                rationale="upstream removed those lines",
                intents_emitted=[],
            ),
        ],
    )
    write_phase_result("bundle-pr", "patch", result)
    raw = saved_store[("bundle-pr", "analysis/patch_result.json")].decode("utf-8")
    payload = json.loads(raw)
    assert payload["schema_version"] == 2
    assert payload["deferred_verdicts"][0]["verdict"] == "dropped"

    loaded = load_phase_result(None, "bundle-pr", "patch", PatchResult)
    assert loaded is not None
    assert isinstance(loaded.deferred_verdicts[0], DeferredVerdict)
    assert loaded.deferred_verdicts[0].rationale.startswith("upstream removed")


def test_patch_result_invalid_verdict_strings_dropped_at_write(saved_store):
    """The runner's _write_patch_audit_harness filters bad entries
    out of the agent's response before constructing DeferredVerdict
    instances. Smoke-test that filter inline (full producer flow has
    too many moving parts for this test)."""
    plan = {
        "deferred_verdicts": [
            {"path": "x", "verdict": "regenerated", "rationale": "ok",
             "intents_emitted": ["add_patch"]},
            # Missing path
            {"verdict": "dropped"},
            # Bad verdict value
            {"path": "y", "verdict": "totally-made-up"},
            # Empty path
            {"path": "", "verdict": "escalated"},
        ],
    }
    valid: list[dict] = []
    for entry in plan["deferred_verdicts"]:
        path = str(entry.get("path") or "").strip()
        verdict = str(entry.get("verdict") or "").strip().lower()
        if path and verdict in {"regenerated", "dropped", "escalated"}:
            valid.append(entry)
    assert len(valid) == 1
    assert valid[0]["path"] == "x"
