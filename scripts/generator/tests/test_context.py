"""Unit tests for the ContextAssembler driver.

Phase 4 Step 1. Concrete sections (and parity tests) land in
Steps 2 and 3.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from dportsv3.agent.context import ContextCtx, ContextSection, render_payload


# --- helpers ------------------------------------------------------------------


@dataclass
class _StaticSection:
    """Section that returns a fixed string (or None) regardless of ctx."""
    name: str
    priority: int
    output: str | None = "hello"

    def render(self, ctx: ContextCtx) -> str | None:
        return self.output


@dataclass
class _SeesCtxSection:
    """Section that reads from ctx.job to prove ctx propagates."""
    name: str
    priority: int

    def render(self, ctx: ContextCtx) -> str | None:
        v = ctx.job.get("origin", "?")
        return f"## Origin\n{v}"


@dataclass
class _RaisingSection:
    name: str = "kaboom"
    priority: int = 100

    def render(self, ctx: ContextCtx) -> str | None:
        raise ValueError("synthetic")


# --- basic ordering + filtering ---------------------------------------------


def test_priority_order_lower_first():
    sections = [
        _StaticSection("middle", priority=20, output="B"),
        _StaticSection("last",   priority=30, output="C"),
        _StaticSection("first",  priority=10, output="A"),
    ]
    out = render_payload(sections, ContextCtx())
    assert out == "A\nB\nC"


def test_none_section_drops_silently():
    sections = [
        _StaticSection("a", priority=10, output="A"),
        _StaticSection("b", priority=20, output=None),
        _StaticSection("c", priority=30, output="C"),
    ]
    out = render_payload(sections, ContextCtx())
    # Single newline between A and C — no extra blank line for the
    # skipped middle section.
    assert out == "A\nC"


def test_empty_string_drops_silently():
    sections = [
        _StaticSection("a", priority=10, output="A"),
        _StaticSection("b", priority=20, output=""),
        _StaticSection("c", priority=30, output="C"),
    ]
    assert render_payload(sections, ContextCtx()) == "A\nC"


def test_input_order_independence():
    """The driver always sorts by priority — caller can pass any order."""
    sections = [
        _StaticSection("a", priority=10, output="A"),
        _StaticSection("b", priority=20, output="B"),
        _StaticSection("c", priority=30, output="C"),
    ]
    forward = render_payload(sections, ContextCtx())
    reverse = render_payload(list(reversed(sections)), ContextCtx())
    assert forward == reverse


def test_same_priority_preserves_insertion_order():
    sections = [
        _StaticSection("a", priority=10, output="A"),
        _StaticSection("b", priority=10, output="B"),
        _StaticSection("c", priority=10, output="C"),
    ]
    assert render_payload(sections, ContextCtx()) == "A\nB\nC"


# --- ctx propagation ---------------------------------------------------------


def test_ctx_propagates_to_sections():
    ctx = ContextCtx(job={"origin": "devel/foo"})
    sections = [_SeesCtxSection("origin", priority=10)]
    out = render_payload(sections, ctx)
    assert out == "## Origin\ndevel/foo"


def test_default_ctx_is_empty():
    """A bare ContextCtx() doesn't crash sections that read job
    fields with .get() defaults — sanity for caller convenience."""
    out = render_payload([_SeesCtxSection("origin", priority=10)], ContextCtx())
    assert out == "## Origin\n?"


# --- error semantics ---------------------------------------------------------


def test_section_exception_bubbles():
    """A buggy section crashes the assembler loudly — no swallowing."""
    with pytest.raises(ValueError, match="synthetic"):
        render_payload([_RaisingSection()], ContextCtx())


def test_empty_section_list():
    assert render_payload([], ContextCtx()) == ""


# --- protocol conformance ----------------------------------------------------


def test_static_section_satisfies_protocol():
    """Runtime check — the test sections are valid ContextSections."""
    section = _StaticSection("x", priority=1)
    assert isinstance(section, ContextSection)


def test_render_does_not_mutate_input_list():
    """Sort uses a new list internally; caller's list stays put."""
    sections = [
        _StaticSection("b", priority=20, output="B"),
        _StaticSection("a", priority=10, output="A"),
    ]
    before_names = [s.name for s in sections]
    render_payload(sections, ContextCtx())
    after_names = [s.name for s in sections]
    assert before_names == after_names


# --- ContextCtx shape --------------------------------------------------------


def test_context_ctx_defaults_are_reasonable():
    """Every field has a None / empty default so callers can build
    a ctx incrementally."""
    ctx = ContextCtx()
    assert ctx.bundle_dir is None
    assert ctx.bundle_id is None
    assert ctx.job == {}
    assert ctx.kedb_dir is None
    assert ctx.port_history is None
    assert ctx.sibling_bundle_ids == []
    assert ctx.db_conn is None


def test_context_ctx_independent_default_lists():
    """Mutable defaults must use field(default_factory=...) — confirm
    two instances don't share state."""
    a = ContextCtx()
    b = ContextCtx()
    a.sibling_bundle_ids.append("x")
    assert b.sibling_bundle_ids == []
    a.job["k"] = "v"
    assert b.job == {}
