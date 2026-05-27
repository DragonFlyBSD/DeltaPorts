"""Step 29a: triage prompt + payload route operator context as
first-class evidence.

Two layered assertions:

1. ``TRIAGE_SYSTEM`` contains the "consult operator context FIRST"
   section so the model is told to weigh operator context ahead of
   the bundle's mechanical signals.
2. ``build_triage_payload`` renders a ``## User Context`` block
   when ``get_user_context`` returns non-empty text, so the prompt
   instruction has something to match against at runtime.

Neither assertion proves the LLM will reclassify — that requires a
live model call. They pin the input-side contract: prompt section
is present, and operator text reaches the payload.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dportsv3.agent import prompts, runner


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def _minimal_bundle(tmp_path: Path) -> Path:
    bdir = tmp_path / "bundle"
    _write(bdir / "logs" / "errors.txt", "synthetic compile error\n")
    _write(bdir / "port" / "Makefile", "PORTNAME=foo\n")
    return bdir


def test_triage_system_instructs_to_consult_user_context_first():
    sys_prompt = prompts.TRIAGE_SYSTEM
    # Section heading is the contract the runtime section emitter
    # matches against; both must reference the same phrase.
    assert '## If the payload contains "## User Context"' in sys_prompt
    # The "FIRST, before you classify" framing is load-bearing —
    # the whole point of 29a is that the model consults context
    # before classification, not as a post-hoc override.
    assert "Read the User Context section FIRST" in sys_prompt
    assert "before you classify" in sys_prompt
    # Escape valve: model may still disagree on strong contrary
    # bundle evidence, but must say so in Notes.
    assert "you may still disagree" in sys_prompt.lower()


@pytest.fixture(autouse=True)
def _no_db(monkeypatch):
    """build_triage_payload reads get_user_context from the runner
    module; default it to "no context" so the per-test override is
    explicit."""
    monkeypatch.setattr(runner, "get_user_context",
                        lambda *a, **kw: (None, 0))


def test_payload_omits_user_context_section_when_none(tmp_path):
    bdir = _minimal_bundle(tmp_path)
    job = {"origin": "devel/foo", "snippet_round": "0",
           "has_snippets": "false"}
    payload = runner.build_triage_payload(bdir, None, job)
    assert "## User Context" not in payload


def test_payload_includes_user_context_section_when_present(
    tmp_path, monkeypatch,
):
    bdir = _minimal_bundle(tmp_path)
    operator_text = (
        "The dependency reported missing is actually installed under "
        "a different name; check the configure shim."
    )
    monkeypatch.setattr(
        runner, "get_user_context",
        lambda *a, **kw: (operator_text, 3),
    )
    job = {
        "origin": "devel/foo", "run_id": "run-1",
        "snippet_round": "0", "has_snippets": "false",
    }
    payload = runner.build_triage_payload(bdir, None, job)
    assert "## User Context (run-scoped)" in payload
    assert operator_text in payload


def test_payload_renders_full_operator_context_history_when_present(
    tmp_path, monkeypatch,
):
    """Step 29e: when user_context_history is non-empty, the triage
    payload renders each round verbatim (Round 1, 2, 3 in submission
    order) instead of just the latest overwrite. Without this, the
    model sees only the last operator message and can't follow
    references like "consider what I said before"."""
    bdir = _minimal_bundle(tmp_path)
    monkeypatch.setattr(
        runner, "get_user_context",
        lambda *a, **kw: ("round three text", 3),
    )
    history = [
        {"context_rev": 1, "submitted_at": "2026-05-27T10:00:00Z",
         "text": "round one text", "submitted_by": "alice"},
        {"context_rev": 2, "submitted_at": "2026-05-27T11:00:00Z",
         "text": "round two text", "submitted_by": None},
        {"context_rev": 3, "submitted_at": "2026-05-27T12:00:00Z",
         "text": "round three text", "submitted_by": "alice"},
    ]
    monkeypatch.setattr(
        runner, "_load_operator_context_history",
        lambda *a, **kw: history,
    )
    job = {
        "origin": "devel/foo", "run_id": "run-1",
        "snippet_round": "0", "has_snippets": "false",
    }
    payload = runner.build_triage_payload(bdir, None, job)
    assert "## User Context (run-scoped)" in payload
    # Each round renders with its timestamp + heading.
    assert "### Round 1 — 2026-05-27T10:00:00Z (operator: alice)" in payload
    # Anonymous round omits the operator parenthetical.
    assert "### Round 2 — 2026-05-27T11:00:00Z" in payload
    # All three texts present in submission order.
    pos1 = payload.index("round one text")
    pos2 = payload.index("round two text")
    pos3 = payload.index("round three text")
    assert pos1 < pos2 < pos3


def test_payload_falls_back_to_single_text_when_history_empty(
    tmp_path, monkeypatch,
):
    """Empty history + non-empty current text → legacy single-block
    rendering. Covers pre-29b submissions whose history table rows
    don't exist, and direct test seeds."""
    bdir = _minimal_bundle(tmp_path)
    monkeypatch.setattr(
        runner, "get_user_context",
        lambda *a, **kw: ("legacy single-block text", 1),
    )
    monkeypatch.setattr(
        runner, "_load_operator_context_history",
        lambda *a, **kw: [],
    )
    job = {
        "origin": "devel/foo", "run_id": "run-1",
        "snippet_round": "0", "has_snippets": "false",
    }
    payload = runner.build_triage_payload(bdir, None, job)
    assert "## User Context (run-scoped)" in payload
    assert "legacy single-block text" in payload
    # Legacy shape has no Round headings.
    assert "### Round" not in payload
