"""Step 36-1: schema + I/O helpers for typed phase results.

Covers schema round-trips, missing-file degradation, and
version-mismatch surfacing. Producer/consumer wiring (36-2..36-7) is
tested separately in the call-site tests; here we only exercise the
contract.
"""

from __future__ import annotations

import json
from dataclasses import asdict

import pytest

from dportsv3.agent.phase_result import (
    ConvertResult,
    PatchResult,
    PhaseResultVersionMismatch,
    TriageResult,
    load_phase_result,
    write_phase_result,
)


@pytest.fixture
def fake_store(monkeypatch):
    """A dict-backed stand-in for the bundle artifact store.

    Keyed by ``(bundle_id, relpath) → bytes``. Returns the same
    object the test mutates so assertions can peek at writes.
    The runner's helpers are looked up via late binding inside
    ``write_phase_result`` / ``load_phase_result``, so substituting
    attributes on ``dportsv3.agent.runner`` works without import
    loops.
    """
    store: dict[tuple[str, str], bytes] = {}

    def fake_put(bundle_id, relpath, data, kind=None):
        store[(bundle_id, relpath)] = data
        return True

    def fake_read(bundle_dir, bundle_id, relpath):
        if bundle_id is None:
            return None
        raw = store.get((bundle_id, relpath))
        return raw.decode("utf-8") if raw is not None else None

    from dportsv3.agent import runner
    monkeypatch.setattr(runner, "artifact_store_put", fake_put)
    monkeypatch.setattr(runner, "read_bundle_text", fake_read)
    return store


# ---------------------------------------------------------------------
# Round-trip per phase
# ---------------------------------------------------------------------


def test_triage_result_round_trip(fake_store):
    original = TriageResult(
        classification="patch-error",
        confidence="high",
        root_cause="failing hunk on Makefile.in",
        evidence_excerpt="cc: error: bla\n...",
        error_signature="abc1234567890def",
        tier="ASSIST",
        classifier_version="triage-prompt-v3",
        tokens_prompt=10_000,
        tokens_completion=2_345,
        tokens_total=12_345,
        model="anthropic/claude-sonnet-4",
    )
    write_phase_result("b1", "triage", original)

    loaded = load_phase_result(None, "b1", "triage", TriageResult)
    assert loaded == original


def test_convert_result_round_trip(fake_store):
    original = ConvertResult(
        status="reapply_failed",
        reapply_ok=False,
        reason_code="reapply_failed",
        overlay_sha256="deadbeef" * 8,
        files_removed=["STATUS", "Makefile.DragonFly"],
        diag_tail="modes: dops=1\n",
        tokens_prompt=35_000,
        tokens_completion=7_000,
        tokens_total=42_000,
    )
    write_phase_result("b1", "convert", original)

    loaded = load_phase_result(None, "b1", "convert", ConvertResult)
    assert loaded == original


def test_patch_result_round_trip(fake_store):
    original = PatchResult(
        rebuild_ok=True,
        status="success",
        attempts=2,
        tokens_prompt=120_000,
        tokens_completion=10_000,
        tokens_total=130_000,
    )
    write_phase_result("b1", "patch", original)

    loaded = load_phase_result(None, "b1", "patch", PatchResult)
    assert loaded == original


# ---------------------------------------------------------------------
# Graceful degradation
# ---------------------------------------------------------------------


def test_load_missing_returns_none(fake_store):
    """A bundle that never wrote a phase result loads as None — the
    expected shape for legacy bundles and for downstream phases that
    consult an upstream result that hasn't been produced yet (e.g.
    convert reading triage on an operator-fired convert with no
    bundle)."""
    assert load_phase_result(None, "b1", "triage", TriageResult) is None


def test_load_with_empty_bundle_id_returns_none(fake_store):
    """Operator-fired convert: no bundle attached → no result. Same
    "degrade gracefully" path consumers take for legacy bundles."""
    assert load_phase_result(None, "", "triage", TriageResult) is None
    assert load_phase_result(None, None, "triage", TriageResult) is None


# ---------------------------------------------------------------------
# Version mismatch is loud, not silent
# ---------------------------------------------------------------------


def test_version_mismatch_raises(fake_store):
    """A future v2-shaped artifact loaded by a v1 consumer is a
    contract violation; raise so the operator notices rather than
    silently dropping fields."""
    payload = asdict(TriageResult(
        classification="patch-error", confidence="high",
        root_cause="", evidence_excerpt="", error_signature=None,
        tier="ASSIST", classifier_version="v3",
        tokens_prompt=0, tokens_completion=0, tokens_total=0,
        model="m",
    ))
    payload["schema_version"] = 999
    fake_store[("b1", "analysis/triage_result.json")] = (
        json.dumps(payload).encode("utf-8")
    )

    with pytest.raises(PhaseResultVersionMismatch) as exc:
        load_phase_result(None, "b1", "triage", TriageResult)
    assert exc.value.phase == "triage"
    assert exc.value.got == 999
    assert exc.value.expected == 1


def test_missing_schema_version_treated_as_mismatch(fake_store):
    """Legacy artifact without a schema_version key (e.g. the
    pre-Step-36 ``triage.json`` shape) must not be silently coerced
    into a v1 result — schemas differ. Raise so the producer is
    fixed."""
    payload = {"classification": "patch-error"}
    fake_store[("b1", "analysis/triage_result.json")] = (
        json.dumps(payload).encode("utf-8")
    )

    with pytest.raises(PhaseResultVersionMismatch):
        load_phase_result(None, "b1", "triage", TriageResult)


# ---------------------------------------------------------------------
# Forward-compat: extra unknown fields on a matching version are
# ignored by the constructor (mismatch fires first; this is the
# defensive belt for "same-version, future field added").
# ---------------------------------------------------------------------


def test_unknown_field_at_same_version_ignored(fake_store):
    payload = asdict(PatchResult(
        rebuild_ok=True, status="success", attempts=1,
        tokens_prompt=8, tokens_completion=2, tokens_total=10,
    ))
    payload["future_field"] = "ignored"
    fake_store[("b1", "analysis/patch_result.json")] = (
        json.dumps(payload).encode("utf-8")
    )

    loaded = load_phase_result(None, "b1", "patch", PatchResult)
    assert loaded is not None
    assert loaded.rebuild_ok is True


# ---------------------------------------------------------------------
# Producer-side write failure surfaces
# ---------------------------------------------------------------------


def test_write_failure_raises(monkeypatch):
    """artifact_store_put returning False (store unavailable) must
    raise — silent loss is the bug we're trying to close."""
    from dportsv3.agent import runner
    monkeypatch.setattr(
        runner, "artifact_store_put",
        lambda *_a, **_kw: False,
    )

    with pytest.raises(RuntimeError, match="failed to write"):
        write_phase_result(
            "b1", "triage",
            TriageResult(
                classification="x", confidence="low", root_cause="",
                evidence_excerpt="", error_signature=None,
                tier="MANUAL", classifier_version="v",
                tokens_prompt=0, tokens_completion=0, tokens_total=0,
                model="m",
            ),
        )


# ---------------------------------------------------------------------
# bundle_dir-based lookup (filesystem-mode bundles)
# ---------------------------------------------------------------------


def test_load_via_bundle_dir(tmp_path):
    """When the bundle lives on the local filesystem (tests,
    operator-fired paths), load_phase_result resolves via bundle_dir
    using the same routing as read_bundle_text."""
    analysis = tmp_path / "analysis"
    analysis.mkdir()
    payload = asdict(TriageResult(
        classification="plist-error", confidence="medium",
        root_cause="", evidence_excerpt="", error_signature=None,
        tier="ASSIST", classifier_version="v1",
        tokens_prompt=10, tokens_completion=5, tokens_total=15,
        model="m",
    ))
    (analysis / "triage_result.json").write_text(json.dumps(payload))

    loaded = load_phase_result(tmp_path, None, "triage", TriageResult)
    assert loaded is not None
    assert loaded.classification == "plist-error"
