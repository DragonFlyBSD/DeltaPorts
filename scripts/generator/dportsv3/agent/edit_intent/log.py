"""Intent log accumulator (Step 25b).

The intent log is the canonical record of a patch attempt (design
§7). The IntentLog class collects entries as the agent applies
intents, enforces the size caps from §13.2 (100 intents, 1 MB
total), and serializes to the bundle's ``analysis/intent_log.json``
shape at COMMIT/ABORT time.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from .grammar import Intent
from .validator import IntentError


# Default caps. Operator-overridable per the design doc §13.2.
DEFAULT_MAX_COUNT = 100
DEFAULT_MAX_BYTES = 1_000_000  # 1 MB

SCHEMA_VERSION = 1


@dataclass
class IntentLogEntry:
    """One row in the intent log."""
    seq: int
    intent: dict[str, Any]   # the wire-format dict, post-validation
    applied_at: str          # ISO timestamp
    ok: bool
    substrate_diff: str = ""
    error: str | None = None


@dataclass
class IntentLog:
    """Accumulator + serializer for one transaction.

    Caller invariant: append rows in execution order; serialize at
    COMMIT (success) or ABORT (failure). Don't mutate entries after
    appending — entries are designed to be append-only forensics.
    """
    origin: str
    target: str
    mode_at_apply: str       # "compat" | "dops" | "convert"
    baseline_commit: str
    intents: list[IntentLogEntry] = field(default_factory=list)
    max_count: int = field(default_factory=lambda: int(
        os.environ.get("DP_HARNESS_INTENT_MAX_COUNT", DEFAULT_MAX_COUNT)
    ))
    max_bytes: int = field(default_factory=lambda: int(
        os.environ.get("DP_HARNESS_INTENT_MAX_BYTES", DEFAULT_MAX_BYTES)
    ))

    def would_overflow(self, intent: dict[str, Any], *,
                       substrate_diff: str = "") -> str | None:
        """Predicate: returns an error message if appending the
        given intent would exceed either cap, or ``None`` if it
        would fit. Used by callers to refuse a substrate edit
        before the log would reject it — the canonical-log
        invariant requires log and substrate to stay in sync.

        ``substrate_diff`` MUST be the real diff the intent
        produced (or will produce) — passing ``""`` UNDERestimates
        the projected size and can let a byte-cap-busting edit
        through. The supported usage pattern in
        ``worker.apply_intent`` is:

        1. pre-apply check with ``substrate_diff=""`` — cheap
           guard for count cap + intent-payload byte cap;
        2. translator.apply;
        3. post-apply check with ``substrate_diff=result.substrate_diff``
           — catches large generated diffs (dops overlay rewrites,
           from_dupe payloads, replace_in_patch); on overflow the
           caller must revert the substrate edit before returning.
        """
        if len(self.intents) >= self.max_count:
            return (
                f"intent log exceeds {self.max_count} entries — "
                f"almost certainly an agent loop; the patch agent "
                f"should split into smaller bundles or escalate to "
                f"the operator"
            )
        # Build a hypothetical entry to measure projected size.
        probe = IntentLogEntry(
            seq=len(self.intents),
            intent=intent,
            applied_at="0000-00-00T00:00:00+00:00",
            ok=True,
            substrate_diff=substrate_diff,
            error=None,
        )
        projected = self.intents + [probe]
        size = len(self._serialize(projected).encode("utf-8"))
        if size > self.max_bytes:
            return (
                f"intent log size would exceed "
                f"{self.max_bytes} bytes ({size} after this intent) "
                f"— split, simplify, or escalate"
            )
        return None

    def append(self, intent: dict[str, Any], *,
               ok: bool,
               substrate_diff: str = "",
               error: str | None = None) -> IntentLogEntry:
        """Append a row, enforcing the caps.

        Raises IntentError if appending would exceed either cap.
        Production callers should use :meth:`would_overflow`
        first to refuse the substrate edit ahead of time — calling
        ``append`` after the substrate change has landed and then
        hitting the cap creates an unrecorded edit, which the
        canonical-log contract forbids.
        """
        msg = self.would_overflow(intent, substrate_diff=substrate_diff)
        if msg is not None:
            raise IntentError(msg, intent=intent)
        entry = IntentLogEntry(
            seq=len(self.intents),
            intent=intent,
            applied_at=datetime.now(timezone.utc).isoformat(),
            ok=ok,
            substrate_diff=substrate_diff,
            error=error,
        )
        self.intents.append(entry)
        return entry

    def to_json(self) -> str:
        return self._serialize(self.intents)

    def _serialize(self, intents: list[IntentLogEntry]) -> str:
        doc = {
            "schema_version": SCHEMA_VERSION,
            "origin": self.origin,
            "target": self.target,
            "mode_at_apply": self.mode_at_apply,
            "baseline_commit": self.baseline_commit,
            "intents": [asdict(e) for e in intents],
        }
        return json.dumps(doc, indent=2, sort_keys=False)
