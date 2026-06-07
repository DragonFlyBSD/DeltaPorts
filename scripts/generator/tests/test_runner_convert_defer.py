"""Handler-side convert defer + retry (generalized from the
former patch.apply-only drift recovery).

Three layers under test:

- ``_drop_op_span(text, op_id)`` — pure rewrite of an overlay.dops
  body: re-plan, locate the op by id, splice out its source span.
  Uniform across op kinds (single-line and multi-line heredoc).
- ``_failed_ops(report, origin)`` — pull every failing dops op from
  compose's structured report (not just patch.apply).
- ``_materialize_with_defer_retry(...)`` — the bounded loop gluing
  them to the worker shell-out. Uses a fake worker to stay
  substrate-free.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dportsv3.agent.runner import (
    _drop_op_span,
    _extract_reject_summary,
    _failed_ops,
    _infer_target_file_from_diff,
    _materialize_with_defer_retry,
)

# Overlay bodies used across the drop tests.
_PATCH_OVERLAY = (
    "target @main\n"
    "port lang/foo\n"
    "patch apply diffs/pkg-plist.diff\n"
    "patch apply diffs/Makefile.diff\n"
)
_INLINE_OVERLAY = (
    "target @any\n"
    "port lang/foo\n"
    "type port\n"
    'mk set CFLAGS "-O2"\n'
    'text replace-once file Makefile from "a" to "b"\n'
    "mk target append post-patch <<'MK1'\n"
    "\t@echo hi\n"
    "MK1\n"
)


# --- _drop_op_span ------------------------------------------------------------


def test_drop_single_line_op_by_id():
    new, dropped = _drop_op_span(_INLINE_OVERLAY, "op-0001-mk-var-set")
    assert dropped == 'mk set CFLAGS "-O2"\n'
    assert 'mk set CFLAGS' not in new
    assert 'text replace-once' in new  # untouched
    assert "type port" in new


def test_drop_patch_apply_op_by_id():
    new, dropped = _drop_op_span(_PATCH_OVERLAY, "op-0001-patch-apply")
    assert dropped == "patch apply diffs/pkg-plist.diff\n"
    assert "diffs/pkg-plist.diff" not in new
    assert "diffs/Makefile.diff" in new  # untouched


def test_drop_multiline_heredoc_op_removes_whole_block():
    """A heredoc op spans several lines incl. the terminator; the
    span-based drop must remove the entire block, and the remainder
    must still parse."""
    new, dropped = _drop_op_span(_INLINE_OVERLAY, "op-0003-mk-target-append")
    assert dropped == "mk target append post-patch <<'MK1'\n\t@echo hi\nMK1\n"
    assert "MK1" not in new
    assert "@echo hi" not in new
    from dportsv3.engine.api import build_plan
    assert build_plan(new, None).ok  # remainder still valid


def test_drop_unknown_id_is_noop():
    new, dropped = _drop_op_span(_INLINE_OVERLAY, "op-9999-bogus")
    assert dropped is None
    assert new == _INLINE_OVERLAY  # unchanged


def test_drop_unparseable_overlay_is_noop():
    bad = "this is not valid dops\n"
    new, dropped = _drop_op_span(bad, "op-0001-mk-var-set")
    assert dropped is None
    assert new == bad


# --- _failed_ops --------------------------------------------------------------


def _row(op_id, kind, src, msg=""):
    return {
        "id": op_id,
        "kind": kind,
        "diagnostics": [{"severity": "error", "source_path": src, "message": msg}],
    }


def test_failed_ops_pulls_every_failing_op():
    """Generalized: inline op kinds (mk.var.set, text.replace_once)
    are candidates too, not just patch.apply."""
    report = {
        "ports": [{
            "origin": "lang/foo",
            "dops_failed_op_results": [
                _row("op-0001-mk-var-set", "mk.var.set",
                     "/work/DeltaPorts/ports/lang/foo/Makefile", "ambiguous"),
                _row("op-0002-patch-apply", "patch.apply",
                     "/work/DeltaPorts/ports/lang/foo/diffs/pkg-plist.diff",
                     "Hunk #1 failed at 1"),
            ],
        }],
    }
    out = _failed_ops(report, "lang/foo")
    assert out == [
        ("op-0001-mk-var-set", "mk.var.set", "Makefile", "ambiguous"),
        ("op-0002-patch-apply", "patch.apply", "diffs/pkg-plist.diff",
         "Hunk #1 failed at 1"),
    ]


def test_failed_ops_ignores_other_origins():
    report = {
        "ports": [
            {"origin": "other/port", "dops_failed_op_results": [
                _row("op-0001-patch-apply", "patch.apply",
                     "/work/DeltaPorts/ports/other/port/diffs/a.diff")]},
            {"origin": "lang/foo", "dops_failed_op_results": []},
        ],
    }
    assert _failed_ops(report, "lang/foo") == []


def test_failed_ops_skips_rows_without_id():
    report = {"ports": [{"origin": "x/y", "dops_failed_op_results": [
        {"kind": "mk.var.set", "diagnostics": [{"source_path": "Makefile"}]},
    ]}]}
    assert _failed_ops(report, "x/y") == []


def test_failed_ops_empty_on_none_or_empty():
    assert _failed_ops(None, "x/y") == []
    assert _failed_ops({}, "x/y") == []


# --- _materialize_with_defer_retry --------------------------------------------


class _FakeWorker:
    """Replaces dportsv3.agent.worker for the loop test. Each call to
    materialize_dports_with_report pops the next scripted result;
    the real overlay file on disk is mutated by the drop helper."""

    def __init__(self, results, env_paths_obj):
        self._results = list(results)
        self._env_paths = env_paths_obj

    def materialize_dports_with_report(self, env, origin):  # noqa: ARG002
        assert self._results, "fake worker exhausted (loop ran too long)"
        result = self._results.pop(0)
        result.setdefault("report", None)
        return result

    def env_paths(self, env):  # noqa: ARG002
        return self._env_paths


class _FakePaths:
    def __init__(self, deltaports: Path):
        self.deltaports = deltaports


def _write_overlay(tmp_path: Path, body: str) -> tuple[Path, _FakePaths]:
    deltaports = tmp_path / "DeltaPorts"
    port_dir = deltaports / "ports" / "lang" / "foo"
    (port_dir / "diffs").mkdir(parents=True)
    (port_dir / "overlay.dops").write_text(body)
    return port_dir / "overlay.dops", _FakePaths(deltaports)


@pytest.fixture
def fake_env(tmp_path: Path, monkeypatch):
    """Write a patch.apply overlay + stub the worker so the loop can
    mutate the overlay via _drop_op_from_overlay_file."""
    overlay, fake_paths = _write_overlay(tmp_path, _PATCH_OVERLAY)

    def make_loop(results):
        fake = _FakeWorker(results, fake_paths)
        from dportsv3.agent import worker as _real_worker
        monkeypatch.setattr(_real_worker, "materialize_dports_with_report",
                            fake.materialize_dports_with_report)
        monkeypatch.setattr(_real_worker, "env_paths", fake.env_paths)
        return overlay

    return make_loop


def _ok():
    return {"ok": True, "rc": 0, "stdout_tail": "done\n"}


def _fail_op(op_id, kind, source_rel, origin="lang/foo", msg=""):
    """Scripted compose failure naming one failing op."""
    abs_path = f"/work/DeltaPorts/ports/{origin}/{source_rel}"
    return {
        "ok": False, "rc": 2, "stdout_tail": "(structured report)",
        "stderr_tail": "",
        "report": {"ok": False, "ports": [{
            "origin": origin,
            "dops_failed_op_results": [_row(op_id, kind, abs_path, msg)],
        }]},
    }


def _patch_fail(source_rel, origin="lang/foo", msg="Hunk #1 failed at 249."):
    return _fail_op("op-0001-patch-apply", "patch.apply", source_rel, origin, msg)


def _qr(tmp_path):
    q = tmp_path / "queue"
    q.mkdir()
    return q


def test_loop_succeeds_first_try_returns_no_defers(fake_env, tmp_path):
    overlay = fake_env([_ok()])
    mat, deferred = _materialize_with_defer_retry(
        "e", "lang/foo", queue_root=_qr(tmp_path), job_id="j", max_drops=3,
    )
    assert mat["ok"] is True
    assert deferred == []
    assert "patch apply diffs/pkg-plist.diff" in overlay.read_text()


def test_loop_drops_one_patch_then_succeeds(fake_env, tmp_path):
    overlay = fake_env([_patch_fail("diffs/pkg-plist.diff"), _ok()])
    mat, deferred = _materialize_with_defer_retry(
        "e", "lang/foo", queue_root=_qr(tmp_path), job_id="j", max_drops=3,
    )
    assert mat["ok"] is True
    assert [d.path for d in deferred] == ["diffs/pkg-plist.diff"]
    # File-backed: backing_file set so cleanup can later remove the diff.
    assert deferred[0].backing_file == "diffs/pkg-plist.diff"
    text = overlay.read_text()
    assert "diffs/pkg-plist.diff" not in text
    assert "diffs/Makefile.diff" in text


def test_loop_drops_two_patches_then_succeeds(fake_env, tmp_path):
    # After the first drop the Makefile patch renumbers to op-0001, so
    # both scripted failures reference op-0001 (the loop always drops
    # the first failing op).
    overlay = fake_env([
        _patch_fail("diffs/pkg-plist.diff"),
        _patch_fail("diffs/Makefile.diff"),
        _ok(),
    ])
    mat, deferred = _materialize_with_defer_retry(
        "e", "lang/foo", queue_root=_qr(tmp_path), job_id="j", max_drops=3,
    )
    assert mat["ok"] is True
    assert [d.path for d in deferred] == [
        "diffs/pkg-plist.diff", "diffs/Makefile.diff",
    ]


def test_loop_respects_max_drops_cap(fake_env, tmp_path):
    overlay = fake_env([
        _patch_fail("diffs/pkg-plist.diff"),
        _patch_fail("diffs/Makefile.diff"),
    ])
    mat, deferred = _materialize_with_defer_retry(
        "e", "lang/foo", queue_root=_qr(tmp_path), job_id="j", max_drops=1,
    )
    assert mat["ok"] is False
    assert [d.path for d in deferred] == ["diffs/pkg-plist.diff"]
    text = overlay.read_text()
    assert "diffs/pkg-plist.diff" not in text
    assert "diffs/Makefile.diff" in text


def test_loop_bails_when_no_failing_ops(fake_env, tmp_path):
    """Compose fails with a shape carrying no op-level failures (e.g.
    a dops parse error). No drop — caller's _fail path takes over."""
    overlay = fake_env([
        {"ok": False, "rc": 1, "stdout_tail": "E_DOPS_PARSE\n",
         "stderr_tail": "", "report": {"ports": [
             {"origin": "lang/foo", "dops_failed_op_results": []}]}},
    ])
    mat, deferred = _materialize_with_defer_retry(
        "e", "lang/foo", queue_root=_qr(tmp_path), job_id="j", max_drops=3,
    )
    assert mat["ok"] is False
    assert deferred == []
    assert "diffs/pkg-plist.diff" in overlay.read_text()


def test_loop_bails_when_op_not_locatable(fake_env, tmp_path):
    """Report names an op id that doesn't exist in the overlay → drop
    returns None → bail rather than spin."""
    overlay = fake_env([
        _fail_op("op-0099-patch-apply", "patch.apply", "diffs/pkg-plist.diff"),
    ])
    mat, deferred = _materialize_with_defer_retry(
        "e", "lang/foo", queue_root=_qr(tmp_path), job_id="j", max_drops=3,
    )
    assert mat["ok"] is False
    assert deferred == []
    assert "diffs/pkg-plist.diff" in overlay.read_text()  # untouched


def test_loop_defers_inline_op(tmp_path, monkeypatch):
    """An inline op (mk.var.set) that fails apply is dropped + deferred
    with a synthetic op:<sha> key, the dropped overlay source as
    original_content, and backing_file=None (nothing on disk)."""
    overlay, fake_paths = _write_overlay(tmp_path, _INLINE_OVERLAY)
    fake = _FakeWorker(
        [_fail_op("op-0001-mk-var-set", "mk.var.set", "Makefile",
                  msg="multiple assignments found for CFLAGS"),
         _ok()],
        fake_paths,
    )
    from dportsv3.agent import worker as _real_worker
    monkeypatch.setattr(_real_worker, "materialize_dports_with_report",
                        fake.materialize_dports_with_report)
    monkeypatch.setattr(_real_worker, "env_paths", fake.env_paths)

    mat, deferred = _materialize_with_defer_retry(
        "e", "lang/foo", queue_root=_qr(tmp_path), job_id="j", max_drops=3,
    )
    assert mat["ok"] is True
    assert len(deferred) == 1
    dp = deferred[0]
    assert dp.path.startswith("op:")
    assert dp.backing_file is None
    assert dp.original_content == 'mk set CFLAGS "-O2"\n'
    assert dp.target_file == "Makefile"
    assert "multiple assignments" in dp.reject_summary
    # The op is gone from the overlay; the rest remains.
    text = overlay.read_text()
    assert 'mk set CFLAGS' not in text
    assert 'text replace-once' in text


def test_loop_defers_duplicate_inline_op_guard(tmp_path, monkeypatch):
    """Two identical inline ops: dropping the first renumbers the
    second to op-0001; the second drop yields the same content key →
    the seen-guard bails instead of spinning."""
    body = (
        "target @any\nport lang/foo\ntype port\n"
        'mk set DUP "1"\n'
        'mk set DUP "1"\n'
    )
    overlay, fake_paths = _write_overlay(tmp_path, body)
    fake = _FakeWorker(
        [_fail_op("op-0001-mk-var-set", "mk.var.set", "Makefile"),
         _fail_op("op-0001-mk-var-set", "mk.var.set", "Makefile")],
        fake_paths,
    )
    from dportsv3.agent import worker as _real_worker
    monkeypatch.setattr(_real_worker, "materialize_dports_with_report",
                        fake.materialize_dports_with_report)
    monkeypatch.setattr(_real_worker, "env_paths", fake.env_paths)

    mat, deferred = _materialize_with_defer_retry(
        "e", "lang/foo", queue_root=_qr(tmp_path), job_id="j", max_drops=5,
    )
    assert mat["ok"] is False
    assert len(deferred) == 1  # bailed on the repeated content key


# --- patch.apply rich context helpers (unchanged behavior) -------------------


def test_infer_target_file_from_plist_diff():
    diff = (
        "--- pkg-plist.orig\t2024-04-20 15:25:52 UTC\n"
        "+++ pkg-plist\n@@ -249,9 +249,6 @@\n line\n"
    )
    assert _infer_target_file_from_diff(diff, fallback="diffs/pkg-plist.diff") == "pkg-plist"


def test_infer_target_file_falls_back_to_path_stem():
    assert _infer_target_file_from_diff("", fallback="diffs/pkg-plist.diff") == "pkg-plist"
    assert _infer_target_file_from_diff("garbage\n", fallback="diffs/Makefile.diff") == "Makefile"


def test_extract_reject_summary_single_hunk():
    diag = "patching pkg-plist...\nHunk #1 failed at 249.\n"
    assert _extract_reject_summary(diag, "diffs/pkg-plist.diff") == "Hunks #1 failed at 249"


def test_extract_reject_summary_multiple_hunks():
    diag = (
        "Hunk #1 failed at 249.\nHunk #2 succeeded at 720.\n"
        "Hunk #3 failed at 2929.\nHunk #4 failed at 2972.\n"
    )
    assert _extract_reject_summary(diag, "diffs/pkg-plist.diff") == (
        "Hunks #1 #3 #4 failed at 249, 2929, 2972"
    )


def test_extract_reject_summary_no_hunks_fallback():
    assert _extract_reject_summary("(no useful diag)", "diffs/x.diff") == (
        "compose rejected diffs/x.diff"
    )


def test_loop_populates_file_backed_deferred_patch(tmp_path, monkeypatch):
    """End-to-end file-backed defer: DeferredPatch carries the diff
    path, target_file from the diff's +++ line, full original_content,
    reject_summary, and backing_file = the diff path."""
    overlay, fake_paths = _write_overlay(
        tmp_path, "target @main\nport lang/foo\npatch apply diffs/pkg-plist.diff\n",
    )
    diff_content = (
        "--- pkg-plist.orig\t2024-04-20 15:25:52 UTC\n"
        "+++ pkg-plist\n@@ -249,9 +249,6 @@\n"
        " %%PYTHON_LIBDIR%%/__pycache__/_strptime.opt-1.pyc\n"
    )
    (overlay.parent / "diffs" / "pkg-plist.diff").write_text(diff_content)

    fake = _FakeWorker(
        [_patch_fail("diffs/pkg-plist.diff",
                     msg="Hunk #1 failed at 249.\nHunk #3 failed at 2929.\n"),
         _ok()],
        fake_paths,
    )
    from dportsv3.agent import worker as _real_worker
    monkeypatch.setattr(_real_worker, "materialize_dports_with_report",
                        fake.materialize_dports_with_report)
    monkeypatch.setattr(_real_worker, "env_paths", fake.env_paths)

    mat, deferred = _materialize_with_defer_retry(
        "e", "lang/foo", queue_root=_qr(tmp_path), job_id="j", max_drops=3,
    )
    assert mat["ok"] is True
    assert len(deferred) == 1
    dp = deferred[0]
    assert dp.path == "diffs/pkg-plist.diff"
    assert dp.backing_file == "diffs/pkg-plist.diff"
    assert dp.target_file == "pkg-plist"
    assert diff_content in dp.original_content
    assert "Hunks #1 #3 failed at 249, 2929" in dp.reject_summary


def test_convert_result_round_trips_deferred_patches(tmp_path):
    """ConvertResult with mixed file-backed + inline deferred patches
    serializes and rehydrates, preserving backing_file."""
    from dportsv3.agent.phase_result import (
        ConvertResult, DeferredPatch, load_phase_result, write_phase_result,
    )
    from dportsv3.agent import runner as runner_mod
    import json
    import pytest as _pytest

    saved = {}

    def fake_put(bundle_id, relpath, data, _kind):
        saved[(bundle_id, relpath)] = data
        return True

    def fake_read(_bundle_dir, bundle_id, relpath):
        data = saved.get((bundle_id, relpath))
        return data.decode("utf-8") if data else None

    monkey = _pytest.MonkeyPatch()
    monkey.setattr(runner_mod, "artifact_store_put", fake_put)
    monkey.setattr(runner_mod, "read_bundle_text", fake_read)
    try:
        result = ConvertResult(
            status="verified", reapply_ok=True, reason_code=None,
            overlay_sha256="abc", files_removed=[], diag_tail=None,
            tokens_prompt=0, tokens_completion=0, tokens_total=0,
            deferred_patches=[
                DeferredPatch(
                    path="diffs/pkg-plist.diff", target_file="pkg-plist",
                    original_content="--- a\n+++ b\n",
                    reject_summary="Hunks #1 failed",
                    backing_file="diffs/pkg-plist.diff"),
                DeferredPatch(
                    path="op:deadbeef0000", target_file="Makefile",
                    original_content='mk set X "1"\n',
                    reject_summary="ambiguous", backing_file=None),
            ],
        )
        write_phase_result("b-X", "convert", result)
        payload = json.loads(saved[("b-X", "analysis/convert_result.json")].decode())
        assert payload["deferred_patches"][1]["backing_file"] is None

        loaded = load_phase_result(None, "b-X", "convert", ConvertResult)
        assert loaded.deferred_patches[0].backing_file == "diffs/pkg-plist.diff"
        assert loaded.deferred_patches[1].backing_file is None
    finally:
        monkey.undo()
