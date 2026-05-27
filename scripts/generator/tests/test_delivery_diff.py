"""Step 30 slice 2: ``analysis/delivery.diff`` is the
branch-vs-base artifact that captures everything between the env's
base branch and the bundle's working tree.

Two layers under test:

1. ``worker._git_diff_against_base`` produces the correct git
   command shape (base ref + path scope + --intent-to-add for
   untracked surfacing).

2. ``runner._write_delivery_diff`` exercises the same artifact-
   write semantics as ``_write_changes_diff`` (bundle_id → artifact
   store, else bundle_dir → filesystem), tolerates worker failures
   with a tombstone diff body, and uses the env's resolved base
   branch as the comparison ref.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from dportsv3.agent import runner, worker


# --- _git_diff_against_base -------------------------------------------


def test_git_diff_against_base_runs_correct_command_shape(monkeypatch):
    """Three subprocess.run calls in order:
       1. ``git add --intent-to-add -- ports/<origin>``
       2. ``git diff <base> -- ports/<origin>``  ← the load-bearing one
       3. ``git reset -- ports/<origin>``
    The middle call's argv shape is what the delivery diff hinges on."""
    calls: list[list[str]] = []

    def _fake_run(argv, **kw):
        calls.append(list(argv))
        return SimpleNamespace(
            returncode=0, stdout="diff body\n", stderr="",
        )

    monkeypatch.setattr(worker.subprocess, "run", _fake_run)
    repo = Path("/repo")
    p = worker._git_diff_against_base(repo, "main", "ports/devel/foo")

    assert p.returncode == 0
    assert p.stdout == "diff body\n"
    # Three calls in order.
    assert len(calls) == 3
    # The diff call has the base ref + path scope.
    diff_argv = calls[1]
    assert diff_argv[:5] == ["git", "-C", "/repo", "diff", "main"]
    assert "ports/devel/foo" in diff_argv


# --- _write_delivery_diff --------------------------------------------


class _FakeEnvPaths:
    def __init__(self, deltaports: Path) -> None:
        self.deltaports = deltaports
        self.writable = deltaports.parent
        self.env_dir = deltaports.parent


def test_write_delivery_diff_uses_resolved_base_branch(
    tmp_path, monkeypatch,
):
    """The writer must ask ``_resolve_bundle_base_branch`` for the
    env's base and pass it through to the diff helper. Without this
    the diff would either error (no base argv) or default to HEAD
    (defeats the purpose)."""
    captured: dict = {}

    def _fake_resolve(env):
        return "main"

    def _fake_diff(repo, base, rel):
        captured["repo"] = repo
        captured["base"] = base
        captured["rel"] = rel
        return SimpleNamespace(
            returncode=0, stdout="delivery body\n", stderr="",
        )

    deltaports = tmp_path / "writable" / "work" / "DeltaPorts"
    deltaports.mkdir(parents=True)
    monkeypatch.setattr(
        worker, "env_paths", lambda env: _FakeEnvPaths(deltaports),
    )
    monkeypatch.setattr(
        worker, "_resolve_bundle_base_branch", _fake_resolve,
    )
    monkeypatch.setattr(
        worker, "_git_diff_against_base", _fake_diff,
    )

    bundle_dir = tmp_path / "bundle-x"
    bundle_dir.mkdir()
    runner._write_delivery_diff(
        bundle_dir, None, env="e1", origin="devel/foo",
    )

    out = bundle_dir / "analysis" / "delivery.diff"
    assert out.is_file()
    assert out.read_text() == "delivery body\n"
    assert captured == {
        "repo": deltaports,
        "base": "main",
        "rel": "ports/devel/foo",
    }


def test_write_delivery_diff_artifact_store_path(
    tmp_path, monkeypatch,
):
    """When a bundle_id is supplied, the writer routes through
    ``artifact_store_put`` rather than the filesystem."""
    stored: list[tuple] = []
    monkeypatch.setattr(
        runner, "artifact_store_put",
        lambda bid, rel, body, kind: stored.append(
            (bid, rel, body, kind),
        ) or True,
    )
    monkeypatch.setattr(
        worker, "env_paths",
        lambda env: _FakeEnvPaths(tmp_path),
    )
    monkeypatch.setattr(
        worker, "_resolve_bundle_base_branch", lambda env: "main",
    )
    monkeypatch.setattr(
        worker, "_git_diff_against_base",
        lambda *a, **kw: SimpleNamespace(
            returncode=0, stdout="body\n", stderr="",
        ),
    )

    runner._write_delivery_diff(
        None, "bundle-abc", env="e1", origin="devel/foo",
    )
    assert len(stored) == 1
    bid, rel, body, kind = stored[0]
    assert bid == "bundle-abc"
    assert rel == "analysis/delivery.diff"
    assert body == b"body\n"
    assert kind == "text"


def test_write_delivery_diff_tolerates_worker_failure(
    tmp_path, monkeypatch,
):
    """A failure in env_paths / base resolution / diff capture
    must not break the patch step. The writer should emit a
    tombstone diff body so the operator sees the failure shape
    rather than a silent empty file."""
    monkeypatch.setattr(
        worker, "env_paths",
        lambda env: (_ for _ in ()).throw(
            RuntimeError("env gone"),
        ),
    )
    bundle_dir = tmp_path / "bundle-x"
    bundle_dir.mkdir()
    runner._write_delivery_diff(
        bundle_dir, None, env="missing-env", origin="devel/foo",
    )
    out = bundle_dir / "analysis" / "delivery.diff"
    assert out.is_file()
    contents = out.read_text()
    assert "failed to capture delivery diff" in contents
    assert "env gone" in contents


def test_changes_diff_and_delivery_diff_are_separate_artifacts(
    tmp_path, monkeypatch,
):
    """Step 30 contract: ``changes.diff`` stays HEAD-relative for
    audit + intent replay; ``delivery.diff`` is the
    branch-vs-base shape the delivery path consumes. They must be
    distinct artifacts and produced by distinct writers."""
    deltaports = tmp_path / "writable" / "work" / "DeltaPorts"
    deltaports.mkdir(parents=True)
    monkeypatch.setattr(
        worker, "env_paths",
        lambda env: _FakeEnvPaths(deltaports),
    )
    monkeypatch.setattr(
        worker, "_git_diff_with_untracked",
        lambda *a, **kw: SimpleNamespace(
            returncode=0, stdout="changes body\n", stderr="",
        ),
    )
    monkeypatch.setattr(
        worker, "_resolve_bundle_base_branch", lambda env: "main",
    )
    monkeypatch.setattr(
        worker, "_git_diff_against_base",
        lambda *a, **kw: SimpleNamespace(
            returncode=0, stdout="delivery body\n", stderr="",
        ),
    )
    bundle_dir = tmp_path / "bundle-x"
    bundle_dir.mkdir()

    runner._write_changes_diff(
        bundle_dir, None, env="e1", origin="devel/foo",
    )
    runner._write_delivery_diff(
        bundle_dir, None, env="e1", origin="devel/foo",
    )

    changes = bundle_dir / "analysis" / "changes.diff"
    delivery = bundle_dir / "analysis" / "delivery.diff"
    assert changes.is_file()
    assert delivery.is_file()
    assert changes.read_text() == "changes body\n"
    assert delivery.read_text() == "delivery body\n"
