"""Edit-intent DSL — public surface (plan Step 25b).

The patch agent emits intents (declarative descriptions of edits)
instead of file writes; this module parses, validates, and applies
them, returning a deterministic substrate diff per intent.

See ``docs/edit-intent-design.md`` for the normative spec.

Module layout:
- ``grammar``     — @dataclass per intent type + Intent union
- ``validator``   — parse_intent, IntentError, schema_for
- ``translator``  — Translator(workspace, origin, mode).apply
- ``log``         — IntentLog accumulator + size caps
- ``_dops``       — dops-mode renderers (one per intent type)
- ``schemas/``    — one JSON Schema per intent type
"""

from .grammar import (
    INTENT_TYPES,
    INTENT_DATACLASSES,
    Intent,
    ReplaceInPatch,
    DropPatch,
    AddPatch,
    AddFile,
    ChangeMakefile,
    BumpPortrevision,
    ReplaceInDopsBlock,
    AddDops,
)
from .log import IntentLog, IntentLogEntry, SCHEMA_VERSION
from .translator import EditResult, Mode, Translator
from .validator import IntentError, parse_intent, schema_for


__all__ = [
    # Grammar
    "INTENT_TYPES", "INTENT_DATACLASSES",
    "Intent",
    "ReplaceInPatch", "DropPatch", "AddPatch", "AddFile",
    "ChangeMakefile", "BumpPortrevision",
    "ReplaceInDopsBlock", "AddDops",
    # Validator
    "parse_intent", "schema_for", "IntentError",
    # Translator
    "Translator", "EditResult", "Mode",
    # Log
    "IntentLog", "IntentLogEntry", "SCHEMA_VERSION",
]
