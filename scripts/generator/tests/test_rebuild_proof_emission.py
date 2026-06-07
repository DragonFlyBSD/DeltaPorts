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


def test_success_proof_keeps_verdict_stamps_metadata(tmp_path: Path):
    """M1: only rebuild_ok comes from the LLM. The harness stamps the
    timestamp + origin from real data and drops fabricated metadata it
    can't get authoritatively (the dsynth profile is the chroot's
    $DPORTS_DSYNTH_PROFILE, not knowable here — don't replace one guess
    with another)."""
    from datetime import datetime
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    # LLM-authored metadata is bogus — the literal prompt example.
    real = {"rebuild_ok": True, "build_command": "make",
            "dsynth_profile": "DragonFly",
            "timestamp_utc": "2026-05-18T20:00:00Z", "extra": "value"}
    runner_mod._write_patch_audit_harness(
        bundle, None, _result("success", proof=real), model="m",
        origin="cat/port",
    )
    proof = json.loads(
        (bundle / "analysis" / "rebuild_proof.json").read_text()
    )
    assert proof["rebuild_ok"] is True       # the agent's verdict survives
    assert proof["extra"] == "value"         # non-owned fields survive
    assert "synthetic" not in proof
    assert proof["origin"] == "cat/port"     # code-stamped
    assert proof["timestamp_utc"] != "2026-05-18T20:00:00Z"  # not the example
    datetime.fromisoformat(proof["timestamp_utc"])           # real, parseable
    # build_command = the real env-var-templated form (not "make", not a
    # hardcoded profile); standalone fabricated profile field dropped
    assert proof["build_command"] == 'dsynth -S -y -p "$DPORTS_DSYNTH_PROFILE" build cat/port'
    assert "dsynth_profile" not in proof


def test_terminal_partial_proof_keeps_verdict_stamps_metadata(tmp_path: Path):
    """A partial proof (rebuild_ok=false) keeps its verdict + non-owned
    fields; the harness stamps timestamp/origin and drops fabricated
    profile/command."""
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    partial = {"rebuild_ok": False, "build_command": "make", "stage": "build"}
    runner_mod._write_patch_audit_harness(
        bundle, None,
        _result("budget-exhausted", proof=partial),
        model="m", origin="cat/port",
    )
    proof = json.loads(
        (bundle / "analysis" / "rebuild_proof.json").read_text()
    )
    assert proof["rebuild_ok"] is False                  # verdict preserved
    assert proof["stage"] == "build"                     # non-owned preserved
    assert proof["origin"] == "cat/port"
    # LLM's "make" replaced by the real templated command
    assert proof["build_command"] == 'dsynth -S -y -p "$DPORTS_DSYNTH_PROFILE" build cat/port'
    assert "timestamp_utc" in proof
