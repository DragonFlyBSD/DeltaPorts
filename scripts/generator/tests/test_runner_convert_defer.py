"""Step 37-1: handler-side framework-patch defer + retry.

Three layers under test:

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
    _extract_reject_summary,
    _infer_target_file_from_diff,
    _materialize_with_defer_retry,
)


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
    materialize_dports[_with_report] pops the next scripted result;
    overlay state is stored in a dict so the drop helper has
    somewhere to write.

    Scripted results may carry a ``report`` key (a parsed compose
    JSON dict) for the report-based defer path; if absent the loop
    falls back to text scraping via ``stdout_tail``/``stderr_tail``.
    """

    def __init__(self, results, overlay_path: Path, env_paths_obj):
        self._results = list(results)
        self._overlay_path = overlay_path
        self._env_paths = env_paths_obj

    def materialize_dports(self, env, origin):  # noqa: ARG002
        assert self._results, "fake worker exhausted (loop ran too long)"
        return self._results.pop(0)

    def materialize_dports_with_report(self, env, origin):  # noqa: ARG002
        assert self._results, "fake worker exhausted (loop ran too long)"
        result = self._results.pop(0)
        # The runtime function always sets `report` (None if parse
        # failed). Tests that don't care about the JSON path can
        # supply only stdout_tail and the loop falls back to text.
        result.setdefault("report", None)
        return result

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
        monkeypatch.setattr(_real_worker, "materialize_dports_with_report",
                            fake.materialize_dports_with_report)
        monkeypatch.setattr(_real_worker, "env_paths", fake.env_paths)
        return overlay

    return make_loop


def _ok_result():
    return {"ok": True, "rc": 0, "stdout_tail": "done\nmodes: dops=1\n"}


def _hunk_fail_result(path: str, origin: str = "lang/foo",
                      patch_msg: str = ""):
    """Scripted failure result matching what `materialize_dports_with_
    report` returns when a `patch.apply` op rejects. The `report` field
    carries the structured shape compose's --json output emits; the
    runner consumes that to identify the failing diff path."""
    abs_path = f"/work/DeltaPorts/ports/{origin}/{path}"
    msg = patch_msg or (
        "patching foo using Plan A...\n"
        "Hunk #1 failed at 249.\n"
        "3 out of 5 hunks failed--saving rejects to -\n"
    )
    return {
        "ok": False,
        "rc": 2,
        "stdout_tail": "(structured report; stdout_tail is opaque)",
        "stderr_tail": "",
        "report": {
            "ok": False,
            "ports": [{
                "origin": origin,
                "dops_failed_op_results": [{
                    "id": "op-0001-patch-apply",
                    "kind": "patch.apply",
                    "diagnostics": [{
                        "severity": "error",
                        "code": "E_APPLY_PATCH_FAILED",
                        "source_path": abs_path,
                        "message": msg,
                    }],
                }],
            }],
        },
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
    assert [d.path for d in deferred] == ["diffs/pkg-plist.diff"]
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
    assert [d.path for d in deferred] == [
        "diffs/pkg-plist.diff",
        "diffs/Makefile.diff",
    ]


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
    assert [d.path for d in deferred] == ["diffs/pkg-plist.diff"]
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
    assert [d.path for d in deferred] == ["diffs/pkg-plist.diff"]


# --- Step 37-2: rich DeferredPatch context -----------------------------------


def test_infer_target_file_from_plist_diff():
    diff = (
        "--- pkg-plist.orig\t2024-04-20 15:25:52 UTC\n"
        "+++ pkg-plist\n"
        "@@ -249,9 +249,6 @@\n"
        " line\n"
    )
    assert _infer_target_file_from_diff(diff, fallback="diffs/pkg-plist.diff") == "pkg-plist"


def test_infer_target_file_falls_back_to_path_stem():
    """No +++ line in the diff → derive from the diff path's stem
    (strip ``.diff`` suffix)."""
    assert _infer_target_file_from_diff("", fallback="diffs/pkg-plist.diff") == "pkg-plist"
    assert _infer_target_file_from_diff("garbage\n", fallback="diffs/Makefile.diff") == "Makefile"


def test_extract_reject_summary_single_hunk():
    diag = (
        "patching pkg-plist using Plan A...\n"
        "Hunk #1 failed at 249.\n"
        "1 out of 5 hunks failed--saving rejects to -\n"
    )
    assert _extract_reject_summary(diag, "diffs/pkg-plist.diff") == "Hunks #1 failed at 249"


def test_extract_reject_summary_multiple_hunks():
    diag = (
        "Hunk #1 failed at 249.\n"
        "Hunk #2 succeeded at 720.\n"
        "Hunk #3 failed at 2929.\n"
        "Hunk #4 failed at 2972.\n"
    )
    summary = _extract_reject_summary(diag, "diffs/pkg-plist.diff")
    assert summary == "Hunks #1 #3 #4 failed at 249, 2929, 2972"


def test_extract_reject_summary_no_position_data():
    diag = "Hunk #1 failed\n"
    assert _extract_reject_summary(diag, "diffs/x.diff") == "Hunks #1 failed"


def test_extract_reject_summary_no_hunks_fallback():
    """No Hunk #N failed lines (shouldn't happen in practice, since
    _failed_patch_diags requires patch.apply rows) — fall back to a generic
    message naming the diff path so the field is never empty."""
    assert _extract_reject_summary("(no useful diag)", "diffs/x.diff") == "compose rejected diffs/x.diff"


def test_loop_populates_deferred_patch_fields(tmp_path, monkeypatch):
    """End-to-end: loop drops one patch, the DeferredPatch carries
    path, target_file, non-empty original_content, and a real
    reject_summary."""
    deltaports = tmp_path / "DeltaPorts"
    port_dir = deltaports / "ports" / "lang" / "foo"
    diffs_dir = port_dir / "diffs"
    diffs_dir.mkdir(parents=True)
    overlay = port_dir / "overlay.dops"
    overlay.write_text(
        "target @main\n"
        "port lang/foo\n"
        "patch apply diffs/pkg-plist.diff\n"
    )
    # Real diff file content so original_content + target_file are
    # populated from the file, not the fallback.
    diff_content = (
        "--- pkg-plist.orig\t2024-04-20 15:25:52 UTC\n"
        "+++ pkg-plist\n"
        "@@ -249,9 +249,6 @@\n"
        " %%PYTHON_LIBDIR%%/__pycache__/_strptime.opt-1.pyc\n"
        "-%%PYTHON_LIBDIR%%/__pycache__/_sysconfigdata__freebsd99_.opt-1.pyc\n"
    )
    (diffs_dir / "pkg-plist.diff").write_text(diff_content)

    fake_paths = _FakePaths(deltaports)
    fake = _FakeWorker(
        [_hunk_fail_result("diffs/pkg-plist.diff", patch_msg=("patching pkg-plist using Plan A...\n"
                                       "Hunk #1 failed at 249.\n"
                                       "Hunk #3 failed at 2929.\n"
                                       "2 out of 5 hunks failed--saving rejects to -\n")), _ok_result()],
        overlay, fake_paths,
    )
    from dportsv3.agent import worker as _real_worker
    monkeypatch.setattr(_real_worker, "materialize_dports",
                        fake.materialize_dports)
    monkeypatch.setattr(_real_worker, "materialize_dports_with_report",
                        fake.materialize_dports_with_report)
    monkeypatch.setattr(_real_worker, "env_paths", fake.env_paths)

    queue_root = tmp_path / "queue"
    queue_root.mkdir()
    mat, deferred = _materialize_with_defer_retry(
        "test-env", "lang/foo",
        queue_root=queue_root, job_id="j-1", max_drops=3,
    )

    assert mat["ok"] is True
    assert len(deferred) == 1
    dp = deferred[0]
    assert dp.path == "diffs/pkg-plist.diff"
    assert dp.target_file == "pkg-plist"             # from +++ line
    assert diff_content in dp.original_content       # full content captured
    assert "Hunks #1 #3 failed at 249, 2929" in dp.reject_summary


def test_convert_result_round_trips_deferred_patches(tmp_path):
    """ConvertResult with deferred_patches serializes via asdict +
    rehydrates via load_phase_result (which reconstructs nested
    DeferredPatch instances)."""
    from dportsv3.agent.phase_result import (
        ConvertResult, DeferredPatch, load_phase_result, write_phase_result,
    )
    from dportsv3.agent import runner as runner_mod
    import json

    # Stand up a fake bundle dir + artifact_store_put / read_bundle_text
    # that point to it.
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    saved = {}

    def fake_put(bundle_id, relpath, data, _kind):
        saved[(bundle_id, relpath)] = data
        return True

    def fake_read(_bundle_dir, bundle_id, relpath):
        data = saved.get((bundle_id, relpath))
        return data.decode("utf-8") if data else None

    import pytest as _pytest
    monkey = _pytest.MonkeyPatch()
    monkey.setattr(runner_mod, "artifact_store_put", fake_put)
    monkey.setattr(runner_mod, "read_bundle_text", fake_read)
    try:
        result = ConvertResult(
            status="verified",
            reapply_ok=True,
            reason_code=None,
            overlay_sha256="abc123",
            files_removed=["Makefile.DragonFly"],
            diag_tail=None,
            tokens_prompt=100,
            tokens_completion=50,
            tokens_total=150,
            deferred_patches=[
                DeferredPatch(
                    path="diffs/pkg-plist.diff",
                    target_file="pkg-plist",
                    original_content="--- pkg-plist.orig\n+++ pkg-plist\n",
                    reject_summary="Hunks #1 #3 failed at 249, 2929",
                ),
            ],
        )
        write_phase_result("bundle-X", "convert", result)

        # Bytes on disk parse as JSON with the expected shape.
        raw = saved[("bundle-X", "analysis/convert_result.json")].decode("utf-8")
        payload = json.loads(raw)
        assert payload["schema_version"] == 2
        assert payload["deferred_patches"][0]["path"] == "diffs/pkg-plist.diff"

        # Load round-trips into typed DeferredPatch.
        loaded = load_phase_result(None, "bundle-X", "convert", ConvertResult)
        assert loaded is not None
        assert isinstance(loaded.deferred_patches[0], DeferredPatch)
        assert loaded.deferred_patches[0].target_file == "pkg-plist"
        assert loaded.deferred_patches[0].reject_summary.startswith("Hunks #1")
    finally:
        monkey.undo()


# --- Step 37 --json report path ----------------------------------------------


from dportsv3.agent.runner import _failed_patch_diags


def test_candidates_from_report_pulls_diff_path():
    """The structured compose report carries the diff path natively
    in dops_failed_op_results[].diagnostics[].source_path. Caller
    no longer depends on the formatter's bracket suffix."""
    report = {
        "ok": False,
        "ports": [{
            "origin": "lang/python311",
            "dops_failed_op_results": [{
                "id": "op-0002-patch-apply",
                "kind": "patch.apply",
                "diagnostics": [{
                    "severity": "error",
                    "code": "E_APPLY_PATCH_FAILED",
                    "source_path": (
                        "/work/DeltaPorts/ports/lang/python311/"
                        "diffs/pkg-plist.diff"
                    ),
                }],
            }],
        }],
    }
    assert [p for p, _ in _failed_patch_diags(report, "lang/python311",)] == ["diffs/pkg-plist.diff"]


def test_candidates_from_report_returns_empty_on_no_failures():
    report = {"ok": True, "ports": [{"origin": "x/y",
                                      "dops_failed_op_results": []}]}
    assert [p for p, _ in _failed_patch_diags(report, "x/y")] == []


def test_candidates_from_report_ignores_non_patch_failures():
    """Other op kinds (mk.var.set, file.materialize) shouldn't be
    treated as defer candidates — only patch.apply rejections."""
    report = {
        "ports": [{
            "origin": "x/y",
            "dops_failed_op_results": [{
                "kind": "mk.var.set",
                "diagnostics": [{
                    "source_path": "/work/DeltaPorts/ports/x/y/Makefile",
                }],
            }],
        }],
    }
    assert [p for p, _ in _failed_patch_diags(report, "x/y")] == []


def test_candidates_from_report_ignores_other_origins():
    """When multiple ports failed, only the requested origin's
    patch failures count — avoids dropping patches for an unrelated
    port that happened to fail in the same compose run."""
    report = {
        "ports": [
            {
                "origin": "other/port",
                "dops_failed_op_results": [{
                    "kind": "patch.apply",
                    "diagnostics": [{
                        "source_path": "/work/DeltaPorts/ports/other/port/diffs/a.diff",
                    }],
                }],
            },
            {
                "origin": "lang/python311",
                "dops_failed_op_results": [],
            },
        ],
    }
    assert [p for p, _ in _failed_patch_diags(report, "lang/python311")] == []


def test_candidates_from_report_returns_empty_on_none():
    assert [p for p, _ in _failed_patch_diags(None, "x/y")] == []
    assert [p for p, _ in _failed_patch_diags({}, "x/y")] == []


def test_loop_drops_via_json_report_path(fake_env, tmp_path):
    """End-to-end: when the worker returns a structured report
    (compose --json), the loop pulls candidates from it directly —
    no text scraping. Pins the option-2 path so a regression
    surfaces here."""
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
    assert [d.path for d in deferred] == ["diffs/pkg-plist.diff"]
    # The overlay's reference is gone after the drop.
    assert "diffs/pkg-plist.diff" not in overlay.read_text()
