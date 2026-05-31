"""Typed inter-phase result contracts (Step 36).

Each phase (triage / convert / patch) writes one canonical result per
bundle to ``analysis/<phase>_result.json`` matching a frozen dataclass
with a ``schema_version`` first field. Downstream phases consume the
typed object via :func:`load_phase_result` instead of re-parsing
markdown — eliminates regex-fishing brittleness (triage prompt
rewrites silently breaking patch's parser) and closes the asymmetric-
coverage gap that left the convert flow blind to triage's
classification.

Markdown artifacts (``analysis/<phase>.md``) stay as the
human-readable surface. They are no longer the source of truth for
code; producers extract structured fields once at write time and
persist the typed shape.

Storage / routing: same bundle artifact store, same DB tracking. The
typed JSON is just a new relpath family.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, fields
from typing import Any, TypeVar


__all__ = [
    "TriageResult",
    "ConvertResult",
    "PatchResult",
    "PhaseResultVersionMismatch",
    "write_phase_result",
    "load_phase_result",
]


_SCHEMA_VERSION = 1


class PhaseResultVersionMismatch(Exception):
    """Raised when an on-disk phase result carries a schema_version
    that doesn't match the dataclass's current ``schema_version``
    default. Consumers should treat this as "no result available"
    (degrade gracefully) rather than crash the run."""

    def __init__(self, phase: str, got: Any, expected: int) -> None:
        super().__init__(
            f"phase_result {phase!r}: schema_version {got!r} on disk, "
            f"loader expects {expected}"
        )
        self.phase = phase
        self.got = got
        self.expected = expected


# Token-spend is split into three flat fields rather than a nested
# {"prompt": ..., "completion": ..., "total": ...} dict so the
# dataclass round-trips cleanly via ``asdict`` without nested-shape
# special-casing. The existing ``analysis/triage.json`` / ``patch_audit.json``
# audit files used the nested form; downstream consumers
# (``proposed_fix.py``) read the same three numbers — the field names
# change but the values don't.


@dataclass(frozen=True)
class TriageResult:
    """What triage classified about a bundle's failure."""

    classification: str
    confidence: str
    root_cause: str
    evidence_excerpt: str
    error_signature: str | None
    tier: str
    classifier_version: str
    tokens_prompt: int
    tokens_completion: int
    tokens_total: int
    model: str
    schema_version: int = _SCHEMA_VERSION


@dataclass(frozen=True)
class ConvertResult:
    """What the convert phase produced + how the verifier judged it."""

    status: str
    reapply_ok: bool
    reason_code: str | None
    overlay_sha256: str | None
    files_removed: list[str]
    diag_tail: str | None
    tokens_prompt: int
    tokens_completion: int
    tokens_total: int
    schema_version: int = _SCHEMA_VERSION


@dataclass(frozen=True)
class PatchResult:
    """What the patch agent did and how the rebuild gate judged it."""

    rebuild_ok: bool
    status: str
    attempts: int
    intents_applied: int
    tokens_prompt: int
    tokens_completion: int
    tokens_total: int
    schema_version: int = _SCHEMA_VERSION


_T = TypeVar("_T")


def _relpath(phase: str) -> str:
    return f"analysis/{phase}_result.json"


def write_phase_result(bundle_id: str, phase: str, result: Any) -> None:
    """Serialize ``result`` (a phase-result dataclass) to the bundle
    artifact store at ``analysis/<phase>_result.json``.

    Raises ``RuntimeError`` if the artifact-store write fails, matching
    the existing producer conventions (e.g. ``_write_triage_audit``).
    """
    # Lazy import — phase_result is upstream of runner in the import
    # graph; runner imports phase_result, not the reverse. Pulling
    # artifact_store_put in at module level would cycle.
    from dportsv3.agent.runner import artifact_store_put  # noqa: PLC0415

    payload = asdict(result)
    data = (json.dumps(payload, indent=2) + "\n").encode("utf-8")
    if not artifact_store_put(bundle_id, _relpath(phase), data, "json"):
        raise RuntimeError(
            f"failed to write {_relpath(phase)} to artifact store "
            f"(bundle_id={bundle_id!r})"
        )


def load_phase_result(
    bundle_id: str | None, phase: str, cls: type[_T],
) -> _T | None:
    """Load a typed phase result from the bundle artifact store.

    Returns ``None`` when:
    - ``bundle_id`` is empty (operator-fired job with no bundle)
    - the artifact doesn't exist (phase hasn't run yet, or legacy
      bundle predating Step 36)

    Raises ``PhaseResultVersionMismatch`` when the on-disk
    ``schema_version`` doesn't match the dataclass default — the
    caller decides whether to degrade or surface.
    """
    if not bundle_id:
        return None
    from dportsv3.agent.runner import read_bundle_text  # noqa: PLC0415

    raw = read_bundle_text(None, bundle_id, _relpath(phase))
    if not raw:
        return None
    payload = json.loads(raw)

    expected = _expected_schema_version(cls)
    got = payload.get("schema_version")
    if got != expected:
        raise PhaseResultVersionMismatch(phase, got, expected)

    # Filter to known fields so a forward-compat extra key on disk
    # (e.g. a future field added at v2) doesn't TypeError the
    # constructor at v1. (Version mismatch fires first; this is for
    # same-version-but-extra defensive only.)
    known = {f.name for f in fields(cls)}  # type: ignore[arg-type]
    kwargs = {k: v for k, v in payload.items() if k in known}
    return cls(**kwargs)  # type: ignore[call-arg]


def _expected_schema_version(cls: type) -> int:
    """The ``schema_version`` default declared on the dataclass."""
    for f in fields(cls):
        if f.name == "schema_version":
            return int(f.default)  # type: ignore[arg-type]
    raise TypeError(
        f"{cls.__name__} is not a phase-result dataclass "
        f"(missing schema_version field)"
    )
