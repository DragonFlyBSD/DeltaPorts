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

    response, usage, _rebuild_ok_seen = tool_loop.run(
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
        return (
            Response(text="no proof"),
            Usage(prompt_tokens=90, completion_tokens=20, total_tokens=110),
            False,
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


def test_tool_loop_extends_cap_after_rebuild_ok_seen(monkeypatch) -> None:
    """Once a tool result returns ``rebuild_ok=True``, the inner loop's
    cap is extended by the grace window so the closing turns get to run
    even when the attempt budget was already on the edge."""
    from dportsv3.agent import llm, tool_loop, tools

    turns: list[int] = []

    def fake_complete(*args, **kwargs):
        turn = len(turns) + 1
        turns.append(turn)
        if turn == 1:
            return llm.Response(
                text="",
                tool_calls=[llm.ToolCall(id="tc-1", name="dsynth_build", arguments={})],
                usage=llm.Usage(prompt_tokens=80, completion_tokens=10, total_tokens=90),
            )
        # Closing text-only turn — would have been refused without grace.
        return llm.Response(
            text="## Rebuild Proof (JSON)\n```json\n{\"rebuild_ok\": true}\n```",
            tool_calls=[],
            usage=llm.Usage(prompt_tokens=20, completion_tokens=10, total_tokens=30),
        )

    def fake_dispatch(name, arguments, *, env):
        return {"ok": True, "rebuild_ok": True}

    events: list[dict] = []
    monkeypatch.setattr(llm, "complete", fake_complete)
    monkeypatch.setattr(tools, "dispatch", fake_dispatch)

    response, usage, rebuild_ok_seen = tool_loop.run(
        [{"role": "user", "content": "x"}],
        model="test-model",
        env="test-env",
        max_tokens=100,
        on_event=events.append,
    )

    # First turn (90) crossed the static cap, but grace was unlocked by
    # the tool returning rebuild_ok, so the second turn (30 more) ran.
    assert rebuild_ok_seen is True
    assert usage.total_tokens == 120
    assert "Rebuild Proof" in (response.text or "")
    rok_events = [e for e in events if e.get("type") == "rebuild_ok_seen"]
    assert len(rok_events) == 1
    assert rok_events[0]["tool"] == "dsynth_build"


def test_attempt_loop_synthesizes_proof_on_orphan(monkeypatch) -> None:
    """If a tool already returned rebuild_ok=True but the LLM never
    emitted the ## Rebuild Proof block (e.g. ran out during the closing
    turn), attempt_loop should lift the success from the tool result
    rather than report budget-exhausted."""
    from dportsv3.agent import attempt_loop
    from dportsv3.agent.llm import Response, Usage
    from dportsv3.agent.policy import Tier

    def fake_tool_loop_run(*args, **kwargs):
        return (
            Response(text="Build succeeded. Let me verify..."),
            Usage(prompt_tokens=900, completion_tokens=80, total_tokens=980),
            True,  # rebuild_ok_seen — the tool said so
        )

    monkeypatch.setattr(attempt_loop.tool_loop, "run", fake_tool_loop_run)

    result = attempt_loop.run(
        "payload",
        tier=Tier(name="ASSIST", max_iterations=2, max_tokens=1000),
        env="test-env",
        model="test-model",
    )

    assert result.status == "success"
    assert result.proof == {"rebuild_ok": True, "source": "tool_result"}
    assert result.attempts[-1].rebuild_ok is True
