"""Regression for attempt-boundary amnesia: attempt 2 must receive
a summary of attempt 1's intents so the agent doesn't re-emit an
intent that already failed (the symptom observed on the redis
smoke-test where attempt 2's seq 2 == attempt 1's seq 0).
"""

from __future__ import annotations

from dportsv3.agent import attempt_loop, patch as patch_mod
from dportsv3.agent import worker as worker_mod
from dportsv3.agent.edit_intent import IntentLog
from dportsv3.agent.edit_intent.log import IntentLogEntry


def _log_with(intents: list[dict], oks: list[bool] | None = None) -> IntentLog:
    log = IntentLog(
        origin="cat/x", target="@main",
        mode_at_apply="dops", baseline_commit="abc123",
    )
    oks = oks or [True] * len(intents)
    for seq, (intent, ok) in enumerate(zip(intents, oks)):
        log.intents.append(IntentLogEntry(
            seq=seq, intent=intent, applied_at="t",
            ok=ok, substrate_diff="", error=None if ok else "boom",
        ))
    return log


# -----------------------------------------------------------------
# Formatter
# -----------------------------------------------------------------


def test_format_intent_log_summary_one_line_per_entry():
    log = _log_with([
        {"type": "change_makefile", "key": "BINARY_ALIAS",
         "value": "gmd5sum=md5 -r", "op": "set"},
        {"type": "change_makefile", "key": "BINARY_ALIAS",
         "value": "gmd5sum=md5", "op": "set"},
    ])
    text = patch_mod._format_intent_log_summary(log)
    assert "seq 0:" in text
    assert "seq 1:" in text
    assert "change_makefile(BINARY_ALIAS=gmd5sum=md5 -r) ok" in text
    assert "change_makefile(BINARY_ALIAS=gmd5sum=md5) ok" in text


def test_format_intent_log_summary_flags_failed_entries():
    log = _log_with(
        [{"type": "change_makefile", "key": "K", "value": "v", "op": "set"}],
        oks=[False],
    )
    text = patch_mod._format_intent_log_summary(log)
    assert "FAIL[boom]" in text


def test_format_intent_log_summary_caps_long_logs():
    log = _log_with([
        {"type": "bump_portrevision"} for _ in range(60)
    ])
    text = patch_mod._format_intent_log_summary(log)
    # 50 entries rendered + a truncation note
    assert text.count("\n- seq") == 49  # 50 entries, first has no leading \n
    assert "10 more entries truncated" in text


# -----------------------------------------------------------------
# Provider
# -----------------------------------------------------------------


def test_provider_returns_none_when_no_log():
    class FakeWorker:
        peek_intent_log = staticmethod(lambda env, origin: None)
    prov = patch_mod._build_intent_summary_provider(FakeWorker, "env", "cat/x")
    assert prov() is None


def test_provider_returns_none_when_log_empty():
    empty = _log_with([])
    class FakeWorker:
        peek_intent_log = staticmethod(lambda env, origin: empty)
    prov = patch_mod._build_intent_summary_provider(FakeWorker, "env", "cat/x")
    assert prov() is None


def test_provider_reflects_log_at_call_time_not_construction_time():
    """The closure must re-peek each call so additions between attempts
    are visible. Without this the attempt-2 summary would be a snapshot
    from before attempt 1 even started."""
    log = _log_with([])

    class FakeWorker:
        peek_intent_log = staticmethod(lambda env, origin: log)

    prov = patch_mod._build_intent_summary_provider(FakeWorker, "env", "cat/x")
    assert prov() is None

    log.intents.append(IntentLogEntry(
        seq=0,
        intent={"type": "change_makefile", "key": "K", "value": "v", "op": "set"},
        applied_at="t", ok=True, substrate_diff="", error=None,
    ))
    assert "seq 0" in (prov() or "")


# -----------------------------------------------------------------
# attempt_loop integration
# -----------------------------------------------------------------


def test_failure_context_message_includes_prior_summary_when_given():
    msg = attempt_loop._failure_context_message(
        1, "prior text",
        prior_summary="- seq 0: change_makefile(K=v) ok",
    )
    assert "Prior-attempt actions" in msg["content"]
    assert "seq 0: change_makefile(K=v) ok" in msg["content"]


def test_failure_context_message_omits_section_when_no_summary():
    msg = attempt_loop._failure_context_message(1, "prior text", None)
    assert "Prior-attempt actions" not in msg["content"]


# -----------------------------------------------------------------
# Worker peek is non-destructive
# -----------------------------------------------------------------


def test_peek_intent_log_does_not_drain():
    """peek_intent_log must NOT remove the log — otherwise the
    end-of-run drain_intent_log gets nothing and the bundle loses
    its canonical record."""
    key = ("env-x", "cat/x")
    log = IntentLog(
        origin="cat/x", target="@main",
        mode_at_apply="dops", baseline_commit="abc",
    )
    worker_mod._INTENT_LOGS[key] = log
    try:
        peeked1 = worker_mod.peek_intent_log("env-x", "cat/x")
        peeked2 = worker_mod.peek_intent_log("env-x", "cat/x")
        assert peeked1 is log
        assert peeked2 is log
        # Now drain — must still find it.
        drained = worker_mod.drain_intent_log("env-x", "cat/x")
        assert drained is log
        # And drain consumed it.
        assert worker_mod.peek_intent_log("env-x", "cat/x") is None
    finally:
        worker_mod._INTENT_LOGS.pop(key, None)
