"""Step 30 slice 5: ``analysis/changes.diff`` is the single
canonical diff artifact (branch-vs-base shape).

Pre-slice-5 there were two artifacts:
- ``changes.diff`` (HEAD-relative) — audit-only
- ``delivery.diff`` (branch-vs-base) — delivery-only

Slice 5 collapsed them. ``changes.diff`` is now branch-vs-base
and the only diff anyone reads (delivery, verify-fix replay,
proposed_fix recipe, agent prior-attempt sections).

Tests pin:
- ``_write_changes_diff`` resolves the env's base branch and
  uses ``_git_diff_against_base`` (not ``_git_diff_with_untracked``).
- The artifact-store + filesystem write paths route correctly.
- Worker exceptions surface as tombstone diff bodies, never
  silent breakage.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from dportsv3.agent import runner, worker


class _FakeEnvPaths:
    def __init__(self, deltaports: Path) -> None:
        self.deltaports = deltaports
        self.writable = deltaports.parent
        self.env_dir = deltaports.parent


def test_changes_diff_is_branch_vs_base(tmp_path, monkeypatch):
    """The slice-5 contract: changes.diff is what
    ``_git_diff_against_base`` produces, not the HEAD-relative
    diff. Without this, converted bundles silently lose the
    convert commit's deltas from the artifact."""
    captured: dict = {}

    def _fake_diff(repo, base, rel):
        captured["repo"] = repo
        captured["base"] = base
        captured["rel"] = rel
        return SimpleNamespace(
            returncode=0, stdout="changes body\n", stderr="",
        )

    def _fake_untracked(*a, **kw):
        raise AssertionError(
            "_git_diff_with_untracked must NOT be called by "
            "_write_changes_diff post-slice-5 — that's the old "
            "HEAD-relative path."
        )

    deltaports = tmp_path / "writable" / "work" / "DeltaPorts"
    deltaports.mkdir(parents=True)
    monkeypatch.setattr(
        worker, "env_paths", lambda env: _FakeEnvPaths(deltaports),
    )
    monkeypatch.setattr(
        worker, "_resolve_bundle_base_branch", lambda env: "main",
    )
    monkeypatch.setattr(worker, "_git_diff_against_base", _fake_diff)
    monkeypatch.setattr(worker, "_git_diff_with_untracked", _fake_untracked)

    bundle_dir = tmp_path / "bundle-x"
    bundle_dir.mkdir()
    runner._write_changes_diff(
        bundle_dir, None, env="e1", origin="devel/foo",
    )

    out = bundle_dir / "analysis" / "changes.diff"
    assert out.is_file()
    assert out.read_text() == "changes body\n"
    assert captured == {
        "repo": deltaports,
        "base": "main",
        # C3: whole-tree, not ports/<origin> — captures fixes that land
        # outside the bundle origin (e.g. a slave's master PATCHDIR).
        "rel": ".",
    }


def test_changes_diff_artifact_store_path(tmp_path, monkeypatch):
    """With a bundle_id, the write routes via ``artifact_store_put``
    rather than the filesystem."""
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

    runner._write_changes_diff(
        None, "bundle-abc", env="e1", origin="devel/foo",
    )
    assert len(stored) == 1
    bid, rel, body, kind = stored[0]
    assert bid == "bundle-abc"
    assert rel == "analysis/changes.diff"
    assert body == b"body\n"
    assert kind == "text"


def test_changes_diff_tolerates_worker_failure(tmp_path, monkeypatch):
    """A failure anywhere in the resolve/diff chain must not
    break the patch step. The writer emits a tombstone body so
    the operator sees the failure shape rather than getting a
    silent empty file."""
    monkeypatch.setattr(
        worker, "env_paths",
        lambda env: (_ for _ in ()).throw(
            RuntimeError("env gone"),
        ),
    )
    bundle_dir = tmp_path / "bundle-x"
    bundle_dir.mkdir()
    runner._write_changes_diff(
        bundle_dir, None, env="missing-env", origin="devel/foo",
    )
    out = bundle_dir / "analysis" / "changes.diff"
    assert out.is_file()
    contents = out.read_text()
    assert "failed to capture diff" in contents
    assert "env gone" in contents


def test_delivery_diff_writer_no_longer_exists():
    """Slice 5 retired ``_write_delivery_diff`` — only the
    single ``_write_changes_diff`` survives. Catches a future
    re-introduction of the dual-artifact split."""
    assert not hasattr(runner, "_write_delivery_diff")
