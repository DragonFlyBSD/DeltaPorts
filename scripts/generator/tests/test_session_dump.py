"""Tests for the optional session-dump module.

Covers:
- Gate behavior (env var present/absent, truthy/falsy values).
- Tool-result content head+tail truncation.
- Other-role messages pass through unchanged.
- Relpath shape.
- make_dumper short-circuits to None when gate is off or bundle_id
  is missing — the loops short-circuit on None too, no work done.
"""

from __future__ import annotations

import gzip
import json

import pytest

from dportsv3.agent import session_dump


# --- gate ---


def test_enabled_falsy_by_default(monkeypatch):
    monkeypatch.delenv("DP_HARNESS_DUMP_SESSION", raising=False)
    assert session_dump.enabled() is False


@pytest.mark.parametrize("val", ["1", "true", "yes", "on", "TRUE", "On"])
def test_enabled_truthy_values(monkeypatch, val):
    monkeypatch.setenv("DP_HARNESS_DUMP_SESSION", val)
    assert session_dump.enabled() is True


@pytest.mark.parametrize("val", ["", "0", "false", "no", "off", "garbage"])
def test_enabled_falsy_values(monkeypatch, val):
    monkeypatch.setenv("DP_HARNESS_DUMP_SESSION", val)
    assert session_dump.enabled() is False


# --- content cap ---


def test_truncate_below_cap_passthrough():
    text = "hello world"
    out = session_dump._truncate_head_tail(text, 1024)
    assert out == text


def test_truncate_above_cap_head_and_tail():
    text = "A" * 4000 + "B" * 4000
    out = session_dump._truncate_head_tail(text, 2048)
    assert out.startswith("A" * 1024)
    assert out.endswith("B" * 1024)
    assert "session_dump elided" in out
    assert len(out) < len(text)


def test_redact_message_tool_role_truncated():
    msg = {"role": "tool", "tool_call_id": "x", "content": "Y" * 50000}
    out = session_dump._redact_message(msg, cap=2048)
    assert out["role"] == "tool"
    assert out["tool_call_id"] == "x"
    assert len(out["content"]) < 50000
    assert "elided" in out["content"]


def test_redact_message_non_tool_passthrough():
    """Assistant / user / system content is the LLM's own tokens —
    that's the signal we want to read intact."""
    for role in ("system", "user", "assistant"):
        msg = {"role": role, "content": "Z" * 50000}
        out = session_dump._redact_message(msg, cap=2048)
        assert out["content"] == "Z" * 50000


# --- dump_attempt ---


def _make_messages(tool_content_size: int = 100):
    return [
        {"role": "system", "content": "system prompt"},
        {"role": "user", "content": "user payload"},
        {"role": "assistant", "content": "thinking",
         "tool_calls": [{"id": "call1", "function": {"name": "grep"}}]},
        {"role": "tool", "tool_call_id": "call1",
         "content": "X" * tool_content_size},
        {"role": "assistant", "content": "done"},
    ]


def test_dump_skips_when_gate_off(monkeypatch):
    monkeypatch.delenv("DP_HARNESS_DUMP_SESSION", raising=False)
    saved = {}

    def fake_put(bundle_id, relpath, data, kind):
        saved[(bundle_id, relpath)] = (data, kind)
        return True

    ok = session_dump.dump_attempt(
        bundle_id="b1", job_id="j1", attempt_idx=1,
        messages=_make_messages(), put_artifact=fake_put,
    )
    assert ok is False
    assert saved == {}


def test_dump_skips_when_no_bundle_id(monkeypatch):
    monkeypatch.setenv("DP_HARNESS_DUMP_SESSION", "1")
    saved = {}
    ok = session_dump.dump_attempt(
        bundle_id=None, job_id="j1", attempt_idx=1,
        messages=_make_messages(), put_artifact=lambda *a: True,
    )
    assert ok is False


def test_dump_writes_gzipped_jsonl(monkeypatch):
    monkeypatch.setenv("DP_HARNESS_DUMP_SESSION", "1")
    saved = {}

    def fake_put(bundle_id, relpath, data, kind):
        saved[(bundle_id, relpath)] = (data, kind)
        return True

    ok = session_dump.dump_attempt(
        bundle_id="bundle-x", job_id="job-y", attempt_idx=2,
        messages=_make_messages(), put_artifact=fake_put,
    )
    assert ok is True
    key = ("bundle-x", "analysis/sessions/job-y.attempt2.jsonl.gz")
    assert key in saved
    data, kind = saved[key]
    assert kind == "gzip"
    # Verify it's actually gzipped + parseable line-by-line.
    raw = gzip.decompress(data).decode("utf-8")
    lines = [ln for ln in raw.split("\n") if ln]
    assert len(lines) == 5  # one per message
    parsed = [json.loads(ln) for ln in lines]
    assert parsed[0]["role"] == "system"
    assert parsed[-1]["role"] == "assistant"


def test_dump_caps_tool_content(monkeypatch):
    monkeypatch.setenv("DP_HARNESS_DUMP_SESSION", "1")
    monkeypatch.setenv("DP_HARNESS_DUMP_SESSION_CAP", "2048")
    saved = {}

    def fake_put(bundle_id, relpath, data, kind):
        saved[relpath] = data
        return True

    session_dump.dump_attempt(
        bundle_id="b1", job_id="j1", attempt_idx=1,
        messages=_make_messages(tool_content_size=50000),
        put_artifact=fake_put,
    )
    raw = gzip.decompress(next(iter(saved.values()))).decode("utf-8")
    # Tool message content was truncated; the resulting payload is
    # vastly smaller than the 50KB input would have been.
    assert "session_dump elided" in raw
    # System/user/assistant survive unchanged.
    assert "system prompt" in raw
    assert "user payload" in raw


def test_dump_swallows_put_artifact_failure(monkeypatch, caplog):
    monkeypatch.setenv("DP_HARNESS_DUMP_SESSION", "1")

    def put_raises(*_args):
        raise RuntimeError("boom")

    ok = session_dump.dump_attempt(
        bundle_id="b1", job_id="j1", attempt_idx=1,
        messages=_make_messages(), put_artifact=put_raises,
    )
    assert ok is False
    # Sanity: failure logged at WARN so operators see it.
    assert any("put_artifact failed" in r.message for r in caplog.records)


# --- make_dumper ---


def test_make_dumper_returns_none_when_gate_off(monkeypatch):
    monkeypatch.delenv("DP_HARNESS_DUMP_SESSION", raising=False)
    assert session_dump.make_dumper(
        bundle_id="b", job_id="j", put_artifact=lambda *a: True,
    ) is None


def test_make_dumper_returns_none_without_bundle_id(monkeypatch):
    monkeypatch.setenv("DP_HARNESS_DUMP_SESSION", "1")
    assert session_dump.make_dumper(
        bundle_id=None, job_id="j", put_artifact=lambda *a: True,
    ) is None


def test_make_dumper_returns_callable_when_enabled(monkeypatch):
    monkeypatch.setenv("DP_HARNESS_DUMP_SESSION", "1")
    saved = {}

    def fake_put(b, r, d, k):
        saved[r] = (d, k)
        return True

    dumper = session_dump.make_dumper(
        bundle_id="b", job_id="j", put_artifact=fake_put,
    )
    assert dumper is not None
    dumper(3, _make_messages())
    assert "analysis/sessions/j.attempt3.jsonl.gz" in saved
