"""Tests for the intent log pipeline (plan Step 25e):

- worker accumulates per-(env, origin) intents on apply_intent.
- worker.drain_intent_log returns + clears the log.
- The runner harness function serializes the log to
  analysis/intent_log.json.
- The dev-env apply-and-build primitive accepts --intent-log and
  replays each intent via the in-process Translator.
- The verify-fix orchestrator prefers intent_log over the diff
  fallback.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error
from pathlib import Path
from types import SimpleNamespace

import pytest

from dportsv3.agent import tools, worker
from dportsv3.agent.overlay_state import (
    OverlayAssessment, OverlayRuleResult,
)


# Path: scripts/tools/dev-env on sys.path for the replay test.
_DEV_ENV_PKG = Path(__file__).resolve().parents[2] / "tools" / "dev-env"
if _DEV_ENV_PKG.is_dir() and str(_DEV_ENV_PKG) not in sys.path:
    sys.path.insert(0, str(_DEV_ENV_PKG))


def _make_workspace(tmp_path: Path, origin: str = "devel/foo") -> Path:
    ws = tmp_path / "DeltaPorts"
    port = ws / "ports" / origin
    port.mkdir(parents=True)
    subprocess.run(["git", "-C", str(ws), "init", "-q", "-b", "main"],
                   check=True)
    subprocess.run(["git", "-C", str(ws), "config", "user.email", "t@t"],
                   check=True)
    subprocess.run(["git", "-C", str(ws), "config", "user.name", "t"],
                   check=True)
    (ws / "README").write_text("baseline\n")
    subprocess.run(["git", "-C", str(ws), "add", "README"], check=True)
    subprocess.run(["git", "-C", str(ws), "commit", "-qm", "init"],
                   check=True)
    return ws


def _stub_assess(state="auto_safe_pending", action="defer_to_convert"):
    return OverlayAssessment(
        state=state, action=action,
        rules=(OverlayRuleResult("test_stub"),),
    )


@pytest.fixture
def env_with_workspace(tmp_path, monkeypatch):
    ws = _make_workspace(tmp_path)
    paths = SimpleNamespace(deltaports=ws, env_dir=tmp_path, writable=tmp_path)
    monkeypatch.setattr(worker, "env_paths", lambda env: paths)
    monkeypatch.setattr(worker, "assess_dops",
                        lambda env, origin: _stub_assess())
    # Stub baseline resolver so we don't shell out to a fake chroot.
    monkeypatch.setattr(worker, "_resolve_baseline_commit",
                        lambda env: "fake-baseline")
    # Drain any leftover log from a prior test.
    worker._INTENT_LOGS.clear()
    return ws


# --------------------------------------------------------------------
# Worker-side log accumulation + drain
# --------------------------------------------------------------------


class TestWorkerLogAccumulation:

    def test_apply_intent_appends_log_entry_on_success(
        self, env_with_workspace,
    ):
        # Stage a patch so drop_patch succeeds.
        ws = env_with_workspace
        patch = ws / "ports/devel/foo/dragonfly/patch-old.c"
        patch.parent.mkdir(parents=True)
        patch.write_text("--- a/x\n+++ b/x\n")
        subprocess.run(
            ["git", "-C", str(ws), "add", str(patch.relative_to(ws))],
            check=True,
        )
        subprocess.run(["git", "-C", str(ws), "commit", "-qm", "add"],
                       check=True)

        result = worker.apply_intent("test-env", "devel/foo", {
            "type": "drop_patch",
            "target": "dragonfly/patch-old.c",
            "reason": "obsolete",
        })
        assert result["ok"] is True

        log = worker.drain_intent_log("test-env", "devel/foo")
        assert log is not None
        assert len(log.intents) == 1
        assert log.intents[0].ok is True
        assert log.intents[0].intent["type"] == "drop_patch"
        # Drain clears state.
        assert worker.drain_intent_log("test-env", "devel/foo") is None

    def test_apply_intent_appends_log_entry_on_failure(
        self, env_with_workspace,
    ):
        # Drop a non-existent patch → ok=False.
        result = worker.apply_intent("test-env", "devel/foo", {
            "type": "drop_patch",
            "target": "dragonfly/never-existed.c",
            "reason": "x",
        })
        assert result["ok"] is False
        log = worker.drain_intent_log("test-env", "devel/foo")
        assert log is not None
        assert len(log.intents) == 1
        assert log.intents[0].ok is False
        assert "does not exist" in (log.intents[0].error or "")

    def test_log_serializes_to_canonical_shape(self, env_with_workspace):
        worker.apply_intent("test-env", "devel/foo", {
            "type": "drop_patch", "target": "x", "reason": "y",
        })
        log = worker.drain_intent_log("test-env", "devel/foo")
        doc = json.loads(log.to_json())
        assert doc["schema_version"] == 1
        assert doc["origin"] == "devel/foo"
        assert doc["mode_at_apply"] == "compat"
        assert doc["baseline_commit"] == "fake-baseline"
        assert len(doc["intents"]) == 1

    def test_intent_log_cap_zero_refuses_apply_substrate_unchanged(
        self, env_with_workspace, monkeypatch,
    ):
        """Review #2 fix: the cap check runs BEFORE the translator
        applies. cap=0 means the first intent is refused outright;
        substrate stays clean and the LLM sees ok=False +
        intent_log_full=True. The old shape ran the apply then
        flagged the overflow, which left an unrecorded substrate
        edit and corrupted verify's replay."""
        monkeypatch.setenv("DP_HARNESS_INTENT_MAX_COUNT", "0")
        ws = env_with_workspace
        patch = ws / "ports/devel/foo/dragonfly/patch-zero.c"
        patch.parent.mkdir(parents=True)
        patch.write_text("--- a/x\n+++ b/x\n")
        subprocess.run(
            ["git", "-C", str(ws), "add", str(patch.relative_to(ws))],
            check=True,
        )
        subprocess.run(["git", "-C", str(ws), "commit", "-qm", "add"],
                       check=True)

        result = worker.apply_intent("test-env", "devel/foo", {
            "type": "drop_patch", "target": "dragonfly/patch-zero.c",
            "reason": "x",
        })
        # Substrate is unchanged — patch still on disk.
        assert result["ok"] is False
        assert patch.exists()
        # Cap flag tells the LLM to escalate.
        assert result.get("intent_log_full") is True
        assert "log full" in result["error"].lower()

    def test_mode_drift_refused_within_transaction(
        self, env_with_workspace, monkeypatch,
    ):
        """Once a transaction starts in one mode, subsequent intents
        that resolve to a different mode are refused — prevents
        single-log mixed-flavor accumulation."""
        # Apply one intent in the default (compat) mode.
        r1 = worker.apply_intent("test-env", "devel/foo", {
            "type": "drop_patch",
            "target": "dragonfly/missing.c",  # ok=False, but logged
            "reason": "x",
        })
        assert r1["ok"] is False  # intent failed but transaction started

        # Flip the assess stub to return 'converted' (dops mode).
        monkeypatch.setattr(worker, "assess_dops",
                            lambda env, origin: _stub_assess(
                                state="converted", action="proceed_triage"))
        # Try another intent — same (env, origin). Mode now resolves
        # to dops; the existing log was started in compat. Refuse.
        r2 = worker.apply_intent("test-env", "devel/foo", {
            "type": "drop_patch",
            "target": "dragonfly/whatever.c", "reason": "y",
        })
        assert r2["ok"] is False
        assert r2["blocked_by"] == "transaction_mode_drift"
        assert r2["transaction_mode"] == "compat"
        assert r2["current_mode"] == "dops"

    def test_intent_log_size_cap_surfaces_to_tool_result(
        self, env_with_workspace, monkeypatch,
    ):
        """Review #2 fix: when the second intent would overflow the
        cap, the apply is refused BEFORE translator runs. ok=False,
        intent_log_full=True, and the substrate stays unchanged so
        verify's canonical-log replay stays consistent."""
        monkeypatch.setenv("DP_HARNESS_INTENT_MAX_COUNT", "1")
        # Stage a patch so the underlying drop_patch succeeds.
        ws = env_with_workspace
        patch = ws / "ports/devel/foo/dragonfly/patch-first.c"
        patch.parent.mkdir(parents=True)
        patch.write_text("--- a/x\n+++ b/x\n")
        subprocess.run(
            ["git", "-C", str(ws), "add", str(patch.relative_to(ws))],
            check=True,
        )
        subprocess.run(["git", "-C", str(ws), "commit", "-qm", "add"],
                       check=True)

        # First intent succeeds and fills the cap.
        r1 = worker.apply_intent("test-env", "devel/foo", {
            "type": "drop_patch", "target": "dragonfly/patch-first.c",
            "reason": "y",
        })
        assert r1["ok"] is True
        # Stage another patch for the second intent's substrate work.
        patch2 = ws / "ports/devel/foo/dragonfly/patch-second.c"
        patch2.write_text("--- a/x\n+++ b/x\n")
        subprocess.run(
            ["git", "-C", str(ws), "add", str(patch2.relative_to(ws))],
            check=True,
        )
        subprocess.run(["git", "-C", str(ws), "commit", "-qm", "add"],
                       check=True)
        # Second intent: cap full, apply refused, substrate untouched.
        result = worker.apply_intent("test-env", "devel/foo", {
            "type": "drop_patch", "target": "dragonfly/patch-second.c",
            "reason": "y",
        })
        # ok=False — the apply was refused.
        assert result["ok"] is False
        # Substrate unchanged: patch-second.c still on disk.
        assert patch2.exists()
        # The cap flag tells the LLM to escalate, not retry.
        assert result.get("intent_log_full") is True
        assert "exceeds" in result.get("intent_log_full_reason", "")

    def test_byte_cap_against_large_substrate_diff_reverts_substrate(
        self, env_with_workspace, monkeypatch,
    ):
        """Follow-up #3 regression: a small intent that produces a
        large substrate_diff must trigger the post-apply byte-cap
        check. The pre-apply check sees an empty diff and lets it
        through; the post-apply check catches it; and the substrate
        edit is reverted so the canonical-log invariant holds."""
        # Cap byte budget below what the generated diff will weigh.
        monkeypatch.setenv("DP_HARNESS_INTENT_MAX_BYTES", "2000")
        ws = env_with_workspace
        # Stage a patch with ~10 KB of content. drop_patch's
        # substrate_diff includes the deletion of every line, so
        # the diff itself will be > 10 KB — well past the 2 KB cap.
        patch = ws / "ports/devel/foo/dragonfly/patch-fat.c"
        patch.parent.mkdir(parents=True)
        big = "+ some patched line that is reasonably long\n" * 250
        patch.write_text("--- a/x\n+++ b/x\n" + big)
        subprocess.run(
            ["git", "-C", str(ws), "add", str(patch.relative_to(ws))],
            check=True,
        )
        subprocess.run(["git", "-C", str(ws), "commit", "-qm", "add"],
                       check=True)

        # Sanity: the intent payload itself is tiny, so phase-1
        # (no substrate_diff) would let this through. If this
        # assertion fails the test is no longer exercising phase-2.
        intent_dict = {
            "type": "drop_patch",
            "target": "dragonfly/patch-fat.c",
            "reason": "obsolete",
        }
        log_probe = worker._ensure_intent_log("test-env", "devel/foo", "compat")
        assert log_probe.would_overflow(intent_dict) is None, (
            "phase-1 should accept the small intent; if not, this "
            "test is exercising phase-1, not phase-2"
        )

        result = worker.apply_intent("test-env", "devel/foo", intent_dict)
        # Post-apply byte cap fired, substrate was reverted.
        assert result["ok"] is False
        assert result.get("intent_log_full") is True
        # patch-fat.c is back on disk — the drop was undone.
        assert patch.exists(), (
            "substrate revert failed: patch-fat.c should exist after "
            "the byte-cap refusal reverted the drop_patch edit"
        )
        # Log has no row for this attempt (canonical-log invariant).
        log = worker._ensure_intent_log("test-env", "devel/foo", "compat")
        assert len(log.intents) == 0


# --------------------------------------------------------------------
# Runner harness: _write_intent_log_harness
# --------------------------------------------------------------------


class TestRunnerIntentLogWrite:

    def test_writes_intent_log_to_bundle_dir(
        self, env_with_workspace, tmp_path,
    ):
        from dportsv3.agent.runner import _write_intent_log_harness

        worker.apply_intent("test-env", "devel/foo", {
            "type": "drop_patch", "target": "x", "reason": "y",
        })

        bundle_dir = tmp_path / "bundle"
        bundle_dir.mkdir()
        _write_intent_log_harness(bundle_dir, None, "test-env", "devel/foo")

        out = bundle_dir / "analysis" / "intent_log.json"
        assert out.is_file()
        doc = json.loads(out.read_text())
        assert doc["origin"] == "devel/foo"
        assert len(doc["intents"]) == 1
        # Drain happened; second write writes nothing.
        out.unlink()
        _write_intent_log_harness(bundle_dir, None, "test-env", "devel/foo")
        assert not out.exists()

    def test_no_op_when_no_log_present(self, tmp_path):
        from dportsv3.agent.runner import _write_intent_log_harness
        worker._INTENT_LOGS.clear()

        bundle_dir = tmp_path / "bundle"
        bundle_dir.mkdir()
        _write_intent_log_harness(bundle_dir, None, "no-env", "devel/foo")

        assert not (bundle_dir / "analysis" / "intent_log.json").exists()

    def test_serialize_failure_writes_tombstone(
        self, env_with_workspace, tmp_path, monkeypatch,
    ):
        """When IntentLog.to_json raises, the harness drops a
        .json.error tombstone with the failure detail instead of
        silently swallowing the failure."""
        from dportsv3.agent.runner import _write_intent_log_harness

        worker.apply_intent("test-env", "devel/foo", {
            "type": "drop_patch", "target": "x", "reason": "y",
        })
        # Make to_json blow up.
        log = worker._INTENT_LOGS[("test-env", "devel/foo")]
        def _bad(self_):
            raise RuntimeError("synthetic serialize fail")
        monkeypatch.setattr(type(log), "to_json", _bad)

        bundle_dir = tmp_path / "bundle"
        bundle_dir.mkdir()
        _write_intent_log_harness(bundle_dir, None, "test-env", "devel/foo")

        # No main artifact.
        assert not (bundle_dir / "analysis" / "intent_log.json").exists()
        # Tombstone exists and carries the error.
        tomb = bundle_dir / "analysis" / "intent_log.json.error"
        assert tomb.is_file()
        doc = json.loads(tomb.read_text())
        assert "synthetic serialize fail" in doc["error"]
        assert doc["intent_count_at_failure"] == 1


# --------------------------------------------------------------------
# Dev-env primitive: --intent-log replay
# --------------------------------------------------------------------


class TestApplyAndBuildIntentLog:

    def test_replay_intent_log_helper_happy(self, tmp_path):
        """The _replay_intent_log helper applies each intent against
        the workspace via the in-process Translator and returns
        (rc=0, applied_count, '')."""
        from dports_dev_env.cli import _replay_intent_log

        ws = _make_workspace(tmp_path)
        # Pre-stage a patch we can drop.
        patch = ws / "ports/devel/foo/dragonfly/patch-old.c"
        patch.parent.mkdir(parents=True)
        patch.write_text("--- a/x\n+++ b/x\n")
        subprocess.run(
            ["git", "-C", str(ws), "add", str(patch.relative_to(ws))],
            check=True,
        )
        subprocess.run(["git", "-C", str(ws), "commit", "-qm", "add"],
                       check=True)

        from dports_dev_env.cli import _git_head
        log = {
            "schema_version": 1,
            "origin": "devel/foo",
            "target": "@main",
            "mode_at_apply": "compat",
            # baseline_commit must match the workspace's HEAD or the
            # baseline check refuses replay (added in §3 follow-up).
            "baseline_commit": _git_head(ws),
            "intents": [
                {"seq": 0, "ok": True,
                 "intent": {"type": "drop_patch",
                            "target": "dragonfly/patch-old.c",
                            "reason": "obsolete"}},
            ],
        }
        log_path = tmp_path / "intent_log.json"
        log_path.write_text(json.dumps(log))

        rc, applied, total, err = _replay_intent_log(log_path, ws, "devel/foo")
        assert rc == 0, err
        assert applied == 1
        assert not patch.exists()

    def test_replay_rejects_origin_mismatch(self, tmp_path):
        from dports_dev_env.cli import _replay_intent_log
        ws = _make_workspace(tmp_path)
        log = {"origin": "devel/bar", "mode_at_apply": "compat", "intents": []}
        log_path = tmp_path / "log.json"
        log_path.write_text(json.dumps(log))
        rc, applied, total, err = _replay_intent_log(log_path, ws, "devel/foo")
        assert rc == 1
        assert "origin" in err.lower()
        assert applied == 0

    def test_replay_skips_originally_failed_entries(self, tmp_path):
        """Entries with ok=False shouldn't be replayed — they were
        already failures the first time around; replaying them just
        causes phantom failures in the verify run."""
        from dports_dev_env.cli import _replay_intent_log
        ws = _make_workspace(tmp_path)
        log = {
            "origin": "devel/foo", "mode_at_apply": "compat",
            "intents": [
                {"seq": 0, "ok": False,
                 "intent": {"type": "drop_patch",
                            "target": "dragonfly/missing.c",
                            "reason": "x"}},
            ],
        }
        log_path = tmp_path / "log.json"
        log_path.write_text(json.dumps(log))
        rc, applied, total, err = _replay_intent_log(log_path, ws, "devel/foo")
        # No intents successfully applied, but no error either —
        # ok=False entries are skipped.
        assert rc == 0, err
        assert applied == 0

    def test_replay_refuses_baseline_mismatch(self, tmp_path):
        """Design §8 step 2: refuse replay when intent log's
        baseline_commit doesn't match the env's git HEAD. Drift
        protection — operator can't verify against a different
        starting state than the agent ran on."""
        from dports_dev_env.cli import _replay_intent_log

        ws = _make_workspace(tmp_path)
        log = {
            "origin": "devel/foo", "mode_at_apply": "compat",
            "baseline_commit": "deadbeef0000deadbeef0000deadbeef00000000",
            "intents": [],
        }
        log_path = tmp_path / "log.json"
        log_path.write_text(json.dumps(log))
        rc, applied, total, err = _replay_intent_log(log_path, ws, "devel/foo")
        assert rc == 1
        assert applied == 0
        assert "baseline_commit" in err
        assert "drift" in err.lower()

    def test_replay_allows_missing_baseline(self, tmp_path):
        """Empty baseline (older logs / git failure at apply time)
        is allowed through with a stderr warning — operator opted
        in by triggering verify."""
        from dports_dev_env.cli import _replay_intent_log

        ws = _make_workspace(tmp_path)
        log = {
            "origin": "devel/foo", "mode_at_apply": "compat",
            "baseline_commit": "",
            "intents": [],
        }
        log_path = tmp_path / "log.json"
        log_path.write_text(json.dumps(log))
        rc, applied, total, err = _replay_intent_log(log_path, ws, "devel/foo")
        # rc=0 even with empty intent list; the warning goes to
        # stderr, not the return value's error blob.
        assert rc == 0, err
        assert applied == 0

    def test_replay_first_failure_short_circuits(self, tmp_path):
        from dports_dev_env.cli import _replay_intent_log
        ws = _make_workspace(tmp_path)
        log = {
            "origin": "devel/foo", "mode_at_apply": "compat",
            "intents": [
                {"seq": 0, "ok": True,
                 "intent": {"type": "drop_patch",
                            "target": "dragonfly/does-not-exist.c",
                            "reason": "x"}},
            ],
        }
        log_path = tmp_path / "log.json"
        log_path.write_text(json.dumps(log))
        rc, applied, total, err = _replay_intent_log(log_path, ws, "devel/foo")
        assert rc == 1
        assert applied == 0
        assert "drop_patch" in err


# --------------------------------------------------------------------
# Step 25g: pre-replay clean assertion + post-build cleanup
# --------------------------------------------------------------------


class TestStep25gLifecycle:

    def test_port_dirty_paths_returns_porcelain_paths(self, tmp_path):
        from dports_dev_env.cli import _port_dirty_paths
        ws = _make_workspace(tmp_path)
        # Workspace starts clean.
        assert _port_dirty_paths(ws, "devel/foo") == []
        # Make a tracked file dirty + add an untracked file.
        port = ws / "ports/devel/foo"
        (port / "Makefile.DragonFly").write_text("USES+= ssl\n")
        subprocess.run(
            ["git", "-C", str(ws), "add",
             "ports/devel/foo/Makefile.DragonFly"], check=True,
        )
        subprocess.run(
            ["git", "-C", str(ws), "commit", "-qm", "add"], check=True,
        )
        (port / "Makefile.DragonFly").write_text("USES+= ssl readline\n")
        (port / "untracked.txt").write_text("scratch\n")
        dirty = _port_dirty_paths(ws, "devel/foo")
        assert any("Makefile.DragonFly" in p for p in dirty)
        assert any("untracked.txt" in p for p in dirty)

    def test_reset_port_cli_registers(self):
        """`dportsv3 dev-env reset-port --help` should not blow up,
        and the subcommand should be in the dispatch table."""
        from dports_dev_env.cli import build_parser
        parser = build_parser()
        args = parser.parse_args(["reset-port", "my-env", "devel/foo"])
        assert args.action == "reset-port"
        assert args.name == "my-env"
        assert args.origin == "devel/foo"

    def test_reset_logic_via_helper(self, tmp_path):
        """Verify the reset shell command shape works against a
        real workspace (the same command apply_and_build's
        post-build runs inside the chroot)."""
        ws = _make_workspace(tmp_path)
        # Commit a baseline file.
        port = ws / "ports/devel/foo"
        (port / "Makefile.DragonFly").write_text("baseline\n")
        subprocess.run(["git", "-C", str(ws), "add", "."], check=True)
        subprocess.run(["git", "-C", str(ws), "commit", "-qm", "base"],
                       check=True)
        # Dirty it: modify tracked + add untracked.
        (port / "Makefile.DragonFly").write_text("modified\n")
        (port / "scratch").write_text("untracked\n")

        # Run the same reset sequence apply_and_build uses.
        subprocess.run(
            ["git", "-C", str(ws), "checkout", "HEAD", "--",
             "ports/devel/foo"], check=True,
        )
        subprocess.run(
            ["git", "-C", str(ws), "clean", "-fd", "--",
             "ports/devel/foo"], check=True,
        )

        assert (port / "Makefile.DragonFly").read_text() == "baseline\n"
        assert not (port / "scratch").exists()


# --------------------------------------------------------------------
# End-to-end pipeline: apply_intent → drain → serialize → load →
# replay. Regression guard for the whole 25b-25e chain. If any
# link breaks, this test should catch it before the dependent
# slices touch it.
# --------------------------------------------------------------------


class TestPipelineEndToEnd:

    def test_apply_drain_replay_round_trip(
        self, env_with_workspace, tmp_path, monkeypatch,
    ):
        """Full pipeline: the agent applies two intents against a
        fresh workspace, the runner drains the log to JSON, a clean
        copy of the workspace replays the log via the dev-env
        primitive — the end states should be identical."""
        from dports_dev_env.cli import _git_head, _replay_intent_log
        from dportsv3.agent.runner import _write_intent_log_harness

        ws = env_with_workspace

        # Make the worker's baseline resolver actually return the
        # workspace's real HEAD — the default fixture stubbed it
        # to "fake-baseline" which would fail the new drift check
        # at replay.
        monkeypatch.setattr(
            worker, "_resolve_baseline_commit",
            lambda env: _git_head(ws),
        )
        worker._INTENT_LOGS.clear()

        # Stage two patches for two drop_patch intents.
        for name in ("patch-a.c", "patch-b.c"):
            p = ws / f"ports/devel/foo/dragonfly/{name}"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("--- a/x\n+++ b/x\n")
        subprocess.run(
            ["git", "-C", str(ws), "add",
             "ports/devel/foo/dragonfly/patch-a.c",
             "ports/devel/foo/dragonfly/patch-b.c"],
            check=True,
        )
        subprocess.run(["git", "-C", str(ws), "commit", "-qm", "stage"],
                       check=True)
        head_at_apply = _git_head(ws)

        # 1. Agent applies two intents via the worker tool.
        r1 = worker.apply_intent("test-env", "devel/foo", {
            "type": "drop_patch", "target": "dragonfly/patch-a.c",
            "reason": "obsolete A",
        })
        r2 = worker.apply_intent("test-env", "devel/foo", {
            "type": "drop_patch", "target": "dragonfly/patch-b.c",
            "reason": "obsolete B",
        })
        assert r1["ok"] and r2["ok"]
        assert not (ws / "ports/devel/foo/dragonfly/patch-a.c").exists()
        assert not (ws / "ports/devel/foo/dragonfly/patch-b.c").exists()

        # 2. Runner drains + writes intent_log.json.
        bundle_dir = tmp_path / "bundle"
        bundle_dir.mkdir()
        _write_intent_log_harness(bundle_dir, None, "test-env", "devel/foo")
        log_artifact = bundle_dir / "analysis" / "intent_log.json"
        assert log_artifact.is_file()

        # 3. Reset the workspace to the pre-apply state so replay
        #    operates on a clean baseline. (Simulating the verify
        #    env that hasn't seen the agent's edits.)
        subprocess.run(["git", "-C", str(ws), "checkout", "HEAD",
                        "--", "ports/devel/foo"], check=True)
        assert (ws / "ports/devel/foo/dragonfly/patch-a.c").exists()
        assert (ws / "ports/devel/foo/dragonfly/patch-b.c").exists()

        # 4. Replay the intent log against the clean workspace.
        rc, applied, total, err = _replay_intent_log(
            log_artifact, ws, "devel/foo",
        )
        assert rc == 0, err
        assert applied == 2
        assert total == 2
        # End state matches what the agent left behind.
        assert not (ws / "ports/devel/foo/dragonfly/patch-a.c").exists()
        assert not (ws / "ports/devel/foo/dragonfly/patch-b.c").exists()


# --------------------------------------------------------------------
# Verify-fix orchestrator: intent log preference
# --------------------------------------------------------------------


class TestVerifyFixIntentLogPreference:

    def test_orchestrator_prefers_intent_log(self, tmp_path):
        """When the bundle has both intent_log.json and changes.diff,
        the orchestrator pulls intent_log and invokes apply_and_build
        with intent_log_path, not diff_path."""
        from dportsv3 import verify_fix

        captured: dict = {}

        def _fake_ab(env, origin, *, diff_path=None, intent_log_path=None):
            captured["diff_path"] = diff_path
            captured["intent_log_path"] = intent_log_path
            return {"ok": True, "env": env, "origin": origin,
                    "applied_diff_sha256": "x", "dsynth_exit": 0,
                    "apply_exit": 0, "reapply_exit": 0,
                    "log_path": None, "stderr_tail": None}

        def _get_json(url, timeout=10):
            return {"bundle_id": "b-1", "origin": "devel/foo",
                    "target": "@main"}

        def _get_bytes(url, timeout=20):
            if "intent_log.json" in url:
                return b'{"origin":"devel/foo","intents":[]}\n'
            assert False, f"unexpected fetch: {url}"

        def _post_json(url, body, timeout=10):
            return {"ok": True}

        result = verify_fix.run_verify_fix(
            bundle_id="b-1", env="verify-env", tracker_url="http://t",
            _get_json=_get_json, _get_bytes=_get_bytes,
            _post_json=_post_json, _apply_and_build=_fake_ab,
        )
        assert result.ok is True
        assert captured["intent_log_path"] is not None
        assert captured["diff_path"] is None

    def test_orchestrator_refuses_zero_success_replay(self, tmp_path):
        """Step 25g: when intent_log replay produces intents_applied=0,
        the subsequent build success doesn't constitute "verified" —
        the baseline builds, but no fix was reproduced. Orchestrator
        flips ok=False and surfaces a clear reason in the POST body."""
        from dportsv3 import verify_fix

        captured: dict = {}

        def _fake_ab(env, origin, *, diff_path=None, intent_log_path=None):
            # Pretend replay applied zero intents but the build passed.
            return {"ok": True, "env": env, "origin": origin,
                    "applied_diff_sha256": "x",
                    "apply_exit": 0, "reapply_exit": 0, "dsynth_exit": 0,
                    "intents_applied": 0, "replay_mode": "intent_log",
                    "log_path": None, "stderr_tail": None}

        def _get_json(url, timeout=10):
            return {"bundle_id": "b-1", "origin": "devel/foo"}

        def _get_bytes(url, timeout=20):
            if "intent_log.json" in url:
                return b'{"origin":"devel/foo","intents":[]}\n'
            assert False

        posts: list = []
        def _post_json(url, body, timeout=10):
            posts.append((url, body))
            return {"ok": True}

        result = verify_fix.run_verify_fix(
            bundle_id="b-1", env="verify-env", tracker_url="http://t",
            _get_json=_get_json, _get_bytes=_get_bytes,
            _post_json=_post_json, _apply_and_build=_fake_ab,
        )
        # Refused verified despite the build passing.
        assert result.ok is False
        _, body = posts[0]
        assert body["ok"] is False
        # The orchestrator also updates ab["ok"] in sync so any
        # downstream reader of the dict sees the same verdict.
        # (Implicit here — the dict is internal — but the test
        # also exercises the path that flips it.)

    def test_orchestrator_falls_back_to_diff_when_intent_log_missing(
        self, tmp_path,
    ):
        from dportsv3 import verify_fix

        captured: dict = {}

        def _fake_ab(env, origin, *, diff_path=None, intent_log_path=None):
            captured["diff_path"] = diff_path
            captured["intent_log_path"] = intent_log_path
            return {"ok": True, "env": env, "origin": origin,
                    "applied_diff_sha256": "x", "dsynth_exit": 0,
                    "apply_exit": 0, "reapply_exit": 0,
                    "log_path": None, "stderr_tail": None}

        def _get_json(url, timeout=10):
            return {"bundle_id": "b-1", "origin": "devel/foo",
                    "target": "@main"}

        def _get_bytes(url, timeout=20):
            if "intent_log.json" in url:
                raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
            if "changes.diff" in url:
                return b"--- a\n+++ b\n@@ -1 +1 @@\n-x\n+y\n"
            assert False, f"unexpected fetch: {url}"

        def _post_json(url, body, timeout=10):
            return {"ok": True}

        result = verify_fix.run_verify_fix(
            bundle_id="b-1", env="verify-env", tracker_url="http://t",
            _get_json=_get_json, _get_bytes=_get_bytes,
            _post_json=_post_json, _apply_and_build=_fake_ab,
        )
        assert result.ok is True
        assert captured["intent_log_path"] is None
        assert captured["diff_path"] is not None
