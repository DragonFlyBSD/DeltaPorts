"""Triage confidence is a closed enum (low/medium/high). The agent is
told to emit exactly one of those words, but may disobey and append
prose ("high — because ..."). The parser must coerce to the enum so a
prose value can't silently fail the policy floor and downgrade the tier
to MANUAL (M2a)."""

from __future__ import annotations

import pytest

from dportsv3.agent.triage import _normalize_confidence, _parse


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("high", "high"),
        ("medium", "medium"),
        ("low", "low"),
        ("HIGH", "high"),                       # case-folded
        ("high.", "high"),                      # trailing punctuation
        ("high — both patterns are clear", "high"),   # em-dash prose (redis82)
        ("high – the malloc.h error", "high"),        # en-dash prose (valkey8)
        ("high, very confident", "high"),
        ("highly confident", "low"),            # must NOT match "high"
        ("totally unsure", "low"),              # non-enum → fallback
        ("", "low"),                            # empty → fallback
    ],
)
def test_normalize_confidence(raw, expected):
    assert _normalize_confidence(raw) == expected


def test_parse_coerces_prose_confidence_to_enum():
    """End-to-end: a prose Confidence line parses to the bare enum word,
    so downstream tiering sees 'high', not the prose (which would
    silently cascade to MANUAL)."""
    text = (
        "## Classification\ncompile-error\n\n"
        "## Confidence\nhigh — both failure patterns are clear\n\n"
        "## Root Cause\nwhatever\n"
    )
    classification, confidence = _parse(text)
    assert classification == "compile-error"
    assert confidence == "high"
