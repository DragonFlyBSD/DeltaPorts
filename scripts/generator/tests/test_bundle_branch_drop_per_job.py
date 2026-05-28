"""Step 30 slice 4: ``_drop_bundle_branch_for_job`` helper +
per-job-end cleanup contract.

Lifecycle pinned by these tests:

- Convert SUCCESS → branch is kept (the next patch job's
  ``checkout_bundle_branch`` will reuse it).
- Convert FAILURE → branch is dropped (the next attempt starts
  fresh from base; partial convert commits are not useful).
- Patch end (either outcome) → branch is dropped (changes.diff
  was already captured at patch success — slice 5 made it the
  branch-vs-base shape; on failure the branch's state is moot).
- Verify end (either outcome) → the throwaway ``bundle/<id>-verify``
  branch is dropped and the pre-verify ref restored (verify runs on
  its own branch cut fresh from base, decoupled from the patch
  agent's ``bundle/<id>``). See the verify-branch wrapper tests
  below.

Soft-fail semantics mirror slice 1's checkout helper: drop
failures activity-log and continue. The next bundle's checkout
will tolerate a leftover branch.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from dportsv3.agent import runner


def _activity_recorder(monkeypatch) -> list[dict]:
    rows: list[dict] = []
    monkeypatch.setattr(
        runner, "activity_log",
        lambda queue_root, stage, message, **kw: rows.append(
            {"stage": stage, "message": message, **kw},
        ),
    )
    return rows


# --- _drop_bundle_branch_for_job helper -----------------------------


def test_drop_helper_noop_without_env_or_bundle(monkeypatch, tmp_path):
    """The helper is a no-op when either env or bundle_id is empty —
    matches ``_checkout_bundle_branch_for_job``'s shape so triage
    jobs (no env interaction) and operator-fired converts (no bundle)
    don't accidentally call into worker."""
    rows = _activity_recorder(monkeypatch)
    called: list = []
    from dportsv3.agent import worker
    monkeypatch.setattr(
        worker, "drop_bundle_branch",
        lambda env, bundle_id: called.append((env, bundle_id))
        or {"ok": True, "removed": True},
    )
    runner._drop_bundle_branch_for_job(
        queue_root=tmp_path, job_id="j", env=None,
        bundle_id="b", job_type="patch", reason="x",
    )
    runner._drop_bundle_branch_for_job(
        queue_root=tmp_path, job_id="j", env="e", bundle_id=None,
        job_type="patch", reason="x",
    )
    assert called == []
    assert rows == []


def test_drop_helper_logs_success_when_removed(monkeypatch, tmp_path):
    rows = _activity_recorder(monkeypatch)
    from dportsv3.agent import worker
    monkeypatch.setattr(
        worker, "drop_bundle_branch",
        lambda env, bundle_id: {
            "ok": True, "removed": True,
            "branch": f"bundle/{bundle_id}", "base": "main",
        },
    )
    runner._drop_bundle_branch_for_job(
        queue_root=tmp_path, job_id="j-1", env="e1",
        bundle_id="b-abc", job_type="patch",
        reason="patch_success",
    )
    swept = [r for r in rows if r["stage"] == "bundle_branch_dropped"]
    assert len(swept) == 1
    assert "patch_success" in swept[0]["message"]
    assert swept[0]["extra"]["bundle_id"] == "b-abc"


def test_drop_helper_quiet_when_branch_already_absent(
    monkeypatch, tmp_path,
):
    """Soft idempotency: if the branch was already gone (e.g.
    operator ran ``git branch -D`` manually), the helper does
    not emit a misleading 'dropped' activity row. Failure rows
    are also suppressed since ok=True; the operation just
    returns silently."""
    rows = _activity_recorder(monkeypatch)
    from dportsv3.agent import worker
    monkeypatch.setattr(
        worker, "drop_bundle_branch",
        lambda env, bundle_id: {
            "ok": True, "removed": False, "reason": "branch_absent",
            "branch": f"bundle/{bundle_id}", "base": "main",
        },
    )
    runner._drop_bundle_branch_for_job(
        queue_root=tmp_path, job_id="j-1", env="e1",
        bundle_id="b-abc", job_type="patch", reason="x",
    )
    assert not any(
        r["stage"] in ("bundle_branch_dropped",
                       "bundle_branch_drop_failed")
        for r in rows
    )


def test_drop_helper_logs_failure(monkeypatch, tmp_path):
    rows = _activity_recorder(monkeypatch)
    from dportsv3.agent import worker
    monkeypatch.setattr(
        worker, "drop_bundle_branch",
        lambda env, bundle_id: {
            "ok": False,
            "error": "git branch -D bundle/b failed",
            "branch": "bundle/b",
        },
    )
    runner._drop_bundle_branch_for_job(
        queue_root=tmp_path, job_id="j-1", env="e1",
        bundle_id="b", job_type="patch", reason="x",
    )
    failed = [
        r for r in rows
        if r["stage"] == "bundle_branch_drop_failed"
    ]
    assert len(failed) == 1
    assert "git branch -D bundle/b failed" in failed[0]["message"]


def test_drop_helper_tolerates_worker_raise(monkeypatch, tmp_path):
    """If worker.drop_bundle_branch itself raises (env vanished,
    subprocess crash), the helper logs and continues. Never
    propagates."""
    rows = _activity_recorder(monkeypatch)
    from dportsv3.agent import worker

    def _raise(env, bundle_id):
        raise RuntimeError("env disconnected")
    monkeypatch.setattr(worker, "drop_bundle_branch", _raise)

    # Should not raise.
    runner._drop_bundle_branch_for_job(
        queue_root=tmp_path, job_id="j-1", env="e1",
        bundle_id="b", job_type="patch", reason="x",
    )
    failed = [
        r for r in rows
        if r["stage"] == "bundle_branch_drop_failed"
    ]
    assert len(failed) == 1
    assert "env disconnected" in failed[0]["message"]


# --- verify-branch wrappers -----------------------------------------


def test_checkout_verify_wrapper_returns_previous_ref(monkeypatch, tmp_path):
    """The verify checkout wrapper returns worker's previous_ref so
    the dispatch can hand it to the end-of-run drop."""
    rows = _activity_recorder(monkeypatch)
    from dportsv3.agent import worker
    monkeypatch.setattr(
        worker, "checkout_verify_branch",
        lambda env, bundle_id: {
            "ok": True, "branch": "bundle/b-verify", "base": "main",
            "previous_ref": "bundle/b", "created": True,
        },
    )
    prev = runner._checkout_verify_branch_for_job(
        queue_root=tmp_path, job_id="j-1", env="e1", bundle_id="b",
    )
    assert prev == "bundle/b"
    assert any(r["stage"] == "verify_branch_checkout" for r in rows)


def test_checkout_verify_wrapper_noop_without_env_or_bundle(monkeypatch, tmp_path):
    rows = _activity_recorder(monkeypatch)
    called: list = []
    from dportsv3.agent import worker
    monkeypatch.setattr(
        worker, "checkout_verify_branch",
        lambda env, bundle_id: called.append((env, bundle_id)) or {"ok": True},
    )
    assert runner._checkout_verify_branch_for_job(
        queue_root=tmp_path, job_id="j", env=None, bundle_id="b",
    ) is None
    assert runner._checkout_verify_branch_for_job(
        queue_root=tmp_path, job_id="j", env="e", bundle_id=None,
    ) is None
    assert called == []
    assert rows == []


def test_checkout_verify_wrapper_soft_fails_on_error(monkeypatch, tmp_path):
    rows = _activity_recorder(monkeypatch)
    from dportsv3.agent import worker
    monkeypatch.setattr(
        worker, "checkout_verify_branch",
        lambda env, bundle_id: {"ok": False, "error": "boom", "branch": "x"},
    )
    prev = runner._checkout_verify_branch_for_job(
        queue_root=tmp_path, job_id="j-1", env="e1", bundle_id="b",
    )
    assert prev is None
    assert any(r["stage"] == "verify_branch_checkout_failed" for r in rows)


def test_drop_verify_wrapper_passes_restore_ref(monkeypatch, tmp_path):
    rows = _activity_recorder(monkeypatch)
    seen: list = []
    from dportsv3.agent import worker
    monkeypatch.setattr(
        worker, "drop_verify_branch",
        lambda env, bundle_id, restore_ref: seen.append(
            (env, bundle_id, restore_ref)
        ) or {
            "ok": True, "removed": True, "branch": "bundle/b-verify",
            "restored_to": restore_ref,
        },
    )
    runner._drop_verify_branch_for_job(
        queue_root=tmp_path, job_id="j-1", env="e1", bundle_id="b",
        restore_ref="bundle/b", reason="verify_complete",
    )
    assert seen == [("e1", "b", "bundle/b")]
    assert any(r["stage"] == "verify_branch_dropped" for r in rows)


def test_drop_verify_wrapper_tolerates_worker_raise(monkeypatch, tmp_path):
    rows = _activity_recorder(monkeypatch)
    from dportsv3.agent import worker

    def _raise(env, bundle_id, restore_ref):
        raise RuntimeError("env disconnected")
    monkeypatch.setattr(worker, "drop_verify_branch", _raise)

    runner._drop_verify_branch_for_job(
        queue_root=tmp_path, job_id="j-1", env="e1", bundle_id="b",
        restore_ref=None, reason="verify_failure",
    )
    failed = [r for r in rows if r["stage"] == "verify_branch_drop_failed"]
    assert len(failed) == 1
    assert "env disconnected" in failed[0]["message"]
