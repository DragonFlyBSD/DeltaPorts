"""Step 37-1: handler-side framework-patch defer + retry.

Three layers under test:

- ``_parse_compose_rejects(diag)`` — extracts ``diffs/*.diff`` paths
  from compose stdout when a hunk-reject shape is present, returns
  ``[]`` otherwise.
- ``_drop_patch_apply_from_overlay(text, path)`` — pure-string
  rewrite of an overlay.dops body, dropping the ``patch apply
  <path>`` line.
- ``_materialize_with_defer_retry(...)`` — the bounded loop that
  glues the two together with the worker shell-out. Uses a fake
  worker to keep the test substrate-free.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dportsv3.agent.runner import (
    _drop_patch_apply_from_overlay,
    _materialize_with_defer_retry,
    _parse_compose_rejects,
)


# --- _parse_compose_rejects ---------------------------------------------------


def test_parse_rejects_python_plist_shape():
    """Real-shape diag tail from lang/python311: patch tool prints
    'Hunk #N failed' + the rejected hunks reference the diff path
    elsewhere in stdout."""
    diag = (
        "applying op patch.apply diffs/pkg-plist.diff\n"
        "patching pkg-plist using Plan A...\n"
        "Hunk #1 failed at 249.\n"
        "Hunk #3 failed at 2929.\n"
        "3 out of 5 hunks failed--saving rejects to -\n"
        "done\n"
        "modes: dops=1\n"
    )
    assert _parse_compose_rejects(diag) == ["diffs/pkg-plist.diff"]


def test_parse_rejects_multiple_distinct_paths():
    diag = (
        "applying op patch.apply diffs/pkg-plist.diff\n"
        "Hunk #1 failed at 100.\n"
        "applying op patch.apply diffs/Makefile.diff\n"
        "Hunk #2 failed at 50.\n"
    )
    # Order preserved by first appearance.
    assert _parse_compose_rejects(diag) == [
        "diffs/pkg-plist.diff",
        "diffs/Makefile.diff",
    ]


def test_parse_rejects_dedupes():
    diag = (
        "diffs/pkg-plist.diff Hunk #1 failed\n"
        "diffs/pkg-plist.diff Hunk #2 failed\n"
    )
    assert _parse_compose_rejects(diag) == ["diffs/pkg-plist.diff"]


def test_parse_rejects_returns_empty_without_hunk_failure():
    """Path in diag but no 'Hunk #N failed' → not the shape we
    recover from. Don't treat unrelated compose errors as
    patch drift."""
    diag = (
        "E_APPLY_INVALID_PATH diffs/pkg-plist.diff something\n"
        "(other compose noise)\n"
    )
    assert _parse_compose_rejects(diag) == []


def test_parse_rejects_returns_empty_on_no_diff_paths():
    """Hunk failure but no diffs/*.diff path mentioned (e.g. inline
    `patch.apply { diff = '...' }` failing) → can't auto-recover."""
    diag = "Hunk #1 failed at 100\n3 out of 5 hunks failed\n"
    assert _parse_compose_rejects(diag) == []


def test_parse_rejects_handles_empty_input():
    assert _parse_compose_rejects("") == []
    assert _parse_compose_rejects("(no output)") == []


# --- _drop_patch_apply_from_overlay -------------------------------------------


def test_drop_removes_matching_line():
    text = (
        "target @main\n"
        "port lang/foo\n"
        "patch apply diffs/pkg-plist.diff\n"
        "patch apply diffs/Makefile.diff\n"
    )
    new, dropped = _drop_patch_apply_from_overlay(text, "diffs/pkg-plist.diff")
    assert dropped
    assert "diffs/pkg-plist.diff" not in new
    assert "diffs/Makefile.diff" in new  # untouched
    assert "target @main" in new


def test_drop_handles_leading_whitespace():
    text = "block foo {\n  patch apply diffs/x.diff\n}\n"
    new, dropped = _drop_patch_apply_from_overlay(text, "diffs/x.diff")
    assert dropped
    assert "patch apply" not in new
    assert "block foo {" in new and "}" in new


def test_drop_returns_false_when_path_not_found():
    text = "target @main\npatch apply diffs/other.diff\n"
    new, dropped = _drop_patch_apply_from_overlay(text, "diffs/missing.diff")
    assert not dropped
    assert new == text  # unchanged


def test_drop_only_removes_one_occurrence():
    """Defensive: if (somehow) the overlay carries the same patch
    twice, only the first occurrence is removed. Caller retries
    compose; a second drop run hits the second occurrence next."""
    text = (
        "patch apply diffs/x.diff\n"
        "other stuff\n"
        "patch apply diffs/x.diff\n"
    )
    new, dropped = _drop_patch_apply_from_overlay(text, "diffs/x.diff")
    assert dropped
    assert new.count("patch apply diffs/x.diff") == 1


# --- _materialize_with_defer_retry --------------------------------------------


class _FakeWorker:
    """Replaces dportsv3.agent.worker for the loop test. Each call to
    materialize_dports pops the next scripted result; overlay state
    is stored in a dict so the drop helper has somewhere to write."""

    def __init__(self, results, overlay_path: Path, env_paths_obj):
        self._results = list(results)
        self._overlay_path = overlay_path
        self._env_paths = env_paths_obj

    def materialize_dports(self, env, origin):  # noqa: ARG002
        assert self._results, "fake worker exhausted (loop ran too long)"
        return self._results.pop(0)

    def env_paths(self, env):  # noqa: ARG002
        return self._env_paths


class _FakePaths:
    def __init__(self, deltaports: Path):
        self.deltaports = deltaports


@pytest.fixture
def fake_env(tmp_path: Path, monkeypatch):
    """Set up a writable overlay tree and stub the worker so the loop
    can mutate the overlay file via _drop_patch_apply_from_overlay_file."""
    deltaports = tmp_path / "DeltaPorts"
    port_dir = deltaports / "ports" / "lang" / "foo"
    port_dir.mkdir(parents=True)
    overlay = port_dir / "overlay.dops"
    overlay.write_text(
        "target @main\n"
        "port lang/foo\n"
        "patch apply diffs/pkg-plist.diff\n"
        "patch apply diffs/Makefile.diff\n"
    )

    fake_paths = _FakePaths(deltaports)

    def make_loop(results):
        fake = _FakeWorker(results, overlay, fake_paths)
        from dportsv3.agent import worker as _real_worker
        monkeypatch.setattr(_real_worker, "materialize_dports",
                            fake.materialize_dports)
        monkeypatch.setattr(_real_worker, "env_paths", fake.env_paths)
        return overlay

    return make_loop


def _ok_result():
    return {"ok": True, "rc": 0, "stdout_tail": "done\nmodes: dops=1\n"}


def _hunk_fail_result(path: str):
    return {
        "ok": False,
        "rc": 2,
        "stdout_tail": (
            f"applying op patch.apply {path}\n"
            "patching foo using Plan A...\n"
            "Hunk #1 failed at 249.\n"
            "3 out of 5 hunks failed--saving rejects to -\n"
        ),
        "stderr_tail": "",
    }


def test_loop_succeeds_first_try_returns_no_defers(fake_env, tmp_path):
    overlay = fake_env([_ok_result()])
    queue_root = tmp_path / "queue"
    queue_root.mkdir()
    mat, deferred = _materialize_with_defer_retry(
        "test-env", "lang/foo",
        queue_root=queue_root, job_id="j-1", max_drops=3,
    )
    assert mat["ok"] is True
    assert deferred == []
    # Overlay unchanged
    assert "patch apply diffs/pkg-plist.diff" in overlay.read_text()


def test_loop_drops_one_patch_then_succeeds(fake_env, tmp_path):
    overlay = fake_env([
        _hunk_fail_result("diffs/pkg-plist.diff"),
        _ok_result(),
    ])
    queue_root = tmp_path / "queue"
    queue_root.mkdir()
    mat, deferred = _materialize_with_defer_retry(
        "test-env", "lang/foo",
        queue_root=queue_root, job_id="j-1", max_drops=3,
    )
    assert mat["ok"] is True
    assert deferred == ["diffs/pkg-plist.diff"]
    text = overlay.read_text()
    assert "diffs/pkg-plist.diff" not in text
    assert "diffs/Makefile.diff" in text  # untouched


def test_loop_drops_two_patches_then_succeeds(fake_env, tmp_path):
    overlay = fake_env([
        _hunk_fail_result("diffs/pkg-plist.diff"),
        _hunk_fail_result("diffs/Makefile.diff"),
        _ok_result(),
    ])
    queue_root = tmp_path / "queue"
    queue_root.mkdir()
    mat, deferred = _materialize_with_defer_retry(
        "test-env", "lang/foo",
        queue_root=queue_root, job_id="j-1", max_drops=3,
    )
    assert mat["ok"] is True
    assert deferred == ["diffs/pkg-plist.diff", "diffs/Makefile.diff"]


def test_loop_respects_max_drops_cap(fake_env, tmp_path):
    """Cap=1: drop once, retry; second failure with another reject
    should NOT trigger a second drop (cap reached). Return the last
    failure dict and the single drop we did."""
    overlay = fake_env([
        _hunk_fail_result("diffs/pkg-plist.diff"),
        _hunk_fail_result("diffs/Makefile.diff"),
    ])
    queue_root = tmp_path / "queue"
    queue_root.mkdir()
    mat, deferred = _materialize_with_defer_retry(
        "test-env", "lang/foo",
        queue_root=queue_root, job_id="j-1", max_drops=1,
    )
    assert mat["ok"] is False
    assert deferred == ["diffs/pkg-plist.diff"]
    # First patch is gone; the SECOND was identified but not dropped
    # because cap was reached.
    text = overlay.read_text()
    assert "diffs/pkg-plist.diff" not in text
    assert "diffs/Makefile.diff" in text


def test_loop_bails_on_non_reject_failure(fake_env, tmp_path):
    """Compose fails with a shape that isn't hunk-reject (e.g. dops
    parse error). No drop should happen — caller's existing _fail
    path takes over."""
    overlay = fake_env([
        {"ok": False, "rc": 1, "stdout_tail": "E_DOPS_PARSE: syntax\n",
         "stderr_tail": ""},
    ])
    queue_root = tmp_path / "queue"
    queue_root.mkdir()
    mat, deferred = _materialize_with_defer_retry(
        "test-env", "lang/foo",
        queue_root=queue_root, job_id="j-1", max_drops=3,
    )
    assert mat["ok"] is False
    assert deferred == []
    # Overlay untouched.
    assert "diffs/pkg-plist.diff" in overlay.read_text()


def test_loop_doesnt_drop_same_path_twice(fake_env, tmp_path):
    """If compose somehow reports the same path failing again after
    we already dropped it, don't loop forever — bail. (Shouldn't
    happen in practice; the line is gone from overlay, so compose
    shouldn't reference it.)"""
    overlay = fake_env([
        _hunk_fail_result("diffs/pkg-plist.diff"),
        # Same path reported again — shouldn't happen, but defensive.
        _hunk_fail_result("diffs/pkg-plist.diff"),
    ])
    queue_root = tmp_path / "queue"
    queue_root.mkdir()
    mat, deferred = _materialize_with_defer_retry(
        "test-env", "lang/foo",
        queue_root=queue_root, job_id="j-1", max_drops=3,
    )
    assert mat["ok"] is False
    assert deferred == ["diffs/pkg-plist.diff"]
