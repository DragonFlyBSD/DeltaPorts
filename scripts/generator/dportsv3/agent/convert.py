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
from typing import Any

from . import attempt_loop
from .attempt_loop import PatchResult


@dataclass
class ConvertResult:
    """Outcome of one convert-job attempt loop."""

    success: bool
    proof: dict | None
    raw_result: PatchResult
    status: str


def build_convert_payload(
    *,
    origin: str,
    repo_root: Path,
    classified_record: dict,
    deterministic_result: dict,
    dops_quickref_text: str,
) -> str:
    """Assemble the markdown payload handed to ``CONVERT_SYSTEM``.

    The payload deliberately *narrows* the agent's scope to the long
    tail. The deterministic translator's already-generated ops appear
    so the agent knows what work is done; the unsupported items
    appear with their source context so the agent has what it needs
    to translate them.
    """
    from dportsv3.migration.convert import _parse_makefile_dragonfly

    port_dir = repo_root / "ports" / origin

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
        """attempt_loop's stop condition for convert: any Conversion
        Proof with an ``origin`` field and at least one bucket
        populated. The detailed validity check in
        :func:`run` (below) inspects the same proof and reports the
        finer status, so this is intentionally permissive — it
        prevents attempt_loop from looping after a viable proof
        lands."""
        if not isinstance(p, dict):
            return False
        if not isinstance(p.get("origin"), str):
            return False
        return any(
            p.get(key) for key in (
                "framework_migrated_to_dops",
                "source_migrated_to_semantic",
                "source_patches_retained",
                "mechanical_ops_written",
                "files_added",
            )
        )

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
        proof_parser=parse_conversion_proof,
        is_success=_convert_is_success,
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
