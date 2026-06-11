"""Phase B (Step 48 cutover) — the runner's deterministic
bootstrap-or-abort hook that replaced defer-to-convert."""

from __future__ import annotations

from pathlib import Path

import pytest

from dportsv3.agent import runner as runner_mod
from dportsv3.agent import worker as worker_mod
from dportsv3.agent.overlay_state import OverlayFacts
from dportsv3.agent.runner import _ensure_overlay_or_abort


@pytest.fixture
def wired(monkeypatch):
    """Resolve a fake env, silence activity_log, and capture put_file /
    _exec so the hook runs without a real dev-env."""
    monkeypatch.setattr(runner_mod, "_CLI_ENV_DEFAULT", "test-env")
    monkeypatch.setattr(runner_mod, "activity_log",
                        lambda *a, **k: None)

    calls = {"put_file": [], "exec": []}

    def fake_put_file(env, path, content, **kw):
        calls["put_file"].append((path, content))
        return {"ok": True, "sha256": "deadbeef"}

    class _P:
        def __init__(self, out=""):
            self.stdout = out
            self.returncode = 0

    def fake_exec(env, *args):
        calls["exec"].append(args)
        # STATUS read uses `head -1 ... STATUS`; default empty (no STATUS).
        joined = " ".join(str(a) for a in args)
        if "STATUS" in joined and "head" in joined:
            return _P(fake_exec.status or "")
        return _P("")
    fake_exec.status = ""

    monkeypatch.setattr(worker_mod, "put_file", fake_put_file)
    monkeypatch.setattr(worker_mod, "_exec", fake_exec)
    return calls, fake_exec


def _facts(**kw) -> OverlayFacts:
    return OverlayFacts(origin="devel/x", port_exists=True, **kw)


def _run(monkeypatch, facts):
    monkeypatch.setattr(worker_mod, "probe_overlay_facts",
                        lambda env, origin: facts)
    return _ensure_overlay_or_abort(
        queue_root=Path("/tmp/q"),
        job={"origin": "devel/x", "target": "@main"},
        job_path=Path("/tmp/q/pending/triage-1.job"),
        origin="devel/x",
    )


def test_overlay_present_proceeds(wired, monkeypatch):
    calls, _ = wired
    assert _run(monkeypatch, _facts(overlay_dops=True)) is None
    assert calls["put_file"] == []  # no bootstrap


def test_new_port_bootstraps_type_port(wired, monkeypatch):
    calls, _ = wired
    assert _run(monkeypatch, _facts()) is None
    assert len(calls["put_file"]) == 1
    path, content = calls["put_file"][0]
    assert path.endswith("ports/devel/x/overlay.dops")
    assert "type port" in content
    assert "port devel/x" in content


def test_pure_dport_bootstraps_dport_and_removes_status(wired, monkeypatch):
    calls, _ = wired
    assert _run(monkeypatch, _facts(newport=True)) is None
    _, content = calls["put_file"][0]
    assert "type dport" in content
    # STATUS removal exec'd.
    assert any("rm -f" in " ".join(str(a) for a in c) for c in calls["exec"])


def test_makefile_compat_aborts_without_bootstrap(wired, monkeypatch):
    calls, _ = wired
    out = _run(monkeypatch, _facts(makefile_dragonfly=("Makefile.DragonFly",)))
    assert isinstance(out, tuple) and out[0] == "abort"
    assert calls["put_file"] == []  # never wrote a stub overlay


def test_bootstrap_write_failure_aborts(wired, monkeypatch):
    calls, _ = wired
    monkeypatch.setattr(worker_mod, "put_file",
                        lambda *a, **k: {"ok": False, "error": "denied"})
    out = _run(monkeypatch, _facts())
    assert isinstance(out, tuple) and out[0] == "abort"
    assert out[1] == "bootstrap_write_failed"
