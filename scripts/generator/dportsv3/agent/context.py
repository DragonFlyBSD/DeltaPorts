"""Composable context assembly for LLM payloads.

Phase 4 Step 1 of the agentic framework. Layer 4 of
``agentic-framework-design.md``.

The two ``build_*_payload`` functions in the runner were walls of
``parts.append(...)`` calls that hard-coded section ordering and
inclusion logic. This module replaces them with composable
``ContextSection`` objects that the ``ContextAssembler`` renders in
priority order.

Phase 4 enforces **strict byte-for-byte parity** with the legacy
output. This module ships the protocol + driver only; concrete
section classes land in Steps 2 (triage) and 3 (patch).

Design decisions:

- **Sections are pure functions over typed data.** Anything that
  needs to be queried (DB rows, bundle artifacts) is pre-loaded by
  the caller into the ``ContextCtx``. Sections never do I/O during
  render. Makes them trivial to unit-test and predictable to compose.
- **Section priority is render order.** Integer priority; lower
  renders first. Reproduces today's hard-coded order by assigning
  priorities. The assembler never reorders beyond that.
- **None or "" → skip silently.** A section that has nothing to say
  returns ``None`` (or empty); it drops out of the payload as if it
  weren't in the list. No extra blank line.
- **Exceptions bubble.** A section that raises crashes the assembler.
  This is a bug-not-edge-case; observability beats silent fallback.

Future phases may add aggregate-budget enforcement (drop lowest-
priority sections when total exceeds N bytes); that's not in Phase 4.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable


@dataclass
class ContextCtx:
    """Render-time context passed to every section.

    Fields are populated by the caller before render. Pre-loading
    (rather than letting sections query) keeps render deterministic
    and unit-testable without a live DB or filesystem.
    """
    bundle_dir: Path | None = None
    bundle_id: str | None = None
    job: dict = field(default_factory=dict)
    kedb_dir: Path | None = None
    # Pre-loaded data the caller computes once and hands to sections
    # that need it.
    port_history: object | None = None  # decision.PortHistory or None
    sibling_bundle_ids: list[str] = field(default_factory=list)
    # Optional DB conn for sections that genuinely cannot avoid I/O
    # (e.g. an enumeration where the section list itself depends on
    # query results). Phase-4 sections shouldn't need this; reserved.
    db_conn: sqlite3.Connection | None = None


@runtime_checkable
class ContextSection(Protocol):
    """A piece of the rendered payload.

    Implementations should be dataclasses or simple classes that
    expose:
    - ``name``: short identifier for logging/debugging.
    - ``priority``: integer; lower renders first. Use distinct
      values per section (e.g. 10, 20, 30...) so reorders by
      insertion later don't conflict.
    - ``render(ctx)``: returns the section's markdown chunk
      (including header) or ``None`` to skip.

    Sections must not mutate ``ctx``.
    """
    name: str
    priority: int

    def render(self, ctx: ContextCtx) -> str | None: ...


def render_payload(sections: list[ContextSection], ctx: ContextCtx) -> str:
    """Assemble the payload by rendering each section in priority order.

    Mirrors ``"\\n".join(parts)`` semantics of the legacy code:
    sections that return ``None`` or empty string drop out; the rest
    are joined with a single newline.

    Sections are sorted in-place-ish (a stable sort over a fresh
    list) so callers can pass them in any order. Within the same
    priority value, insertion order is preserved.
    """
    ordered = sorted(sections, key=lambda s: s.priority)
    rendered: list[str] = []
    for section in ordered:
        chunk = section.render(ctx)
        if chunk is None or chunk == "":
            continue
        rendered.append(chunk)
    return "\n".join(rendered)
