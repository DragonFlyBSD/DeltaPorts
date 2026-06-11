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
