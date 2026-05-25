"""Tests for worker.commit_port_changes — the stopgap that lets the
convert→patch handoff succeed without Step 26's branch model.

Without this, the convert agent's `put_file overlay.dops` leaves an
untracked file in the env; the next patch job hits the §5.1 pre-job
clean assertion (`patch_preflight_dirty`) and dies. The thrash was
observed on devel/gperf on 2026-05-25.
"""

from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

from dportsv3.agent import worker


def _completed(rc: int, stdout: str = "", stderr: str = ""):
    return subprocess.CompletedProcess(
        args=["fake"], returncode=rc, stdout=stdout, stderr=stderr,
    )


def test_commit_port_changes_invokes_git_add_then_commit(monkeypatch):
    captured = {}
    def fake_exec(env, *argv, cwd=None, **kw):
        captured["env"] = env
        captured["cmd"] = argv[-1] if argv else ""
        # Simulate "had changes; committed successfully"
        return _completed(0, stdout="[master abc123] convert: devel/foo\n")
    monkeypatch.setattr(worker, "_exec", fake_exec)

    r = worker.commit_port_changes("test-env", "devel/foo", "convert: devel/foo")
    assert r["ok"] is True
    assert r["committed"] is True
    assert r["paths_changed"] == ["ports/devel/foo"]
    # Sanity: the shell script wires add → diff-check → commit.
    cmd = captured["cmd"]
    assert "git add -A -- ports/devel/foo" in cmd
    assert "git diff --cached --quiet" in cmd
    assert "git -c user.name=dportsv3-runner" in cmd
    assert "convert: devel/foo" in cmd


def test_commit_port_changes_noop_when_nothing_to_commit(monkeypatch):
    def fake_exec(env, *argv, cwd=None, **kw):
        # The `if git diff --cached --quiet` branch prints 'nothing-to-commit'.
        return _completed(0, stdout="nothing-to-commit\n")
    monkeypatch.setattr(worker, "_exec", fake_exec)

    r = worker.commit_port_changes("test-env", "devel/foo", "convert")
    assert r["ok"] is True
    assert r["committed"] is False
    assert r["paths_changed"] == []


def test_commit_port_changes_propagates_chroot_failure(monkeypatch):
    def fake_exec(env, *argv, cwd=None, **kw):
        return _completed(128, stderr="fatal: not a git repository\n")
    monkeypatch.setattr(worker, "_exec", fake_exec)

    r = worker.commit_port_changes("test-env", "devel/foo", "convert")
    assert r["ok"] is False
    assert "commit_port_changes failed" in r["error"]
    assert "not a git repository" in (r.get("stderr_tail") or "")


def test_commit_message_with_quote_is_escaped(monkeypatch):
    """A naive single-quote substitution would corrupt the shell
    command. Verify the message survives a single-quote without
    breaking the surrounding 'sh -c' arg."""
    captured = {}
    def fake_exec(env, *argv, cwd=None, **kw):
        captured["cmd"] = argv[-1] if argv else ""
        return _completed(0, stdout="committed\n")
    monkeypatch.setattr(worker, "_exec", fake_exec)

    worker.commit_port_changes(
        "test-env", "devel/foo", "convert: don't break me",
    )
    cmd = captured["cmd"]
    # The escaped form should be: don'\''t — keeps the outer quoting intact.
    assert "don'\\''t break me" in cmd
