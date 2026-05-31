"""Integration tests for the playbook library at runtime.

Distinct from ``tests/test_playbooks.py`` (unit tests against the
selector + parser) — these tests exercise the wiring between the
runner's payload builders and the live ``docs/agent-playbooks/``
content, plus the `playbooks_selected` activity-log telemetry.

Covers:
- build_patch_payload with a classification-tagged bundle attaches
  the matching error-* playbook content (end-to-end, against the
  real on-disk catalog)
- process_triage_job / process_patch_job seed `job["queue_root"]`
  so the payload-build telemetry can find it (regression for the
  audit finding that `_log_playbook_selection` was dead-on-arrival)
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dportsv3.agent import playbooks as pb_mod
from dportsv3.agent import runner as runner_mod
from dportsv3.agent.playbooks import find_playbooks_dir, load_playbooks


# -----------------------------------------------------------------
# End-to-end: build_patch_payload pulls the right playbook
# -----------------------------------------------------------------


def _write_triage_md(bundle_dir: Path, classification: str) -> None:
    (bundle_dir / "analysis").mkdir(parents=True, exist_ok=True)
    (bundle_dir / "analysis" / "triage.md").write_text(
        f"## Classification\n{classification}\n\n"
        f"## Confidence\nhigh\n"
    )
    # Step 36-5: build_patch_payload reads the typed TriageResult
    # via load_phase_result, not the markdown. Write both so tests
    # that grep the markdown for human-readable assertions stay
    # representative while the typed consumer gets its source.
    import json as _json
    (bundle_dir / "analysis" / "triage_result.json").write_text(
        _json.dumps({
            "schema_version": 1,
            "classification": classification,
            "confidence": "high",
            "root_cause": "",
            "evidence_excerpt": "",
            "error_signature": None,
            "tier": "ASSIST",
            "classifier_version": "triage-v1",
            "tokens_prompt": 0,
            "tokens_completion": 0,
            "tokens_total": 0,
            "model": "test-model",
        })
    )


def test_build_triage_payload_attaches_wildcard_playbooks_only(
    tmp_path: Path, monkeypatch,
):
    """Triage flow doesn't know a classification yet (it's the thing
    being determined). Wildcard entries (classifications: []) still
    attach; classification-tagged entries don't. Validates the
    Option-D mixed-trigger curation that resolved review issue #3."""
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()

    monkeypatch.setattr(runner_mod, "artifact_store_get", lambda *a, **kw: None)
    monkeypatch.setattr(runner_mod, "tracker_artifact_get", lambda *a, **kw: None)
    monkeypatch.setattr(runner_mod, "bundle_artifact_list", lambda *a, **kw: [])
    monkeypatch.setattr(runner_mod, "port_bundle_history", lambda *a, **kw: [])
    monkeypatch.setattr(runner_mod, "get_user_context",
                        lambda *a, **kw: (None, 0))

    playbooks_dir = find_playbooks_dir()
    job = {"origin": "category/x", "target": "@main"}
    payload = runner_mod.build_triage_payload(bundle_dir, playbooks_dir, job)

    # The wildcard cross-cutting entry attaches at triage time.
    assert "Agent Playbooks" in payload
    # Classification-tagged entries do NOT attach (no classification known).
    assert "Orphaned" not in payload  # error-plist-mismatch signature
    assert "blacklist.h" not in payload  # error-freebsd-only-features signature


def test_build_patch_payload_attaches_classification_matching_playbook(
    tmp_path: Path, monkeypatch,
):
    """A patch payload built against the live agent-playbooks catalog
    should embed the body of the error-* entry whose
    triggers.classifications matches the bundle's prior triage."""
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    _write_triage_md(bundle_dir, "plist-error")

    # Stub out artifact-store + tracker so build_patch_payload
    # operates from disk only. Same shape as test_patch_payload_parity.
    monkeypatch.setattr(runner_mod, "artifact_store_get", lambda *a, **kw: None)
    monkeypatch.setattr(runner_mod, "tracker_artifact_get", lambda *a, **kw: None)
    monkeypatch.setattr(runner_mod, "bundle_artifact_list", lambda *a, **kw: [])
    monkeypatch.setattr(runner_mod, "port_bundle_history", lambda *a, **kw: [])
    monkeypatch.setattr(runner_mod, "get_user_context",
                        lambda *a, **kw: (None, 0))
    from dportsv3.agent.decision import PortHistory
    monkeypatch.setattr(
        runner_mod, "_load_port_history",
        lambda target, origin, window_hours: PortHistory.empty(target, origin),
    )

    playbooks_dir = find_playbooks_dir()
    assert playbooks_dir is not None

    job = {"origin": "category/x", "target": "@main"}
    payload = runner_mod.build_patch_payload(bundle_dir, playbooks_dir, job)

    # The error-plist-mismatch playbook declares
    # `triggers.classifications: [plist-error, pkg-format]` so it
    # should fire for a plist-error bundle.
    assert "Agent Playbooks" in payload, (
        "expected playbooks header in the assembled patch payload"
    )
    # Match on the entry's title (case-insensitive substring) rather
    # than the filename, since the section renders titles not paths.
    assert "plist" in payload.lower()


def test_build_patch_payload_attaches_only_wildcard_playbooks_when_classification_unmatched(
    tmp_path: Path, monkeypatch,
):
    """A classification not matched by any classification-tagged
    entry still pulls in any wildcard entries (classifications: []).
    Wildcard semantics: the entry's classification trigger is empty,
    so it matches regardless of context — cross-cutting guidance.

    The classification-tagged entries (plist, freebsd-only,
    dragonfly-source) all skip in this case."""
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    _write_triage_md(bundle_dir, "no-such-classification")

    monkeypatch.setattr(runner_mod, "artifact_store_get", lambda *a, **kw: None)
    monkeypatch.setattr(runner_mod, "tracker_artifact_get", lambda *a, **kw: None)
    monkeypatch.setattr(runner_mod, "bundle_artifact_list", lambda *a, **kw: [])
    monkeypatch.setattr(runner_mod, "port_bundle_history", lambda *a, **kw: [])
    monkeypatch.setattr(runner_mod, "get_user_context",
                        lambda *a, **kw: (None, 0))
    from dportsv3.agent.decision import PortHistory
    monkeypatch.setattr(
        runner_mod, "_load_port_history",
        lambda target, origin, window_hours: PortHistory.empty(target, origin),
    )

    playbooks_dir = find_playbooks_dir()
    job = {"origin": "category/x", "target": "@main"}
    payload = runner_mod.build_patch_payload(bundle_dir, playbooks_dir, job)

    # Wildcard entries (the dops-preference cross-cutting guidance)
    # still attach — section header present.
    assert "Agent Playbooks" in payload
    # The wildcard entry's signature appears in the body.
    assert "dops" in payload.lower() and "static-patch" in payload.lower() or \
           "Static patch" in payload, (
        "expected the cross-cutting dops-preference entry to attach "
        "as the wildcard match"
    )
    # The classification-tagged entries do NOT appear.
    assert "Orphaned" not in payload  # error-plist-mismatch signature
    assert "blacklist.h" not in payload  # error-freebsd-only-features signature


# -----------------------------------------------------------------
# Telemetry: playbooks_selected actually fires
# -----------------------------------------------------------------


def test_build_patch_payload_emits_playbooks_selected_activity_row(
    tmp_path: Path, monkeypatch,
):
    """Regression for the audit finding that the telemetry was
    dead-on-arrival because queue_root wasn't in the job dict.
    With process_*_job now seeding job['queue_root'], the activity
    row fires from build_patch_payload."""
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    _write_triage_md(bundle_dir, "plist-error")

    monkeypatch.setattr(runner_mod, "artifact_store_get", lambda *a, **kw: None)
    monkeypatch.setattr(runner_mod, "tracker_artifact_get", lambda *a, **kw: None)
    monkeypatch.setattr(runner_mod, "bundle_artifact_list", lambda *a, **kw: [])
    monkeypatch.setattr(runner_mod, "port_bundle_history", lambda *a, **kw: [])
    monkeypatch.setattr(runner_mod, "get_user_context",
                        lambda *a, **kw: (None, 0))
    from dportsv3.agent.decision import PortHistory
    monkeypatch.setattr(
        runner_mod, "_load_port_history",
        lambda target, origin, window_hours: PortHistory.empty(target, origin),
    )

    rows: list[dict] = []
    monkeypatch.setattr(
        runner_mod, "activity_log",
        lambda queue_root, stage, message, **kw: rows.append(
            {"stage": stage, "message": message, "queue_root": queue_root, **kw}
        ),
    )

    playbooks_dir = find_playbooks_dir()
    job = {
        "origin": "category/x",
        "target": "@main",
        # Production callers (process_patch_job) seed these — mirror
        # that here so the test exercises the wired-up path.
        "queue_root": str(tmp_path / "queue"),
        "job_id": "20260527-000000Z-main-x-1.job",
    }
    runner_mod.build_patch_payload(bundle_dir, playbooks_dir, job)

    selected = [r for r in rows if r["stage"] == "playbooks_selected"]
    assert len(selected) == 1, rows
    # job_id must reach activity_log so the row is visible to
    # `tracker get-activity --job ID`. Without it the row lands
    # with NULL job_id and the Step-27 telemetry signal is invisible.
    assert selected[0]["job_id"] == "20260527-000000Z-main-x-1.job", selected[0]
    extra = selected[0]["extra"]
    assert extra["role"] == "patch"
    assert extra["origin"] == "category/x"
    # Includes at least the plist entry (which matches plist-error).
    assert any("plist" in name for name in extra["included"]), extra


def test_build_patch_payload_telemetry_silent_when_queue_root_absent(
    tmp_path: Path, monkeypatch,
):
    """When the job dict has no queue_root (parity-test path,
    out-of-band builder callers), telemetry silently no-ops rather
    than raising. The activity_log function still exists; it just
    isn't called."""
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    _write_triage_md(bundle_dir, "plist-error")

    monkeypatch.setattr(runner_mod, "artifact_store_get", lambda *a, **kw: None)
    monkeypatch.setattr(runner_mod, "tracker_artifact_get", lambda *a, **kw: None)
    monkeypatch.setattr(runner_mod, "bundle_artifact_list", lambda *a, **kw: [])
    monkeypatch.setattr(runner_mod, "port_bundle_history", lambda *a, **kw: [])
    monkeypatch.setattr(runner_mod, "get_user_context",
                        lambda *a, **kw: (None, 0))
    from dportsv3.agent.decision import PortHistory
    monkeypatch.setattr(
        runner_mod, "_load_port_history",
        lambda target, origin, window_hours: PortHistory.empty(target, origin),
    )

    rows: list[dict] = []
    monkeypatch.setattr(
        runner_mod, "activity_log",
        lambda queue_root, stage, message, **kw: rows.append({"stage": stage}),
    )

    playbooks_dir = find_playbooks_dir()
    job = {"origin": "category/x", "target": "@main"}  # no queue_root
    runner_mod.build_patch_payload(bundle_dir, playbooks_dir, job)

    assert not [r for r in rows if r["stage"] == "playbooks_selected"], rows


# -----------------------------------------------------------------
# Smoke: every intent-* playbook is reachable via intent_reference
# -----------------------------------------------------------------


def test_build_convert_payload_attaches_convert_playbooks(
    tmp_path: Path, monkeypatch,
):
    """Convert payload bulk-attaches every entry with
    `flows: [convert]`. The two convert-*.md entries authored in 27e
    should appear in the assembled payload."""
    from dportsv3.agent import convert as convert_mod
    repo = tmp_path / "repo"
    (repo / "ports" / "devel" / "x").mkdir(parents=True)
    (repo / "ports" / "devel" / "x" / "Makefile.DragonFly").write_text(
        "USES+= pkgconfig\n"
    )

    classified = {"origin": "devel/x", "bucket": "needs-judgment",
                  "classification_reasons": []}
    det_result = {
        "status": "needs-judgment", "parse_ok": True,
        "check_ok": True, "plan_ok": True, "deterministic_ok": True,
        "errors": ["unsupported_line: .if defined(FOO)"],
    }
    playbooks_dir = find_playbooks_dir()
    selection = load_playbooks(playbooks_dir, role="convert")

    payload = convert_mod.build_convert_payload(
        origin="devel/x", repo_root=repo,
        classified_record=classified, deterministic_result=det_result,
        dops_quickref_text="(quickref placeholder)",
        playbooks_text=selection.text,
    )
    # Both convert entries land.
    assert "Classifying a patch's domain" in payload
    assert "Picking the `target` directive" in payload
    # The wildcard error entry (flows: [..., convert]) also lands.
    assert "Static patch fails after upstream version bump" in payload


def test_build_patch_payload_attaches_detected_toolchain_playbook(
    tmp_path: Path, monkeypatch,
):
    """End-to-end: a bundle with port/Makefile declaring
    `USES=cmake` should pull in toolchain-cmake.md via Step 19a's
    detect_toolchains() feeding load_playbooks's `toolchains`
    trigger. Validates the 27f wiring from runner → playbooks
    → selector."""
    bundle_dir = tmp_path / "bundle"
    (bundle_dir / "port").mkdir(parents=True)
    (bundle_dir / "port" / "Makefile").write_text(
        "PORTNAME=foo\nUSES= cmake\n"
    )
    _write_triage_md(bundle_dir, "compile-error")

    monkeypatch.setattr(runner_mod, "artifact_store_get", lambda *a, **kw: None)
    monkeypatch.setattr(runner_mod, "tracker_artifact_get", lambda *a, **kw: None)
    monkeypatch.setattr(runner_mod, "bundle_artifact_list", lambda *a, **kw: [])
    monkeypatch.setattr(runner_mod, "port_bundle_history", lambda *a, **kw: [])
    monkeypatch.setattr(runner_mod, "get_user_context",
                        lambda *a, **kw: (None, 0))
    from dportsv3.agent.decision import PortHistory
    monkeypatch.setattr(
        runner_mod, "_load_port_history",
        lambda target, origin, window_hours: PortHistory.empty(target, origin),
    )

    playbooks_dir = find_playbooks_dir()
    job = {"origin": "category/foo", "target": "@main"}
    payload = runner_mod.build_patch_payload(bundle_dir, playbooks_dir, job)

    # toolchain-cmake.md title is "CMake — usual suspects on DragonFly"
    assert "CMake — usual suspects" in payload, (
        "expected toolchain-cmake.md to attach via detected USES=cmake"
    )
    # toolchain-autoconf.md should NOT fire (the Makefile didn't
    # declare autoreconf or GNU_CONFIGURE).
    assert "Autoconf — usual suspects" not in payload


def test_intent_reference_attaches_every_intent_playbook():
    """Round-trip: for each intent type in INTENT_TYPES, calling
    intent_reference returns the schema AND at least one playbook
    whose triggers.intents declares that type. Catches a future
    intent being added without its recipe being wired."""
    from dportsv3.agent import worker
    from dportsv3.agent.edit_intent import INTENT_TYPES

    for intent_type in INTENT_TYPES:
        result = worker.intent_reference("test-env", intent_type)
        assert result["ok"] is True, (intent_type, result)
        assert result["schema"]["title"] == intent_type
        assert len(result["playbooks"]) >= 1, (
            f"intent_type={intent_type} has no matching playbook in the "
            f"live catalog; add docs/agent-playbooks/intent-{intent_type}.md"
        )
