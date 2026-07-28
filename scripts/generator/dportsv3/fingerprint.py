"""Canonical error fingerprint — the issue-identity primitive.

A *fingerprint* is a stable short digest of a build failure's root-cause
line, computed by canonicalizing away incidental noise (absolute paths,
line/column numbers, hex addresses, PIDs, tmpdir names, timestamps) so
that "the same failure" collapses to the same value instead of splitting
on run-to-run detail.

This is the single definition of a fingerprint in the system. It is used
in two places, which MUST agree:

- at ingest (the failure hook) to stamp ``bundles.error_signature`` the
  moment an occurrence is born, so issues can be keyed immediately; and
- by the runner's sticky-signature retry cap, which asks "is this the
  same wall we keep hitting?".

The digest shape is ``sha256(normalized)[:16]`` — 16 hex chars, matching
the pre-existing ``error_signature`` column so nothing downstream needs
to change width.
"""

from __future__ import annotations

import hashlib
import re

# Ordered canonicalization rules. Each strips one class of incidental
# variation. Order matters: hex/timestamp/path reductions run before the
# generic long-digit rule so structured tokens are handled by their
# specific rule rather than the catch-all.
_HEX_ADDR = re.compile(r"0x[0-9a-fA-F]+")
_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}[T ][0-9:.]+(?:Z|[+-]\d{2}:?\d{2})?")
# A maximal run of path-ish chars containing at least one '/'. Reduced to
# its basename — the directory (build root, tmpdir, ports/ prefix) is
# incidental; the origin already lives in the issue key.
_PATH = re.compile(r"[\w.+\-]*/[\w./+\-]*")
# Trailing/embedded ":line" or ":line:col" after a filename.
_LINECOL = re.compile(r":\d+(?::\d+)?(?=:|\s|\)|,|$)")
# Long standalone integer runs: PIDs, inodes, byte offsets. Word-bounded
# so digits embedded in identifiers (python312, libssl3) survive.
_LONG_INT = re.compile(r"\b\d{3,}\b")
_WS = re.compile(r"\s+")


def normalize_error(text: str | None) -> str | None:
    """Return the canonicalized root-cause line of ``text`` (the first
    non-empty line of ``logs/errors.txt``), or ``None`` if empty.

    The returned string is what gets hashed; it is deterministic and
    stable across builds of the same failure.
    """
    if not text:
        return None
    line = ""
    for raw in text.splitlines():
        stripped = raw.strip()
        if stripped:
            line = stripped
            break
    if not line:
        return None
    line = _HEX_ADDR.sub("0xADDR", line)
    line = _TIMESTAMP.sub("TIMESTAMP", line)
    line = _PATH.sub(lambda m: m.group(0).rsplit("/", 1)[-1], line)
    line = _LINECOL.sub("", line)
    line = _LONG_INT.sub("N", line)
    line = _WS.sub(" ", line).strip()
    return line or None


def compute_fingerprint(text: str | None) -> str | None:
    """Return ``sha256(normalize_error(text))[:16]``, or ``None`` when
    there is no usable line to fingerprint."""
    norm = normalize_error(text)
    if norm is None:
        return None
    return hashlib.sha256(norm.encode("utf-8", errors="replace")).hexdigest()[:16]


def issue_key(target: str | None, origin: str | None,
              fingerprint: str | None) -> str:
    """Return the issue identity for a failure: a short hash of the
    ``(target, origin, fingerprint)`` triple.

    This is the find-or-create key for the ``issues`` table — two
    occurrences with the same triple are the same problem. When a failure
    has no fingerprint (no usable errors text), the empty component makes
    all such failures for one ``(target, origin)`` collapse to a single
    fallback issue rather than each spawning its own.
    """
    triple = f"{target or ''}\x00{origin or ''}\x00{fingerprint or ''}"
    return hashlib.sha256(triple.encode("utf-8", errors="replace")).hexdigest()[:16]
