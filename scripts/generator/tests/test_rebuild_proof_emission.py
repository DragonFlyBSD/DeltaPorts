"""Step 26 (observability): rebuild_proof.json must always exist
on terminal patch states so operators skimming the artifact list
can distinguish "agent gave up cleanly" from "agent crashed."

Regression for the redis smoke-test finding (databases_redis-
20260526-205826Z): patch_audit.json was present with status=
budget-exhausted but no rebuild_proof.json was written, because
the writer guarded the emit on result.proof being non-None.
"""

from __future__ import annotations

import json
from pathlib import Path

from dportsv3.agent import runner as runner_mod
from dportsv3.agent.attempt_loop import AttemptInfo, PatchResult
from dportsv3.agent.llm import Usage


def _result(status: str, proof=None, attempts=2):
    return PatchResult(
        status=status,
        final_text="",
        usage=Usage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
        attempts=[
            AttemptInfo(attempt=i + 1, tokens=1000, rebuild_ok=False)
            for i in range(attempts)
        ],
        proof=proof,
    )


def test_budget_exhausted_emits_synthetic_rebuild_proof(tmp_path: Path):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    runner_mod._write_patch_audit_harness(
        bundle, None, _result("budget-exhausted"), model="m",
    )
    proof_path = bundle / "analysis" / "rebuild_proof.json"
    assert proof_path.is_file(), "rebuild_proof.json must be written"
    proof = json.loads(proof_path.read_text())
    assert proof["rebuild_ok"] is False
    assert proof["status"] == "budget-exhausted"
    assert proof["synthetic"] is True
    assert proof["attempts"] == 2


def test_needs_help_emits_synthetic_rebuild_proof(tmp_path: Path):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    runner_mod._write_patch_audit_harness(
        bundle, None, _result("needs-help"), model="m",
    )
    proof = json.loads(
        (bundle / "analysis" / "rebuild_proof.json").read_text()
    )
    assert proof["status"] == "needs-help"
    assert proof["synthetic"] is True


def test_success_writes_real_proof_verbatim(tmp_path: Path):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    real = {"rebuild_ok": True, "build_command": "make", "extra": "value"}
    runner_mod._write_patch_audit_harness(
        bundle, None, _result("success", proof=real), model="m",
    )
    proof = json.loads(
        (bundle / "analysis" / "rebuild_proof.json").read_text()
    )
    # Real proof preserved; no synthetic flag.
    assert proof == real
    assert "synthetic" not in proof


def test_terminal_with_partial_proof_uses_real_proof(tmp_path: Path):
    """If the LLM emitted a Rebuild Proof block (e.g. with
    rebuild_ok=false) on its final attempt, result.proof carries it
    and we should write THAT verbatim rather than synthesizing."""
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    partial = {"rebuild_ok": False, "build_command": "make", "stage": "build"}
    runner_mod._write_patch_audit_harness(
        bundle, None,
        _result("budget-exhausted", proof=partial),
        model="m",
    )
    proof = json.loads(
        (bundle / "analysis" / "rebuild_proof.json").read_text()
    )
    assert proof == partial
