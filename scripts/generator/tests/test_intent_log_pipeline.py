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

    def test_intent_log_size_cap_surfaces_to_tool_result(
        self, env_with_workspace, monkeypatch,
    ):
        monkeypatch.setenv("DP_HARNESS_INTENT_MAX_COUNT", "1")
        # First intent succeeds.
        worker.apply_intent("test-env", "devel/foo", {
            "type": "drop_patch", "target": "x", "reason": "y",
        })
        # Second hits the count cap; tool surfaces the cap error.
        result = worker.apply_intent("test-env", "devel/foo", {
            "type": "drop_patch", "target": "z", "reason": "y",
        })
        assert result.get("intent_log_full") is True
        assert "intent_log_full" in result["error"]


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

        log = {
            "schema_version": 1,
            "origin": "devel/foo",
            "target": "@main",
            "mode_at_apply": "compat",
            "baseline_commit": "fake",
            "intents": [
                {"seq": 0, "ok": True,
                 "intent": {"type": "drop_patch",
                            "target": "dragonfly/patch-old.c",
                            "reason": "obsolete"}},
            ],
        }
        log_path = tmp_path / "intent_log.json"
        log_path.write_text(json.dumps(log))

        rc, applied, err = _replay_intent_log(log_path, ws, "devel/foo")
        assert rc == 0, err
        assert applied == 1
        assert not patch.exists()

    def test_replay_rejects_origin_mismatch(self, tmp_path):
        from dports_dev_env.cli import _replay_intent_log
        ws = _make_workspace(tmp_path)
        log = {"origin": "devel/bar", "mode_at_apply": "compat", "intents": []}
        log_path = tmp_path / "log.json"
        log_path.write_text(json.dumps(log))
        rc, applied, err = _replay_intent_log(log_path, ws, "devel/foo")
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
        rc, applied, err = _replay_intent_log(log_path, ws, "devel/foo")
        # No intents successfully applied, but no error either —
        # ok=False entries are skipped.
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
        rc, applied, err = _replay_intent_log(log_path, ws, "devel/foo")
        assert rc == 1
        assert applied == 0
        assert "drop_patch" in err


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
