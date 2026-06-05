"""Step 36 producer integration tests.

Verifies the three producer harness functions actually write the
typed ``analysis/<phase>_result.json`` with the expected shape. The
producers are best-effort (try/except: pass on the convert path,
silent on the write helpers), so without explicit assertions a
silently-broken producer would never break CI — the failure would
only surface in a field bundle analysis.

Companion to ``test_phase_result.py`` which covers the schema
contract end of the same pipe.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from dportsv3.agent.phase_result import (
    ConvertResult,
    PatchResult,
    TriageResult,
    load_phase_result,
)


# ---------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------


@pytest.fixture
def disk_bundle(tmp_path):
    """A filesystem-mode bundle: tmp_path as bundle_dir, no bundle_id.
    Producers fall back to writing directly under tmp_path/analysis/
    when bundle_id is None — simplest assertion surface."""
    (tmp_path / "analysis").mkdir(parents=True, exist_ok=True)
    return tmp_path


def _usage(prompt: int = 0, completion: int = 0, total: int = 0) -> Any:
    return SimpleNamespace(
        prompt_tokens=prompt,
        completion_tokens=completion,
        total_tokens=total,
    )


# ---------------------------------------------------------------------
# 36-2: triage producer
# ---------------------------------------------------------------------


def test_triage_producer_writes_typed_result(disk_bundle):
    """``_write_triage_audit_harness`` must persist a typed
    TriageResult with classification, confidence, root_cause,
    evidence_excerpt, and token spend extracted from the inputs."""
    from dportsv3.agent.runner import _write_triage_audit_harness

    # The producer reads back analysis/triage.md to lift the
    # Root Cause + Evidence sections via md_section. Provide one.
    (disk_bundle / "analysis" / "triage.md").write_text(
        "## Classification\npatch-error\n\n"
        "## Confidence\nhigh\n\n"
        "## Root Cause\nfailing hunk on Makefile.in (upstream 1.52)\n\n"
        "## Evidence\ncc: error: redirect unexpected\n"
    )
    (disk_bundle / "logs").mkdir(exist_ok=True)
    (disk_bundle / "logs" / "errors.txt").write_text(
        "[hook] ports/devel/foo: build failed\n"
    )

    triage_loop_result = SimpleNamespace(
        classification="patch-error",
        confidence="high",
        snippet_rounds=0,
        usage=_usage(prompt=12_000, completion=345, total=12_345),
    )

    _write_triage_audit_harness(
        disk_bundle, None, triage_loop_result, "anthropic/claude-sonnet-4",
    )

    loaded = load_phase_result(disk_bundle, None, "triage", TriageResult)
    assert loaded is not None
    assert loaded.classification == "patch-error"
    assert loaded.confidence == "high"
    assert "Makefile.in" in loaded.root_cause
    assert "redirect unexpected" in loaded.evidence_excerpt
    assert loaded.tokens_prompt == 12_000
    assert loaded.tokens_completion == 345
    assert loaded.tokens_total == 12_345
    assert loaded.model == "anthropic/claude-sonnet-4"
    assert loaded.classifier_version == "triage-v1"
    # error_signature is sha256[:16] of the first non-empty line of
    # errors.txt — origin-agnostic short hash. Confirm it's set, not
    # None, when errors.txt is present.
    assert loaded.error_signature is not None
    assert len(loaded.error_signature) == 16


# ---------------------------------------------------------------------
# 36-3: patch producer
# ---------------------------------------------------------------------


def test_patch_producer_writes_typed_result(disk_bundle):
    """``_write_patch_audit_harness`` must persist a typed
    PatchResult alongside the existing rebuild_proof.json /
    patch_audit.json."""
    from dportsv3.agent.runner import _write_patch_audit_harness

    patch_loop_result = SimpleNamespace(
        status="success",
        final_text=(
            "## Patch Summary\nEdits applied.\n\n"
            "## Rebuild Proof (JSON)\n```json\n"
            '{"rebuild_ok": true, "origin": "devel/foo"}\n```\n'
        ),
        usage=_usage(prompt=120_000, completion=8_000, total=128_000),
        attempts=[
            SimpleNamespace(attempt=1, tokens=64_000, rebuild_ok=False),
            SimpleNamespace(attempt=2, tokens=64_000, rebuild_ok=True),
        ],
        proof={"rebuild_ok": True, "origin": "devel/foo"},
    )

    _write_patch_audit_harness(
        disk_bundle, None, patch_loop_result, "test-model",
    )

    loaded = load_phase_result(disk_bundle, None, "patch", PatchResult)
    assert loaded is not None
    assert loaded.rebuild_ok is True
    assert loaded.status == "success"
    assert loaded.attempts == 2
    assert loaded.tokens_prompt == 120_000
    assert loaded.tokens_completion == 8_000
    assert loaded.tokens_total == 128_000


# ---------------------------------------------------------------------
# 36-4: convert producer
# ---------------------------------------------------------------------


@pytest.fixture
def store_capture(monkeypatch):
    """Capture artifact-store puts for the convert producer tests.
    Convert writes via the artifact-store path (bundle_id branch),
    not the bundle_dir disk fallback used by the triage/patch
    integration tests above. Same fake_store pattern as
    test_phase_result.py."""
    captured: dict[tuple[str, str], bytes] = {}

    def fake_put(bundle_id, relpath, data, kind=None):
        captured[(bundle_id, relpath)] = data
        return True

    def fake_read(bundle_dir, bundle_id, relpath):
        if bundle_id is None:
            return None
        raw = captured.get((bundle_id, relpath))
        return raw.decode("utf-8") if raw is not None else None

    from dportsv3.agent import runner
    monkeypatch.setattr(runner, "artifact_store_put", fake_put)
    monkeypatch.setattr(runner, "read_bundle_text", fake_read)
    return captured


def test_convert_producer_success_shape(store_capture):
    from dportsv3.agent.runner import _write_convert_phase_result

    _write_convert_phase_result(
        bundle_id="b-success",
        status="verified",
        reapply_ok=True,
        reason_code=None,
        overlay_sha256="cafef00d" * 8,
        files_removed=["STATUS", "Makefile.DragonFly"],
        diag_tail=None,
        tokens_prompt=40_000,
        tokens_completion=2_500,
        tokens_total=42_500,
    )
    loaded = load_phase_result(None, "b-success", "convert", ConvertResult)
    assert loaded is not None
    assert loaded.status == "verified"
    assert loaded.reapply_ok is True
    assert loaded.reason_code is None
    assert loaded.overlay_sha256 == "cafef00d" * 8
    assert loaded.files_removed == ["STATUS", "Makefile.DragonFly"]
    assert loaded.tokens_total == 42_500


def test_convert_producer_failure_shape(store_capture):
    """The _verify_conversion._fail path emits status=<long status
    string>, reason_code=<short code>, reapply_ok=False, and
    diag_tail when supplied. Mirrors the live shape for
    reapply_failed / effective_ops_empty / env_commit_failed."""
    from dportsv3.agent.runner import _write_convert_phase_result

    _write_convert_phase_result(
        bundle_id="b-failure",
        status="reapply failed: rc=2 'pyexpat... missing'",
        reapply_ok=False,
        reason_code="reapply_failed",
        overlay_sha256="0badcafe" * 8,
        files_removed=[],
        diag_tail="modes: dops=1\n",
        tokens_prompt=80_000,
        tokens_completion=3_500,
        tokens_total=83_500,
    )
    loaded = load_phase_result(None, "b-failure", "convert", ConvertResult)
    assert loaded is not None
    assert loaded.reapply_ok is False
    assert loaded.reason_code == "reapply_failed"
    assert "reapply failed: rc=2" in loaded.status
    assert loaded.diag_tail == "modes: dops=1\n"
    # overlay_sha256 should reflect the *agent's* overlay even
    # though the failure path resets it — see 36-4 review fix.
    assert loaded.overlay_sha256 == "0badcafe" * 8


def test_convert_producer_skips_when_no_bundle_id(store_capture):
    """Operator-fired convert (no bundle_id) writes nothing —
    there's no destination, and a missing typed result is the
    documented "no upstream context available" shape."""
    from dportsv3.agent.runner import _write_convert_phase_result

    _write_convert_phase_result(
        bundle_id=None,
        status="verified", reapply_ok=True, reason_code=None,
        overlay_sha256=None, files_removed=[], diag_tail=None,
        tokens_prompt=0, tokens_completion=0, tokens_total=0,
    )
    assert store_capture == {}
