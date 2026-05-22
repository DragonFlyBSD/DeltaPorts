"""Per-turn token telemetry — pinpoint where the budget actually goes.

Smoke surfaced the patch agent burning 1M+ tokens per attempt with no
visibility into WHERE the tokens went. Per-attempt totals tell us
the bill; per-turn breakdown tells us whether it's the model's
deliberation (completion tokens) or the conversation history
compounding (prompt tokens), and which tool calls happened
adjacent to the expensive turns.

This test pins the event shape and the dispatcher routing so we keep
the data going forward.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest


_GEN = Path(__file__).resolve().parents[1]
if str(_GEN) not in sys.path:
    sys.path.insert(0, str(_GEN))


# --- tool_loop emits llm_turn events ----------------------------------------


def test_tool_loop_emits_llm_turn_with_full_breakdown(monkeypatch):
    """Every call to llm.complete inside tool_loop produces an
    llm_turn event with prompt/completion/total + cumulative."""
    from dportsv3.agent import tool_loop, llm
    from dportsv3.agent.llm import Response, Usage

    # Fake completions: turn 1 calls tool, turn 2 returns text-only.
    fake_responses = [
        Response(
            text="thinking",
            tool_calls=[MagicMock(id="c1", name="env_verify", arguments={})],
            usage=Usage(prompt_tokens=1000, completion_tokens=50,
                        total_tokens=1050),
        ),
        Response(
            text="done.",
            tool_calls=[],
            usage=Usage(prompt_tokens=1200, completion_tokens=20,
                        total_tokens=1220),
        ),
    ]
    fake_iter = iter(fake_responses)
    monkeypatch.setattr(llm, "complete",
                        lambda *a, **kw: next(fake_iter))

    # Stub tools so dispatch returns a trivial result.
    fake_tools = MagicMock()
    fake_tools.schemas.return_value = []
    fake_tools.dispatch.return_value = {"ok": True}
    monkeypatch.setattr(tool_loop, "tools", fake_tools)

    events: list[dict] = []

    response, usage = tool_loop.run(
        messages=[{"role": "user", "content": "hi"}],
        env="testenv",
        model="fake",
        attempt_idx=1,
        on_event=events.append,
    )

    llm_turns = [e for e in events if e["type"] == "llm_turn"]
    assert len(llm_turns) == 2

    # Turn 1: tool call.
    t1 = llm_turns[0]
    assert t1["attempt"] == 1
    assert t1["turn"] == 1
    assert t1["prompt_tokens"] == 1000
    assert t1["completion_tokens"] == 50
    assert t1["total_tokens"] == 1050
    assert t1["cumulative_total_tokens"] == 1050
    assert t1["text_only"] is False
    assert t1["tools_requested"]    # any non-empty

    # Turn 2: text-only.
    t2 = llm_turns[1]
    assert t2["turn"] == 2
    assert t2["prompt_tokens"] == 1200
    assert t2["completion_tokens"] == 20
    assert t2["total_tokens"] == 1220
    assert t2["cumulative_total_tokens"] == 1050 + 1220
    assert t2["text_only"] is True
    assert t2["tools_requested"] == []


def test_tool_loop_emits_llm_turn_even_if_callback_raises(monkeypatch):
    """A broken on_event callback must not break the loop or skip
    later turns. The exception is swallowed."""
    from dportsv3.agent import tool_loop, llm
    from dportsv3.agent.llm import Response, Usage

    monkeypatch.setattr(llm, "complete", lambda *a, **kw: Response(
        text="ok", tool_calls=[], usage=Usage(total_tokens=10),
    ))
    fake_tools = MagicMock()
    fake_tools.schemas.return_value = []
    monkeypatch.setattr(tool_loop, "tools", fake_tools)

    def bad_callback(ev):
        raise RuntimeError("intentional")

    # Must not raise.
    response, usage = tool_loop.run(
        messages=[{"role": "user", "content": "x"}],
        env="testenv", model="fake", attempt_idx=1,
        on_event=bad_callback,
    )
    assert usage.total_tokens == 10


# --- dispatcher routes llm_turn to activity_log ----------------------------


def test_patch_dispatcher_routes_llm_turn_to_activity_log():
    """PatchEventDispatcher must surface llm_turn events into the
    activity_log so the operator can scan them in the UI."""
    from dportsv3.agent.steps import PatchEventDispatcher

    activity_calls: list = []

    def fake_activity_log(queue_root, stage, message, *,
                          job_id=None, duration_ms=None, extra=None):
        activity_calls.append({
            "stage": stage, "message": message, "extra": extra or {},
        })

    dispatcher = PatchEventDispatcher(
        queue_root=Path("/tmp/q"), job_id="job-1", origin="devel/foo",
        activity_log=fake_activity_log,
        looks_env_suspicious=lambda res: False,
        invalidate_health_cache=lambda: None,
        summarize_tool_call=lambda t, a, r: "",
    )

    dispatcher({
        "type": "llm_turn",
        "attempt": 2, "turn": 5,
        "prompt_tokens": 8000, "completion_tokens": 250,
        "total_tokens": 8250, "cumulative_total_tokens": 425000,
        "tools_requested": ["get_file", "list_dir"],
        "text_only": False,
    })

    rows = [c for c in activity_calls if c["stage"] == "llm_turn"]
    assert len(rows) == 1
    msg = rows[0]["message"]
    assert "A2.T5" in msg
    assert "in=8000" in msg
    assert "out=250" in msg
    assert "total=8250" in msg
    assert "cumulative=425000" in msg
    assert "get_file" in msg or "list_dir" in msg
    # Raw event payload preserved in extra for analytics.
    extra = rows[0]["extra"]
    assert extra["prompt_tokens"] == 8000
    assert extra["completion_tokens"] == 250


def test_patch_dispatcher_marks_text_only_turns():
    """A text-only llm_turn (no tool calls — typically the final
    answer) renders distinguishably so operators can spot the
    'wrote the final answer' moment."""
    from dportsv3.agent.steps import PatchEventDispatcher

    rows: list = []
    dispatcher = PatchEventDispatcher(
        queue_root=Path("/tmp/q"), job_id="job-2", origin="devel/bar",
        activity_log=lambda *a, **kw: rows.append(kw.get("extra") or {}),
        looks_env_suspicious=lambda res: False,
        invalidate_health_cache=lambda: None,
        summarize_tool_call=lambda t, a, r: "",
    )
    dispatcher({
        "type": "llm_turn",
        "attempt": 1, "turn": 7,
        "prompt_tokens": 50000, "completion_tokens": 800,
        "total_tokens": 50800, "cumulative_total_tokens": 300000,
        "tools_requested": [],
        "text_only": True,
    })
    assert rows[0]["text_only"] is True
    assert rows[0]["tools_requested"] == []


# --- triage telemetry ------------------------------------------------------


def test_triage_emits_llm_turn_for_each_snippet_round(monkeypatch, tmp_path):
    """Triage's loop can run multiple snippet rounds; each
    llm.complete must emit an llm_turn so the operator can see
    whether the snippet rounds were expensive."""
    from dportsv3.agent import triage, llm
    from dportsv3.agent.llm import Response, Usage

    # Two-round triage: first response has snippet requests, second
    # does not (so the loop exits).
    fake_responses = [
        Response(
            text=(
                "## Classification\npatch-error\n\n"
                "## Confidence\nhigh\n\n"
                "## Snippet Requests\n- file `/tmp/x`\n"
            ),
            tool_calls=[],
            usage=Usage(prompt_tokens=2000, completion_tokens=300,
                        total_tokens=2300),
        ),
        Response(
            text=(
                "## Classification\npatch-error\n\n"
                "## Confidence\nhigh\n"
            ),
            tool_calls=[],
            usage=Usage(prompt_tokens=2500, completion_tokens=200,
                        total_tokens=2700),
        ),
    ]
    fake_iter = iter(fake_responses)
    monkeypatch.setattr(llm, "complete", lambda *a, **kw: next(fake_iter))

    # Stub snippet extraction so the first response actually triggers
    # a second round.
    monkeypatch.setattr(triage.snippets, "extract_round",
                        lambda bd, n: (0, ["/tmp/x"]))
    monkeypatch.setattr(triage.snippets, "format_for_prompt",
                        lambda bd, files: "<snippet body>")

    events: list[dict] = []
    result = triage.run(
        payload="(payload)",
        bundle_dir=tmp_path,
        model="fake",
        on_event=events.append,
    )

    llm_turns = [e for e in events if e["type"] == "llm_turn"]
    assert len(llm_turns) == 2
    # Snippet round = 0 for the first (pre-snippet) turn, = 1 for the second.
    assert llm_turns[0]["snippet_round"] == 0
    assert llm_turns[1]["snippet_round"] == 1
    # Cumulative grows.
    assert (llm_turns[1]["cumulative_total_tokens"]
            > llm_turns[0]["cumulative_total_tokens"])
    assert result.snippet_rounds == 1
