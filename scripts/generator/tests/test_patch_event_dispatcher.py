"""Unit tests for PatchEventDispatcher.

Phase 5 Substep 3a. Exercises the routing logic that decides which
activity_log entry to emit per event type, the env-suspicious tool-
result force-invalidate, and the trace accumulation.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dportsv3.agent.steps import PatchEventDispatcher


# --- helpers ------------------------------------------------------------------


class _LogRecorder:
    """Capture activity_log invocations as (stage, message, extra)."""

    def __init__(self):
        self.entries: list[tuple[str, str, dict | None]] = []

    def __call__(self, queue_root, stage, message,
                 job_id=None, duration_ms=None, extra=None):
        self.entries.append((stage, message, dict(extra) if extra else None))


def _make_dispatcher(**overrides):
    log = overrides.pop("activity_log", _LogRecorder())
    looks_env = overrides.pop("looks_env_suspicious", lambda res: False)
    invalidate_calls = overrides.pop("_invalidate_calls", [])
    invalidate = overrides.pop("invalidate_health_cache",
                               lambda: invalidate_calls.append(True))
    summarize = overrides.pop("summarize_tool_call",
                              lambda tool, args, res: f"summary({tool})")
    d = PatchEventDispatcher(
        queue_root=Path("/tmp/x"),
        job_id="job-1",
        origin="devel/foo",
        activity_log=log,
        looks_env_suspicious=looks_env,
        invalidate_health_cache=invalidate,
        summarize_tool_call=summarize,
        **overrides,
    )
    # Attach recorders for test convenience.
    d._log = log
    d._invalidate_calls = invalidate_calls
    return d


# --- event routing -----------------------------------------------------------


def test_attempt_start_logs_one_row():
    d = _make_dispatcher()
    d({"type": "attempt_start",
       "attempt": 1, "iterations": 3,
       "tokens_used_so_far": 0, "budget": 30000})
    stages = [e[0] for e in d._log.entries]
    assert stages == ["attempt_start"]
    msg = d._log.entries[0][1]
    assert "attempt 1/3" in msg
    assert "devel/foo" in msg
    assert "tokens used 0/30000" in msg


def test_attempt_end_logs_one_row_with_rebuild_ok():
    d = _make_dispatcher()
    d({"type": "attempt_end",
       "attempt": 1, "rebuild_ok": True, "tokens": 1234})
    stages = [e[0] for e in d._log.entries]
    assert stages == ["attempt_end"]
    msg = d._log.entries[0][1]
    assert "attempt 1 for devel/foo" in msg
    assert "rebuild_ok=True" in msg
    assert "tokens=1234" in msg


def test_tool_call_logs_with_tool_prefixed_stage_and_summary():
    d = _make_dispatcher(
        summarize_tool_call=lambda tool, args, res: f"path={args.get('path')}",
    )
    d({"type": "tool_call",
       "tool": "put_file",
       "args": {"path": "ports/foo/Makefile"},
       "result": {"ok": True},
       "attempt": 1, "turn": 3, "duration_ms": 17})
    assert d._log.entries[0][0] == "tool:put_file"
    assert d._log.entries[0][1] == "path=ports/foo/Makefile"
    extra = d._log.entries[0][2]
    assert extra["attempt"] == 1
    assert extra["turn"] == 3
    assert extra["ok"] is True


def test_tool_call_with_failure_result_records_ok_false():
    d = _make_dispatcher()
    d({"type": "tool_call",
       "tool": "dsynth_build",
       "args": {"origin": "devel/foo"},
       "result": {"ok": False},
       "attempt": 1, "turn": 5})
    assert d._log.entries[0][2]["ok"] is False


def test_unknown_event_type_no_log_no_crash():
    d = _make_dispatcher()
    d({"type": "weather_changed"})
    assert d._log.entries == []
    # But it still lands in trace_events.
    assert d.trace_events == [{"type": "weather_changed"}]


# --- env-suspicious tool results --------------------------------------------


def test_env_suspicious_tool_result_invalidates_cache():
    d = _make_dispatcher(looks_env_suspicious=lambda res: True)
    d({"type": "tool_call",
       "tool": "materialize_dports",
       "args": {"origin": "devel/foo"},
       "result": {"ok": False, "stderr_tail": "missing DragonFly packages"}})
    assert d._invalidate_calls == [True]
    # Also emits the health_recheck_forced log row + the tool log row.
    stages = [e[0] for e in d._log.entries]
    assert "health_recheck_forced" in stages
    assert "tool:materialize_dports" in stages


def test_env_suspicious_ignored_for_non_tool_events():
    """The env-suspicious check only runs on tool_call events; an
    attempt_end with a "stderr" key isn't reinterpreted as a tool
    failure."""
    d = _make_dispatcher(looks_env_suspicious=lambda res: True)
    d({"type": "attempt_end", "attempt": 1,
       "rebuild_ok": False, "tokens": 100})
    assert d._invalidate_calls == []


def test_invalidate_exception_does_not_break_dispatch():
    """If the cache invalidate raises, the dispatcher still logs +
    accumulates the event."""
    def raising_invalidate():
        raise RuntimeError("invalidate-boom")
    d = _make_dispatcher(
        looks_env_suspicious=lambda res: True,
        invalidate_health_cache=raising_invalidate,
    )
    d({"type": "tool_call", "tool": "x", "args": {}, "result": {"ok": False}})
    assert len(d.trace_events) == 1
    stages = [e[0] for e in d._log.entries]
    assert "health_recheck_forced" in stages
    assert "tool:x" in stages


# --- trace accumulation ------------------------------------------------------


def test_trace_events_accumulate_in_order():
    d = _make_dispatcher()
    d({"type": "attempt_start", "attempt": 1})
    d({"type": "tool_call", "tool": "a", "args": {}, "result": {"ok": True}})
    d({"type": "tool_call", "tool": "b", "args": {}, "result": {"ok": True}})
    d({"type": "attempt_end", "attempt": 1})
    types = [e["type"] for e in d.trace_events]
    assert types == ["attempt_start", "tool_call", "tool_call", "attempt_end"]


def test_trace_starts_empty():
    d = _make_dispatcher()
    assert d.trace_events == []


def test_trace_separated_across_instances():
    """Two dispatcher instances don't share their trace_events list
    (mutable defaults bug guard)."""
    d1 = _make_dispatcher()
    d2 = _make_dispatcher()
    d1({"type": "attempt_start", "attempt": 1})
    assert d1.trace_events != []
    assert d2.trace_events == []
