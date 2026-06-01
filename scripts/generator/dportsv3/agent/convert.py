"""Convert-flow — LLM tool loop for the dops-conversion job type.

20c handles the deterministic path. This module handles the tail
the deterministic translator could not — ports whose
``Makefile.DragonFly`` has conditional blocks, ports with raw
``diffs/``, etc. It mirrors the patch flow's shape: build a markdown
payload, drive the attempt_loop with the ``CONVERT_SYSTEM`` prompt,
parse a JSON Conversion Proof block from the final response.

The runner is responsible for persisting the result to the bundle/
tracker. This module only knows about prompt → response.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import attempt_loop
from .attempt_loop import PatchResult

if TYPE_CHECKING:
    from dportsv3.agent.phase_result import TriageResult


@dataclass
class ConvertResult:
    """Outcome of one convert-job attempt loop."""

    success: bool
    proof: dict | None
    raw_result: PatchResult
    status: str


_STATUS_TYPE_TOKENS = {"PORT", "MASK", "DPORT", "LOCK"}


def read_status_port_type(port_dir: Path) -> str | None:
    """Q2: Resolve the canonical port type from ``ports/<origin>/STATUS``.

    Returns lowercase ``"port"`` / ``"mask"`` / ``"dport"`` / ``"lock"``
    when STATUS's first line begins with the matching uppercase token,
    or None when STATUS is absent, unreadable, empty, or starts with
    an unrecognized token.

    The compose-time fallback (``compat.infer_compat_port_type``)
    treats absent/unrecognized STATUS as ``"port"`` (the default),
    but the convert handler needs the raw signal — including ``None``
    — to decide whether the port has a load-bearing type that must
    be carried into ``overlay.dops`` before STATUS is removed.
    """
    status_file = port_dir / "STATUS"
    if not status_file.is_file():
        return None
    try:
        first = status_file.read_text().splitlines()[0].strip()
    except (OSError, IndexError):
        return None
    if not first:
        return None
    token = first.split()[0].upper() if first else ""
    if token in _STATUS_TYPE_TOKENS:
        return token.lower()
    return None


def build_convert_payload(
    *,
    origin: str,
    repo_root: Path,
    classified_record: dict,
    deterministic_result: dict,
    dops_quickref_text: str,
    playbooks_text: str = "",
    triage_result: "TriageResult | None" = None,
) -> str:
    """Assemble the markdown payload handed to ``CONVERT_SYSTEM``.

    The payload deliberately *narrows* the agent's scope to the long
    tail. The deterministic translator's already-generated ops appear
    so the agent knows what work is done; the unsupported items
    appear with their source context so the agent has what it needs
    to translate them.

    Step 36-6: ``triage_result`` is the typed ``TriageResult`` from
    the same bundle's preceding triage (None when convert was
    operator-fired against an origin with no failure bundle, or for
    deterministic-convert paths that don't pass one in). When
    present, the rendered payload includes an "Original build
    failure (from triage)" section so the convert agent can see what
    actually failed in dsynth — substrate vs. plist vs. compile —
    and pick a strategy aligned with the real root cause instead of
    speculating beyond the compat artifacts.
    """
    from dportsv3.migration.convert import _parse_makefile_dragonfly

    port_dir = repo_root / "ports" / origin
    # Q2: surface the STATUS-encoded port type so the agent emits a
    # matching ``type`` directive in overlay.dops. The handler's
    # post-conversion safety guard refuses to delete STATUS if the
    # types don't match — this section is the agent's chance to get
    # it right on the first pass.
    expected_port_type = read_status_port_type(port_dir)

    # convert_record's return doesn't expose the parsed ops list —
    # re-run the parser to surface what the translator could handle.
    # Pure parse, no side effects.
    auto_ops: list[str] = []
    reasons: list[str] = list(deterministic_result.get("errors") or [])
    mk_path = port_dir / "Makefile.DragonFly"
    if mk_path.exists():
        parsed_ops, parsed_errors = _parse_makefile_dragonfly(mk_path)
        auto_ops = parsed_ops
        # Prefer parser errors over convert_record's d.code list —
        # the parser ones are more specific (unsupported_line:<line>).
        if parsed_errors:
            reasons = parsed_errors

    sections: list[str] = [
        f"# dops conversion task — `{origin}`",
        "",
        f"Repository root: `{repo_root}`",
        f"Port directory: `{port_dir}`",
        f"Classifier bucket: `{classified_record.get('bucket', '?')}`",
        f"Classifier reasons: `{classified_record.get('classification_reasons', [])}`",
        "",
    ]

    # Q2: state the expected dops type directive *before* anything
    # else the agent reads. If STATUS encodes a non-default type
    # (mask/dport/lock), the dops file MUST carry the matching
    # ``type`` directive or the handler's safety guard will refuse
    # to delete STATUS — leaving the substrate half-migrated.
    if expected_port_type and expected_port_type != "port":
        sections.append("## Expected port type")
        sections.append("")
        sections.append(
            f"`ports/{origin}/STATUS` declares this port as "
            f"**`{expected_port_type.upper()}`**. Your `overlay.dops` "
            f"MUST include a matching `type {expected_port_type}` "
            f"directive in the header. Without it the planner defaults "
            f"to `port` and the handler will refuse to delete STATUS "
            f"(it would silently change the port's behavior — "
            f"e.g. `mask` means \"deny this upstream port\"; losing "
            f"that signal would start materializing a port we "
            f"explicitly denied)."
        )
        sections.append("")
    elif expected_port_type == "port":
        # Default type — emit the directive for clarity, but the
        # handler does not enforce its presence (an absent ``type``
        # directive defaults to ``port`` in the planner).
        sections.append("## Expected port type")
        sections.append("")
        sections.append(
            f"`ports/{origin}/STATUS` declares this port as "
            f"`PORT` (the default). Emit `type port` in your "
            f"`overlay.dops` header for clarity; the planner's "
            f"default is also `port` so an absent directive would "
            f"work but the explicit form is what conventions show."
        )
        sections.append("")

    # Step 36-6: surface the originating build failure the convert
    # agent was dispatched in response to. Without this the agent
    # only sees substrate signals (Makefile.DragonFly, STATUS,
    # classified bucket) and can't tell whether the dsynth failure
    # was about substrate (which conversion can address) or about
    # an unrelated layer like plist drift / compile error / missing
    # dep (which it can't). Lets the agent narrow its overlay to
    # what the substrate actually needs and avoid speculative edits
    # against layers it can't affect. Render before the deterministic
    # translator section so the agent reads root-cause context first.
    if triage_result is not None:
        sections += [
            "## Original build failure (from triage)",
            "",
            f"- Classification: `{triage_result.classification or 'unknown'}`",
            f"- Confidence: `{triage_result.confidence or 'unknown'}`",
            "",
        ]
        rc = (triage_result.root_cause or "").strip()
        if rc:
            sections += ["**Root cause:**", "", rc, ""]
        ev = (triage_result.evidence_excerpt or "").strip()
        if ev:
            sections += ["**Evidence excerpt:**", "", ev, ""]
        sections += [
            "Use this to decide whether the build failure is in a "
            "layer the substrate conversion can actually address. "
            "If the root cause is unrelated to compat artifacts "
            "(e.g. plist drift, configure/compile bug, missing dep), "
            "keep the overlay minimal — translate only what's needed "
            "to retire the compat artifacts and leave the unrelated "
            "issue for the patch flow.",
            "",
        ]

    sections += [
        "## Deterministic translator status",
        "",
        f"- status: `{deterministic_result.get('status', '?')}`",
        f"- parse_ok: `{deterministic_result.get('parse_ok')}`",
        f"- check_ok: `{deterministic_result.get('check_ok')}`",
        f"- plan_ok: `{deterministic_result.get('plan_ok')}`",
        f"- deterministic_ok: `{deterministic_result.get('deterministic_ok')}`",
        "",
    ]

    if auto_ops:
        sections.append("## Deterministic ops already produced")
        sections.append("")
        sections.append("```dops")
        sections.extend(auto_ops)
        sections.append("```")
        sections.append("")

    sections.append("## Unsupported items (your job)")
    sections.append("")
    if not reasons:
        sections.append("_(none — translator handled everything; verify and proof)_")
    else:
        for reason in reasons:
            sections.append(f"- `{reason}`")
    sections.append("")

    # Surface the legacy artifacts so the agent has the raw text to
    # translate. Keep each excerpt short to bound tokens.
    mk_path = port_dir / "Makefile.DragonFly"
    if mk_path.exists():
        sections.append("## Source: `Makefile.DragonFly`")
        sections.append("")
        sections.append("```make")
        try:
            text = mk_path.read_text()
            if len(text) > 8000:
                text = text[:8000] + "\n... (truncated)"
            sections.append(text)
        except OSError as exc:
            sections.append(f"<read error: {exc}>")
        sections.append("```")
        sections.append("")

    diffs_dir = port_dir / "diffs"
    if diffs_dir.exists():
        sections.append("## Source: `diffs/`")
        sections.append("")
        for diff_path in sorted(p for p in diffs_dir.iterdir() if p.is_file()):
            sections.append(f"### `diffs/{diff_path.name}`")
            sections.append("")
            sections.append("```diff")
            try:
                text = diff_path.read_text()
                if len(text) > 4000:
                    text = text[:4000] + "\n... (truncated)"
                sections.append(text)
            except OSError as exc:
                sections.append(f"<read error: {exc}>")
            sections.append("```")
            sections.append("")

    dragonfly_dir = port_dir / "dragonfly"
    if dragonfly_dir.exists():
        sections.append("## Source: `dragonfly/`")
        sections.append("")
        for entry in sorted(p for p in dragonfly_dir.iterdir() if p.is_file()):
            sections.append(f"### `dragonfly/{entry.name}`")
            sections.append("")
            sections.append("```")
            try:
                text = entry.read_text()
                if len(text) > 4000:
                    text = text[:4000] + "\n... (truncated)"
                sections.append(text)
            except (OSError, UnicodeDecodeError) as exc:
                sections.append(f"<read error: {exc}>")
            sections.append("```")
            sections.append("")

    sections.append("## dops syntax reference")
    sections.append("")
    sections.append(dops_quickref_text)
    sections.append("")

    if playbooks_text:
        sections.append(playbooks_text)
        if not playbooks_text.endswith("\n"):
            sections.append("")

    return "\n".join(sections)


_CONVERSION_PROOF_RE = re.compile(
    r"##\s+Conversion\s+Proof\s*\(JSON\).*?```(?:json)?\s*(.*?)```",
    re.DOTALL | re.IGNORECASE,
)
_LAST_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def parse_conversion_proof(response_text: str) -> dict | None:
    """Extract the JSON proof block from the agent's final response.

    Tries a labeled heading first ("## Conversion Proof (JSON)"),
    then falls back to the last fenced JSON block in the text. Returns
    None if no parseable JSON object is found.
    """
    if not response_text:
        return None

    m = _CONVERSION_PROOF_RE.search(response_text)
    if m:
        try:
            return json.loads(m.group(1).strip())
        except (json.JSONDecodeError, TypeError):
            pass

    # Fallback: scan all fenced JSON blocks, return the last
    # parseable one. Defends against the agent omitting the heading.
    candidates = _LAST_JSON_BLOCK_RE.findall(response_text)
    for raw in reversed(candidates):
        try:
            obj = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(obj, dict) and "origin" in obj:
            return obj
    return None


def run(
    payload: str,
    *,
    tier,
    env: str,
    model: str,
    api_base: str | None = None,
    api_key: str | None = None,
    custom_llm_provider: str | None = None,
    timeout: int = 600,
    max_tool_turns: int = 30,
    on_event=None,
    session_dump=None,
) -> ConvertResult:
    """Drive the attempt_loop with the CONVERT_SYSTEM prompt.

    Returns a ConvertResult. ``success`` is True iff the agent
    emitted a Conversion Proof block whose required fields parse
    (origin matches, at least one bucket present, no parse errors).
    Full verification (Step 20e) lives in the runner once dsynth_build
    is wired in.
    """
    from . import prompts
    from .tools import CONVERT_TOOL_NAMES

    def _convert_is_success(p: dict | None) -> bool:
        """attempt_loop's stop condition for convert.

        Two requirements, both contractual with CONVERT_SYSTEM:

        - ``origin`` field present (it's the Conversion Proof, not
          some other JSON the agent emitted along the way).
        - ``validate_dops_ok`` is exactly ``True`` — the agent
          asserts the most recent ``validate_dops`` call passed.
          Without this gate, the agent can give up after one
          validate failure and ship a broken proof; the handler
          would then redo the same engine check via compose and
          reject it, wasting the attempt.

        If either fails, attempt_loop runs another attempt (up to
        ``max_iterations``) with the failure context appended, so
        the agent gets feedback rather than silently shipping
        garbage."""
        if not isinstance(p, dict):
            return False
        if not isinstance(p.get("origin"), str):
            return False
        return p.get("validate_dops_ok") is True

    raw = attempt_loop.run(
        payload,
        tier=tier,
        env=env,
        model=model,
        api_base=api_base,
        api_key=api_key,
        custom_llm_provider=custom_llm_provider,
        timeout=timeout,
        max_tool_turns=max_tool_turns,
        on_event=on_event,
        system_prompt=prompts.CONVERT_SYSTEM,
        tool_whitelist=CONVERT_TOOL_NAMES,
        agent_flow="convert",
        proof_parser=parse_conversion_proof,
        is_success=_convert_is_success,
        session_dump=session_dump,
    )

    proof = parse_conversion_proof(raw.final_text or "")
    if proof is None:
        return ConvertResult(
            success=False,
            proof=None,
            raw_result=raw,
            status="no_conversion_proof_block",
        )

    # Minimal proof sanity checks. The agent gets a clear failure
    # reason it could surface next attempt.
    if not isinstance(proof.get("origin"), str):
        return ConvertResult(
            success=False, proof=proof, raw_result=raw,
            status="proof_missing_origin",
        )

    has_any_bucket = any(
        proof.get(key) for key in (
            "framework_migrated_to_dops",
            "source_migrated_to_semantic",
            "source_patches_retained",
            "mechanical_ops_written",
        )
    )
    if not has_any_bucket:
        return ConvertResult(
            success=False, proof=proof, raw_result=raw,
            status="proof_missing_bucket",
        )

    return ConvertResult(
        success=True,
        proof=proof,
        raw_result=raw,
        status="conversion_proof_parsed",
    )
