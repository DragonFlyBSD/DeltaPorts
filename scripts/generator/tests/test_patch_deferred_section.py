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
                backing_file="diffs/pkg-plist.diff",
            ),
            # Inline op (no file on disk): rendered as `text`, not `diff`.
            DeferredPatch(
                path="op:abc123def456",
                target_file="Makefile",
                original_content='mk set CFLAGS "-O2"\n',
                reject_summary="multiple assignments found for CFLAGS",
                backing_file=None,
            ),
        ],
    ))
    ctx = ContextCtx(bundle_id="bundle-dirty",
                     read_bundle_text=runner_mod.read_bundle_text)
    out = DeferredFromConvertSection().render(ctx)
    assert out is not None
    assert "## Deferred from Convert" in out
    # Each entry surfaces with its identifier + target tuple.
    assert "### diffs/pkg-plist.diff → pkg-plist" in out
    assert "### op:abc123def456 → Makefile" in out
    # Reject summary is rendered verbatim.
    assert "Hunks #1 #3 failed at 249, 2929" in out
    # File-backed entry → diff fence; inline entry → text fence.
    assert "```diff" in out
    assert "```text" in out
    assert "+++ pkg-plist" in out
    assert 'mk set CFLAGS' in out
    # The relevance-pass instructions appear (the prompt's
    # complementary "what to do" rule lives in PATCH_SYSTEM;
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
            "tools_used": ["get_file"],
            "deferred_verdicts": [{
                "path": "diffs/pkg-plist.diff",
                "verdict": "regenerated",
                "rationale": "lines moved; new hunks at 254",
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
        tokens_prompt=1000,
        tokens_completion=500,
        tokens_total=1500,
        deferred_verdicts=[
            DeferredVerdict(
                path="diffs/pkg-plist.diff",
                verdict="dropped",
                rationale="upstream removed those lines",
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
            {"path": "x", "verdict": "regenerated", "rationale": "ok"},
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


# --- Step 37-4: per-verdict escalation + playbook discovery ------------------


def test_playbook_entry_is_discoverable_for_plist_error_patch_flow():
    """The new convert-deferred-patch-relevance.md should attach to
    patch payloads on plist-error classifications. Smoke test
    against load_playbooks so a future trigger drift breaks the
    test."""
    from dportsv3.agent.playbooks import find_playbooks_dir, load_playbooks
    pb_dir = find_playbooks_dir()
    assert pb_dir is not None, "playbooks dir not found in this checkout"
    sel = load_playbooks(
        pb_dir, role="patch",
        classification="plist-error",
        toolchains=[],
    )
    assert "convert-deferred-patch-relevance.md" in sel.included, (
        f"expected the deferred-patch playbook in patch+plist-error "
        f"selection, got: {sel.included}"
    )


def test_playbook_not_attached_to_unrelated_classifications():
    """missing-dep is not in the playbook's classifications trigger
    ([plist-error, patch-error]); it should NOT attach there."""
    from dportsv3.agent.playbooks import find_playbooks_dir, load_playbooks
    pb_dir = find_playbooks_dir()
    sel = load_playbooks(
        pb_dir, role="patch",
        classification="missing-dep",
        toolchains=[],
    )
    assert "convert-deferred-patch-relevance.md" not in sel.included


def test_manual_handoff_reason_for_escalated_verdicts_registered():
    """Step 37-4 added REASON_PATCH_ESCALATED_VERDICTS. Verify it's
    in VALID_REASONS so the handoff writer accepts it."""
    from dportsv3.agent.manual_handoff import (
        REASON_PATCH_ESCALATED_VERDICTS, VALID_REASONS,
    )
    assert REASON_PATCH_ESCALATED_VERDICTS == "patch_escalated_verdicts"
    assert REASON_PATCH_ESCALATED_VERDICTS in VALID_REASONS


def test_patching_to_escalated_transition_legal():
    """Step 37-4 added (PATCHING, ESCALATE_MANUAL) → ESCALATED in
    the FSM. Without this transition the per-verdict escalation
    path in PatchAttemptStep would raise IllegalTransition."""
    from dportsv3.agent.lifecycle import (
        JobEvent, JobState, TRANSITIONS,
    )
    assert (JobState.PATCHING, JobEvent.ESCALATE_MANUAL) in TRANSITIONS
    assert TRANSITIONS[(JobState.PATCHING, JobEvent.ESCALATE_MANUAL)] == JobState.ESCALATED


# --- Step 37-4 fix-up: resolver synthesizes missing verdicts -----------------


from dportsv3.agent.runner import _resolve_deferred_verdicts_for_patch


def _convert_with_deferred(paths):
    return ConvertResult(
        status="verified", reapply_ok=True, reason_code=None,
        overlay_sha256="x", files_removed=[], diag_tail=None,
        tokens_prompt=0, tokens_completion=0, tokens_total=0,
        deferred_patches=[DeferredPatch(
            path=p, target_file=p.split("/")[-1].replace(".diff", ""),
            original_content="--- x.orig\n+++ x\n",
            reject_summary="Hunks #1 failed",
        ) for p in paths],
    )


def _plan_text(verdicts):
    """Build a synthetic agent response with a Patch Plan block."""
    return (
        "## Patch Log\nfoo\n\n"
        "## Rebuild Status\nsuccess\n\n"
        "## Patch Plan (JSON)\n"
        "```json\n"
        + json.dumps({
            "origin": "x/y", "summary": "s",
            "tools_used": [],
            "deferred_verdicts": verdicts,
        }, indent=2)
        + "\n```\n\n"
        "## Rebuild Proof (JSON)\n"
        "```json\n{\"rebuild_ok\": true}\n```\n"
    )


def test_resolver_returns_empty_when_convert_didnt_defer(saved_store):
    write_phase_result("b1", "convert", _convert_with_deferred([]))
    out = _resolve_deferred_verdicts_for_patch(None, "b1", _plan_text([]))
    assert out == []


def test_resolver_returns_empty_when_no_convert_result(saved_store):
    """Fresh bundle (no convert ever ran) → nothing to verdict."""
    out = _resolve_deferred_verdicts_for_patch(None, "b-missing", "any text")
    assert out == []


def test_resolver_accepts_dict_keyed_verdicts(saved_store):
    """LLMs often emit `deferred_verdicts` as a dict keyed by the op
    identifier instead of the documented array. Accept it: fold the key
    in as `path`. Regression for the false-MANUAL bug where dict-shaped
    verdicts were silently dropped and synthesized as escalated despite
    a correct, build-passing fix."""
    write_phase_result(
        "b-dict", "convert",
        _convert_with_deferred(["op:abc123", "op:def456"]),
    )
    plan = _plan_text({
        "op:abc123": {"verdict": "regenerated", "rationale": "still applies"},
        "op:def456": {"verdict": "dropped", "rationale": "upstream removed it"},
    })
    out = _resolve_deferred_verdicts_for_patch(None, "b-dict", plan)
    by = {v.path: v.verdict for v in out}
    assert by == {"op:abc123": "regenerated", "op:def456": "dropped"}
    assert not any(v.verdict == "escalated" for v in out)


def test_resolver_dict_key_wins_over_nested_path(saved_store):
    """When the dict value also carries its own (different) `path` —
    agents sometimes put the target file there — the dict KEY (the op
    identifier) is authoritative and must win, else the verdict won't
    match the convert-deferred op id and gets falsely escalated."""
    write_phase_result(
        "b-dict2", "convert", _convert_with_deferred(["op:e9e9e1"]),
    )
    plan = _plan_text({
        "op:e9e9e1": {
            "path": "/work/artifacts/.../Makefile",  # mislabeled target
            "verdict": "dropped",
            "rationale": "duplicate of upstream assignment",
        },
    })
    out = _resolve_deferred_verdicts_for_patch(None, "b-dict2", plan)
    assert len(out) == 1
    assert out[0].path == "op:e9e9e1"
    assert out[0].verdict == "dropped"


def test_resolver_uses_agent_verdict_when_provided(saved_store):
    write_phase_result(
        "b2", "convert", _convert_with_deferred(["diffs/a.diff"]),
    )
    plan = _plan_text([{
        "path": "diffs/a.diff", "verdict": "regenerated",
        "rationale": "lines moved; regenerated in overlay.dops",
    }])
    out = _resolve_deferred_verdicts_for_patch(None, "b2", plan)
    assert len(out) == 1
    assert out[0].path == "diffs/a.diff"
    assert out[0].verdict == "regenerated"


def test_resolver_synthesizes_missing_verdict(saved_store):
    """Convert deferred TWO patches; agent provided verdict for ONE.
    The other gets escalated with synthetic rationale — closes the
    silent-skip gap."""
    write_phase_result(
        "b3", "convert",
        _convert_with_deferred(["diffs/a.diff", "diffs/b.diff"]),
    )
    plan = _plan_text([{
        "path": "diffs/a.diff", "verdict": "dropped",
        "rationale": "upstream removed lines",
    }])
    out = _resolve_deferred_verdicts_for_patch(None, "b3", plan)
    assert [v.path for v in out] == ["diffs/a.diff", "diffs/b.diff"]
    assert out[0].verdict == "dropped"
    assert out[1].verdict == "escalated"
    assert "no verdict provided" in out[1].rationale


def test_resolver_synthesizes_all_when_agent_provided_no_plan(saved_store):
    """Worst case: agent emitted no Patch Plan at all. Every deferred
    patch gets escalated. Bundle routes to MANUAL."""
    write_phase_result(
        "b4", "convert",
        _convert_with_deferred(["diffs/a.diff", "diffs/b.diff"]),
    )
    out = _resolve_deferred_verdicts_for_patch(
        None, "b4", "just prose, no JSON",
    )
    assert len(out) == 2
    assert all(v.verdict == "escalated" for v in out)
    assert all("no verdict provided" in v.rationale for v in out)


def test_resolver_drops_invalid_verdict_strings(saved_store):
    """Agent emits a bogus verdict string → treat as missing → synth."""
    write_phase_result(
        "b5", "convert", _convert_with_deferred(["diffs/a.diff"]),
    )
    plan = _plan_text([{
        "path": "diffs/a.diff", "verdict": "totally-made-up",
        "rationale": "...",
    }])
    out = _resolve_deferred_verdicts_for_patch(None, "b5", plan)
    assert len(out) == 1
    assert out[0].verdict == "escalated"
    assert "no verdict provided" in out[0].rationale


def test_resolver_ignores_verdicts_for_paths_not_deferred(saved_store):
    """Agent invents a verdict for a path convert didn't defer →
    silently dropped. Only convert-listed paths get verdicts in
    the output."""
    write_phase_result(
        "b6", "convert", _convert_with_deferred(["diffs/a.diff"]),
    )
    plan = _plan_text([
        {"path": "diffs/a.diff", "verdict": "dropped", "rationale": "."},
        {"path": "diffs/never-deferred.diff", "verdict": "regenerated",
         "rationale": "."},
    ])
    out = _resolve_deferred_verdicts_for_patch(None, "b6", plan)
    assert [v.path for v in out] == ["diffs/a.diff"]


# --- Step 37 #4-fix: cleanup_resolved_deferred_patches -----------------------


from dportsv3.agent.runner import cleanup_resolved_deferred_patches


class _CleanupPaths:
    def __init__(self, deltaports):
        self.deltaports = deltaports


def _setup_diffs_tree(tmp_path: Path, files: dict[str, str]):
    """Build an env-like tree with ports/lang/foo/diffs/* populated."""
    deltaports = tmp_path / "DeltaPorts"
    diffs_dir = deltaports / "ports" / "lang" / "foo" / "diffs"
    diffs_dir.mkdir(parents=True)
    for name, content in files.items():
        (diffs_dir / name).write_text(content)
    return deltaports, diffs_dir


def _verdict(path, verdict, rationale="."):
    return DeferredVerdict(
        path=path, verdict=verdict, rationale=rationale,
    )


def _write_convert_backing(bundle_id, entries):
    """Persist a ConvertResult whose deferred_patches map each
    verdict path → backing_file, so cleanup can resolve what's on
    disk. ``entries`` is a list of (path, backing_file)."""
    write_phase_result(bundle_id, "convert", ConvertResult(
        status="verified", reapply_ok=True, reason_code=None,
        overlay_sha256="x", files_removed=[], diag_tail=None,
        tokens_prompt=0, tokens_completion=0, tokens_total=0,
        deferred_patches=[
            DeferredPatch(path=p, target_file="t", original_content="c",
                          reject_summary="r", backing_file=bf)
            for p, bf in entries
        ],
    ))


def test_cleanup_removes_regenerated_and_dropped(tmp_path, monkeypatch, saved_store):
    deltaports, diffs_dir = _setup_diffs_tree(
        tmp_path,
        {"a.diff": "a", "b.diff": "b", "c.diff": "c"},
    )
    from dportsv3.agent import worker as _w
    monkeypatch.setattr(_w, "env_paths",
                        lambda env: _CleanupPaths(deltaports))
    _write_convert_backing("b-clean", [
        ("diffs/a.diff", "diffs/a.diff"),
        ("diffs/b.diff", "diffs/b.diff"),
        ("diffs/c.diff", "diffs/c.diff"),
    ])
    queue_root = tmp_path / "queue"
    queue_root.mkdir()
    verdicts = [
        _verdict("diffs/a.diff", "regenerated"),
        _verdict("diffs/b.diff", "dropped"),
        _verdict("diffs/c.diff", "escalated"),
    ]
    deleted = cleanup_resolved_deferred_patches(
        env="t", origin="lang/foo", verdicts=verdicts,
        queue_root=queue_root, job_id="j-1", bundle_id="b-clean",
    )
    assert sorted(deleted) == ["diffs/a.diff", "diffs/b.diff"]
    assert (diffs_dir / "c.diff").exists()
    assert not (diffs_dir / "a.diff").exists()
    assert not (diffs_dir / "b.diff").exists()


def test_cleanup_skips_inline_op_with_no_backing_file(tmp_path, monkeypatch, saved_store):
    """An inline-op deferral (backing_file=None) has nothing on disk;
    its resolved verdict must not delete anything."""
    deltaports, diffs_dir = _setup_diffs_tree(tmp_path, {"a.diff": "a"})
    from dportsv3.agent import worker as _w
    monkeypatch.setattr(_w, "env_paths",
                        lambda env: _CleanupPaths(deltaports))
    _write_convert_backing("b-inline", [("op:abc123", None)])
    queue_root = tmp_path / "queue"
    queue_root.mkdir()
    deleted = cleanup_resolved_deferred_patches(
        env="t", origin="lang/foo",
        verdicts=[_verdict("op:abc123", "regenerated")],
        queue_root=queue_root, job_id="j-1", bundle_id="b-inline",
    )
    assert deleted == []
    assert (diffs_dir / "a.diff").exists()  # untouched


def test_cleanup_silent_on_missing_file(tmp_path, monkeypatch, saved_store):
    deltaports, _ = _setup_diffs_tree(tmp_path, {})
    from dportsv3.agent import worker as _w
    monkeypatch.setattr(_w, "env_paths",
                        lambda env: _CleanupPaths(deltaports))
    _write_convert_backing("b-missing", [("diffs/a.diff", "diffs/a.diff")])
    queue_root = tmp_path / "queue"
    queue_root.mkdir()
    deleted = cleanup_resolved_deferred_patches(
        env="t", origin="lang/foo",
        verdicts=[_verdict("diffs/a.diff", "dropped")],
        queue_root=queue_root, job_id="j-1", bundle_id="b-missing",
    )
    assert deleted == []


def test_cleanup_refuses_path_escape(tmp_path, monkeypatch, saved_store):
    deltaports, diffs_dir = _setup_diffs_tree(
        tmp_path,
        {"keep.diff": "keep"},
    )
    outside = tmp_path / "outside-the-port.diff"
    outside.write_text("nope")

    from dportsv3.agent import worker as _w
    monkeypatch.setattr(_w, "env_paths",
                        lambda env: _CleanupPaths(deltaports))
    # backing_file carries the escape attempts; the path-safety guard
    # in cleanup must refuse each.
    _write_convert_backing("b-escape", [
        ("p1", "diffs/../../../outside-the-port.diff"),
        ("p2", "/etc/passwd"),
    ])
    queue_root = tmp_path / "queue"
    queue_root.mkdir()
    verdicts = [
        _verdict("p1", "dropped"),
        _verdict("p2", "dropped"),
    ]
    deleted = cleanup_resolved_deferred_patches(
        env="t", origin="lang/foo", verdicts=verdicts,
        queue_root=queue_root, job_id="j-1", bundle_id="b-escape",
    )
    assert deleted == []
    assert (diffs_dir / "keep.diff").exists()
    assert outside.exists()


def test_cleanup_handles_empty_verdicts(tmp_path):
    queue_root = tmp_path / "queue"
    queue_root.mkdir()
    assert cleanup_resolved_deferred_patches(
        env="t", origin="lang/foo", verdicts=[],
        queue_root=queue_root, job_id=None,
    ) == []
