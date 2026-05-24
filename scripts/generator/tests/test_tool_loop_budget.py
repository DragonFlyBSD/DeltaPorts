"""Token-budget containment for the inner tool loop."""

from __future__ import annotations


def test_tool_loop_stops_before_dispatch_after_budget_crossing_turn(monkeypatch) -> None:
    """A single oversized LLM turn can exceed the budget; once known,
    do not execute its requested tools or continue the conversation."""
    from dportsv3.agent import llm, tool_loop, tools

    def fake_complete(*args, **kwargs):
        return llm.Response(
            text="",
            tool_calls=[llm.ToolCall(id="tc-1", name="env_verify", arguments={})],
            usage=llm.Usage(prompt_tokens=90, completion_tokens=20, total_tokens=110),
        )

    dispatched: list[str] = []

    def fake_dispatch(name, arguments, *, env):
        dispatched.append(name)
        return {"ok": True}

    events: list[dict] = []
    monkeypatch.setattr(llm, "complete", fake_complete)
    monkeypatch.setattr(tools, "dispatch", fake_dispatch)

    response, usage = tool_loop.run(
        [{"role": "user", "content": "x"}],
        model="test-model",
        env="test-env",
        max_tokens=100,
        on_event=events.append,
    )

    assert response.tool_calls
    assert usage.total_tokens == 110
    assert dispatched == []
    budget_events = [e for e in events if e.get("type") == "token_budget_exhausted"]
    assert len(budget_events) == 1
    assert budget_events[0]["phase"] == "after_llm_turn"
    assert budget_events[0]["tools_skipped"] == ["env_verify"]


def test_attempt_loop_reports_budget_exhausted_after_inner_overshoot(monkeypatch) -> None:
    """attempt_loop should surface budget-exhausted when the inner loop
    returns an over-budget turn."""
    from dportsv3.agent import attempt_loop
    from dportsv3.agent.llm import Response, Usage
    from dportsv3.agent.policy import Tier

    def fake_tool_loop_run(*args, **kwargs):
        return Response(text="no proof"), Usage(
            prompt_tokens=90, completion_tokens=20, total_tokens=110,
        )

    monkeypatch.setattr(attempt_loop.tool_loop, "run", fake_tool_loop_run)

    result = attempt_loop.run(
        "payload",
        tier=Tier(name="AUTO", max_iterations=2, max_tokens=100),
        env="test-env",
        model="test-model",
    )

    assert result.status == "budget-exhausted"
    assert result.usage.total_tokens == 110
    assert len(result.attempts) == 1
