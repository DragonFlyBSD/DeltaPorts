"""Intent validation (Step 25b).

Two layers per the design doc §9:

1. **Schema-level validation** via jsonschema (universal shape:
   required fields, types, enum constraints). Produced by
   ``parse_intent(dict_or_json) -> Intent``.

2. **Mode-sensitive + invariant checks** done by the Translator at
   apply time, knowing the current substrate state (e.g.
   "target file must exist" — depends on the env).

Layer 1 lives here; Layer 2 lives in translator.py /
_dops.py. The two split because Layer 1 can run anywhere, anytime
(useful for `intent_reference` tool dry-runs); Layer 2 needs the
env.
"""

from __future__ import annotations

import json
from importlib import resources
from typing import Any

import jsonschema

from .grammar import INTENT_DATACLASSES, INTENT_TYPES, Intent


class IntentError(ValueError):
    """Raised on validation failure (schema or semantic).

    Carries a short summary plus the underlying jsonschema /
    semantic detail. The intent the validator was checking is
    attached as ``self.intent`` (raw dict) so the runner can log it.
    """

    def __init__(self, message: str, *,
                 intent: dict[str, Any] | None = None,
                 detail: str = ""):
        super().__init__(message)
        self.intent = intent
        self.detail = detail

    def for_log(self) -> dict[str, Any]:
        return {
            "error": str(self),
            "detail": self.detail,
            "intent_type": (self.intent or {}).get("type"),
        }


# Schema cache. Lazy-loaded on first parse to keep import-time cheap.
_SCHEMAS: dict[str, dict[str, Any]] = {}


def _load_schema(intent_type: str) -> dict[str, Any]:
    if intent_type not in _SCHEMAS:
        try:
            with resources.files(
                "dportsv3.agent.edit_intent.schemas"
            ).joinpath(f"{intent_type}.json").open("r") as fp:
                _SCHEMAS[intent_type] = json.load(fp)
        except (FileNotFoundError, ModuleNotFoundError) as exc:
            raise IntentError(
                f"no JSON schema bundled for intent type {intent_type!r}",
                detail=str(exc),
            )
    return _SCHEMAS[intent_type]


def schema_for(intent_type: str) -> dict[str, Any]:
    """Public accessor — backs the ``intent_reference`` tool (25c)."""
    if intent_type not in INTENT_TYPES:
        raise IntentError(
            f"unknown intent type {intent_type!r}; known: "
            f"{', '.join(INTENT_TYPES)}",
        )
    return _load_schema(intent_type)


def parse_intent(raw: dict[str, Any] | str) -> Intent:
    """Validate a wire-format intent and return its dataclass.

    Accepts either a Python dict or a JSON string. Validates
    against the per-type schema; raises IntentError on any
    schema violation with the path + reason in the detail.

    Type discrimination on the ``type`` field. Unknown types
    fail with a clear "unknown intent type" message rather than
    the more cryptic jsonschema diagnostic.
    """
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise IntentError(
                f"intent body is not valid JSON: {exc}",
                detail=str(exc),
            )
    if not isinstance(raw, dict):
        raise IntentError(
            f"intent body must be a JSON object, got {type(raw).__name__}",
        )
    intent_type = raw.get("type")
    if intent_type not in INTENT_TYPES:
        raise IntentError(
            f"unknown or missing intent type: {intent_type!r}; "
            f"known: {', '.join(INTENT_TYPES)}",
            intent=raw,
        )
    schema = _load_schema(intent_type)
    try:
        jsonschema.validate(raw, schema)
    except jsonschema.ValidationError as exc:
        # Walk the path so the LLM sees where the error is.
        path = ".".join(str(p) for p in exc.absolute_path) or "(root)"
        raise IntentError(
            f"intent {intent_type!r} failed schema at {path}: {exc.message}",
            intent=raw,
            detail=str(exc),
        )
    # Schema passed — construct the dataclass. Drop fields the
    # dataclass doesn't know about (shouldn't exist after schema
    # validation since additionalProperties=false, but defense in
    # depth). Defaults are filled in by the dataclass itself.
    cls = INTENT_DATACLASSES[intent_type]
    fields = {f for f in cls.__dataclass_fields__}
    kwargs = {k: v for k, v in raw.items() if k in fields}
    try:
        intent_obj = cls(**kwargs)
    except TypeError as exc:
        # E.g. missing required dataclass field that the schema let
        # through. Shouldn't happen if schemas + dataclasses are in
        # sync, but fail loud rather than crash later.
        raise IntentError(
            f"intent {intent_type!r} could not be constructed: {exc}",
            intent=raw,
            detail=str(exc),
        )
    # Semantic checks the JSON schema can't easily express.
    _check_semantics(intent_obj, raw)
    return intent_obj


def _check_semantics(intent_obj, raw: dict) -> None:
    """Post-schema semantic checks for parsed intents.

    Catches cases the JSON schema accepts as well-formed but the
    grammar/translator would mishandle. Currently:

    - ``replace_in_patch`` whose ``target`` ends in ``.dops``: the
      dops-mode renderer expresses this as a deferred
      ``text.replace_once`` directive in ``overlay.dops``. If the
      target IS ``overlay.dops`` the resulting directive references
      the DSL file itself, which is meta-recursive nonsense — and
      the agent calling it N times appends N escalating directives
      (the "escalating quine" corruption observed on
      ``devel_gperf-20260526-064013Z``). ``replace_in_patch`` is
      strictly for hunks inside patch files; edits to the DSL go
      through ``change_makefile`` / ``drop_patch`` / ``add_patch``.

    - ``replace_in_patch`` whose ``target`` starts with
      ``dragonfly/``: patch files are output artifacts produced by
      ``add_patch`` (or ``add_patch from_dupe=true``). Editing a
      diff in place to nudge line numbers or context produces a
      patch that lies about its own bytes — every text edit shifts
      the hunk body but not the hunk header, and partial edits drift
      arbitrarily. The correct recovery from a failing/drifted
      patch is ``drop_patch`` + ``add_patch`` (with a corrected
      inline diff) or ``add_patch from_dupe=true`` (regenerate from
      a fresh WRKSRC edit). Observed driving the
      devel_jwasm-20260602-204312Z anti-pattern: malformed inline
      diff → drop_patch → add_patch refused (orphan file) →
      replace_in_patch loop → broken overlay.
    """
    intent_type = getattr(intent_obj, "type", None) or raw.get("type")
    if intent_type == "replace_in_patch":
        target = getattr(intent_obj, "target", "") or ""
        if target.endswith(".dops"):
            raise IntentError(
                f"replace_in_patch refuses target={target!r}: this "
                f"intent edits patch hunks, not the dops DSL file. "
                f"In dops mode the renderer would append a "
                f"`text.replace_once` directive referencing the DSL "
                f"itself, producing a self-referential overlay. To "
                f"remove a `patch apply` block use drop_patch; to "
                f"remove a `file materialize dragonfly/patch-*` "
                f"line use drop_patch (extended in this codebase to "
                f"match both forms); to add an arbitrary file use "
                f"add_file.",
                intent=raw,
            )
        if target.startswith("dragonfly/"):
            raise IntentError(
                f"replace_in_patch refuses target={target!r}: patch "
                f"files under dragonfly/ are output artifacts, not "
                f"edit targets. Text-editing a diff to nudge context "
                f"or line numbers produces a patch that lies — the "
                f"hunk body shifts but the hunk header does not, and "
                f"every subsequent compose/apply silently corrupts. "
                f"To fix a failing or drifted patch: use drop_patch "
                f"+ add_patch (with a corrected inline diff), or "
                f"add_patch with from_dupe=true (regenerate the diff "
                f"from a fresh WRKSRC edit via the genpatch flow). "
                f"Never replace_in_patch a dragonfly/ file.",
                intent=raw,
            )
