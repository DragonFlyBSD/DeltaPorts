"""Agent playbook library (Step 27b).

Parses ``docs/agent-playbooks/*.md`` entries with YAML-subset
frontmatter, selects entries by per-job context (flow + classification
+ intent surface + toolchain + convert phase), and renders the
selected entries into a markdown block for inclusion in the agent's
payload.

Replaces the legacy ``load_kedb`` bulk-load. Alpha-mode hard cutover:
entries without a frontmatter ``flows:`` declaration default to
``["triage", "patch"]`` so the four existing ``error-*`` entries keep
landing in those payloads, but new entries are expected to declare
explicitly. There is no compatibility shim — if a selection produces
no entries, the agent payload has no playbooks section (and that's
fine; the structural prompt carries everything else).

Frontmatter shape:

    ---
    triggers:
      classifications: [patch-error, compile-error]
      intents: [replace_in_dops_block]
      toolchains: [autoconf]
      convert_phases: [picking_target]
      flows: [triage, patch]
    tags: [heredoc]
    priority: 100
    ---

Empty trigger list = wildcard for that axis. Empty ``flows`` falls
back to ``[triage, patch]``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

__all__ = [
    "PlaybookTriggers",
    "PlaybookEntry",
    "SelectionResult",
    "find_playbooks_dir",
    "list_entries",
    "load_playbooks",
]


_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_INLINE_LIST_RE = re.compile(r"^\[(.*)\]\s*$")
_FLOWS_DEFAULT = ("triage", "patch")
_SKIP_FILES = {"readme.md", "template.md"}


@dataclass(frozen=True)
class PlaybookTriggers:
    """Trigger axes that gate when an entry is attached to a payload.

    Empty tuple on any axis = wildcard for that axis (entry matches
    regardless of the context's value for that field). ``flows``
    defaults to ``("triage", "patch")`` when the frontmatter omits
    it — convert entries must opt in explicitly via
    ``flows: [convert]``.
    """
    classifications: tuple[str, ...] = ()
    intents: tuple[str, ...] = ()
    toolchains: tuple[str, ...] = ()
    convert_phases: tuple[str, ...] = ()
    flows: tuple[str, ...] = _FLOWS_DEFAULT


@dataclass(frozen=True)
class PlaybookEntry:
    """One parsed entry. ``body`` is everything after the frontmatter
    block (frontmatter stripped). ``title`` is the first ``# ``
    heading in the body, or the filename stem as fallback."""
    path: Path
    title: str
    body: str
    triggers: PlaybookTriggers
    tags: tuple[str, ...] = ()
    priority: int = 100
    est_tokens: int = 0


@dataclass(frozen=True)
class SelectionResult:
    """Outcome of one ``load_playbooks`` call.

    ``text`` is the assembled markdown block (or empty string if
    nothing matched). ``included`` and ``skipped`` are for telemetry —
    the runner logs them so operators can see WHY their entry didn't
    fire.
    """
    text: str
    included: tuple[str, ...] = ()
    skipped: tuple[tuple[str, str], ...] = ()  # (filename, reason)


# ---------------------------------------------------------------------
# Frontmatter parsing
# ---------------------------------------------------------------------


def _parse_inline_list(raw: str) -> tuple[str, ...]:
    """Parse a YAML inline list like ``[a, b, "c"]`` into a tuple."""
    raw = (raw or "").strip()
    if not raw or raw == "[]":
        return ()
    m = _INLINE_LIST_RE.match(raw)
    if not m:
        return ()
    inner = m.group(1).strip()
    if not inner:
        return ()
    items: list[str] = []
    for part in inner.split(","):
        part = part.strip()
        if not part:
            continue
        # Strip one outer pair of quotes if present.
        if len(part) >= 2 and part[0] == part[-1] and part[0] in {'"', "'"}:
            part = part[1:-1]
        if part:
            items.append(part)
    return tuple(items)


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Split frontmatter from body.

    Returns ``(frontmatter_dict, body)``. ``frontmatter_dict`` maps
    top-level keys to either string values or, for nested blocks like
    ``triggers:``, a sub-dict. No frontmatter present → ``({}, text)``.

    Comment lines starting with ``#`` inside the frontmatter block are
    ignored.
    """
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    body = text[m.end():]
    fm_text = m.group(1)
    fm: dict = {}
    current_block: str | None = None
    for raw in fm_text.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        # Nested block lines start with whitespace.
        is_nested = raw.startswith(" ") or raw.startswith("\t")
        if is_nested and current_block is not None:
            key, sep, val = stripped.partition(":")
            if not sep:
                continue
            block = fm.setdefault(current_block, {})
            if isinstance(block, dict):
                block[key.strip()] = val.strip()
            continue
        # Top-level line.
        key, sep, val = stripped.partition(":")
        if not sep:
            continue
        key = key.strip()
        val = val.strip()
        if not val:
            fm[key] = {}
            current_block = key
        else:
            fm[key] = val
            current_block = None
    return fm, body


def _extract_title(body: str, fallback: str) -> str:
    """First ``# heading`` in the body, or fallback (filename stem)."""
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip() or fallback
    return fallback


def _parse_entry(path: Path) -> PlaybookEntry | None:
    """Read + parse one markdown file. Returns None on I/O error."""
    try:
        text = path.read_text()
    except OSError:
        return None
    fm, body = _parse_frontmatter(text)
    triggers_raw = fm.get("triggers")
    if not isinstance(triggers_raw, dict):
        triggers_raw = {}
    flows = _parse_inline_list(triggers_raw.get("flows", ""))
    triggers = PlaybookTriggers(
        classifications=_parse_inline_list(triggers_raw.get("classifications", "")),
        intents=_parse_inline_list(triggers_raw.get("intents", "")),
        toolchains=_parse_inline_list(triggers_raw.get("toolchains", "")),
        convert_phases=_parse_inline_list(triggers_raw.get("convert_phases", "")),
        flows=flows if flows else _FLOWS_DEFAULT,
    )
    tags = _parse_inline_list(fm.get("tags", "") if isinstance(fm.get("tags"), str) else "")
    priority_raw = fm.get("priority", "100")
    if isinstance(priority_raw, dict):
        priority = 100
    else:
        try:
            priority = int(str(priority_raw).strip())
        except (TypeError, ValueError):
            priority = 100
    title = _extract_title(body, path.stem)
    return PlaybookEntry(
        path=path,
        title=title,
        body=body,
        triggers=triggers,
        tags=tags,
        priority=priority,
        # Rough estimate; sufficient for budget gate. ~4 chars/token.
        est_tokens=max(1, len(body) // 4),
    )


# ---------------------------------------------------------------------
# Discovery + loading
# ---------------------------------------------------------------------


def find_playbooks_dir() -> Path | None:
    """Locate ``docs/agent-playbooks/`` by walking up from this file.

    Fixes the pre-existing parent-chain bug in ``find_kedb_dir`` —
    the legacy version did a single ``.parent`` hop and resolved to a
    path that doesn't exist, so auto-detect always returned None.
    Walks up the directory tree until it finds ``docs/agent-playbooks``
    or runs out of ancestors.
    """
    here = Path(__file__).resolve()
    for ancestor in [here.parent, *here.parents]:
        candidate = ancestor / "docs" / "agent-playbooks"
        if candidate.is_dir():
            return candidate
    return None


def list_entries(playbooks_dir: Path | None) -> list[PlaybookEntry]:
    """Read + parse every ``*.md`` entry (excluding README/TEMPLATE).

    Stable order: alphabetic by path. Selection re-sorts by priority.
    """
    if not playbooks_dir or not playbooks_dir.is_dir():
        return []
    entries: list[PlaybookEntry] = []
    for path in sorted(playbooks_dir.glob("*.md")):
        if path.name.lower() in _SKIP_FILES:
            continue
        entry = _parse_entry(path)
        if entry is not None:
            entries.append(entry)
    return entries


# ---------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------


def _matches(entry: PlaybookEntry, *,
             role: str,
             classification: str | None,
             intents: set[str],
             toolchains: set[str],
             convert_phases: set[str]) -> tuple[bool, str]:
    """Trigger check. Returns (matched, reason_if_skipped)."""
    t = entry.triggers
    if role not in t.flows:
        return False, f"flow:{role}-not-in-{list(t.flows)}"
    if t.classifications:
        if classification is None or classification not in t.classifications:
            return False, (
                f"classification:{classification!r}-not-in-"
                f"{list(t.classifications)}"
            )
    if t.intents and not (intents & set(t.intents)):
        return False, f"intents:no-overlap-with-{list(t.intents)}"
    if t.toolchains and not (toolchains & set(t.toolchains)):
        return False, f"toolchains:no-overlap-with-{list(t.toolchains)}"
    if t.convert_phases and not (convert_phases & set(t.convert_phases)):
        return False, (
            f"convert_phases:no-overlap-with-{list(t.convert_phases)}"
        )
    return True, ""


def load_playbooks(
    playbooks_dir: Path | None,
    *,
    role: str,
    classification: str | None = None,
    intents: Iterable[str] = (),
    toolchains: Iterable[str] = (),
    convert_phases: Iterable[str] = (),
    budget_tokens: int = 0,
) -> SelectionResult:
    """Select and render playbook entries for the given context.

    ``role`` is one of ``triage`` / ``patch`` / ``convert`` and is
    matched against each entry's ``triggers.flows``. Other arguments
    are AND'd against their respective trigger axes (within an axis,
    any overlap counts as a match; an empty trigger axis on the entry
    means "wildcard").

    ``budget_tokens > 0`` activates the budget gate — entries are
    included in priority order until the running total would exceed
    the budget. The dropped entries appear in
    ``SelectionResult.skipped`` with a ``budget:`` reason.

    A ``SelectionResult`` with empty ``text`` is a valid outcome
    (no entries matched, or none fit in the budget). The runner
    treats that as "no playbooks section in the payload."
    """
    entries = list_entries(playbooks_dir)
    intents_set = set(intents)
    toolchains_set = set(toolchains)
    phases_set = set(convert_phases)

    included: list[PlaybookEntry] = []
    skipped: list[tuple[str, str]] = []
    for e in entries:
        ok, reason = _matches(
            e, role=role, classification=classification,
            intents=intents_set, toolchains=toolchains_set,
            convert_phases=phases_set,
        )
        if ok:
            included.append(e)
        else:
            skipped.append((e.path.name, reason))

    # Priority sort. Smaller priority value = render earlier (and,
    # under budget, drop later).
    included.sort(key=lambda e: (e.priority, e.path.name))

    if budget_tokens > 0:
        kept: list[PlaybookEntry] = []
        running = 0
        for e in included:
            if running + e.est_tokens > budget_tokens:
                skipped.append((
                    e.path.name,
                    f"budget:{running}+{e.est_tokens}>{budget_tokens}",
                ))
                continue
            kept.append(e)
            running += e.est_tokens
        included = kept

    if not included:
        return SelectionResult(
            text="", included=(), skipped=tuple(skipped),
        )

    parts: list[str] = ["## Agent Playbooks", ""]
    parts.append(
        "Relevant playbook entries selected for this job based on its "
        f"flow ({role})"
        + (f" and classification ({classification})" if classification else "")
        + ":"
    )
    parts.append("")
    for e in included:
        parts.append(f"### {e.title}")
        parts.append(e.body)
        if not e.body.endswith("\n"):
            parts.append("")
    return SelectionResult(
        text="\n".join(parts),
        included=tuple(e.path.name for e in included),
        skipped=tuple(skipped),
    )
