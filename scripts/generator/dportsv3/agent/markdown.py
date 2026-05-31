"""Shared markdown-section extraction helpers.

Used by:

- :mod:`dportsv3.delivery.orchestrator` (lifts Root Cause / Evidence /
  Patch Summary into the delivered PR body).
- :mod:`dportsv3.agent.runner` (Step 36-2: lifts Root Cause / Evidence
  into the typed :class:`TriageResult` at write time).

Originally lived as private ``_md_section`` / ``_md_inline`` helpers
inside ``delivery/orchestrator.py``; promoted here so the typed
phase-result producer doesn't have to import a private symbol from
the delivery package.
"""

from __future__ import annotations

import re


__all__ = ["md_section", "md_inline"]


def md_section(md: str | None, heading: str, *, max_chars: int = 3000) -> str:
    """Full body under a ``## <heading>`` section (until the next
    ``## `` or EOF), trimmed and length-capped. ``""`` when absent.

    Markdown inside the section is preserved as-is (no re-fencing) so
    quoted log lines / bullets render unchanged downstream.
    """
    if not md:
        return ""
    pat = re.compile(
        rf"^##\s+{re.escape(heading)}\s*\n(.*?)(?=^##\s+|\Z)",
        re.MULTILINE | re.DOTALL | re.IGNORECASE,
    )
    m = pat.search(md)
    if not m:
        return ""
    body = m.group(1).strip()
    if len(body) > max_chars:
        body = body[:max_chars].rstrip() + "\n…(truncated)"
    return body


def md_inline(md: str | None, heading: str) -> str:
    """First non-empty line under a ``## <heading>`` section."""
    if not md:
        return ""
    pat = re.compile(
        rf"^##\s+{re.escape(heading)}\s*\n(.*?)(?=^##\s+|\Z)",
        re.MULTILINE | re.DOTALL | re.IGNORECASE,
    )
    m = pat.search(md)
    if not m:
        return ""
    for line in m.group(1).splitlines():
        s = line.strip()
        if s:
            return s
    return ""
