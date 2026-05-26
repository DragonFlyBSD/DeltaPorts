"""Patch flow — thin wrapper over attempt_loop."""

from __future__ import annotations

from . import attempt_loop, prompts, tools
from .attempt_loop import PatchResult


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
    origin: str | None = None,
) -> PatchResult:
    """Run the patch agent for one bundle. Returns the PatchResult.

    The runner is responsible for persisting the result to the bundle
    (patch.md, rebuild_proof.json, changes.diff, audit JSON, and
    intent_log.json when the intent flow is enabled).

    ``on_event`` is a callback invoked with structured dicts as the
    loop progresses: ``attempt_start``, ``tool_call``, ``attempt_end``.
    Used by the runner for live activity-log writes and to build a
    tool-trace artifact. Exceptions inside the callback are swallowed.

    Prompt + tool surface selected by the
    ``DP_HARNESS_PATCH_USE_INTENT`` gate
    (:func:`tools.patch_use_intent_enabled`):

    - **Off (default):** legacy ``PATCH_SYSTEM`` prompt + the full
      patch-tool surface (``put_file`` / ``install_patches`` /
      ``emit_diff`` / ``validate_dops`` / ``dops_reference``).
      Production behavior preserved for older bundles + operators
      who haven't smoke-tested the intent flow yet.
    - **On:** new ``PATCH_INTENT_SYSTEM`` prompt + intent-only tool
      surface (``apply_intent`` / ``intent_reference`` plus the
      substrate-read tools and dsynth_build). The patch agent emits
      declarative intents; the runner records them into
      ``analysis/intent_log.json``; verify replays them drift-free.

    25d-3 retires the gate (and the legacy prompt + the port-subtree
    write tools for the patch agent) once operator smoke-tests
    confirm parity on known-good ports.
    """
    if tools.patch_use_intent_enabled():
        system_prompt = prompts.PATCH_INTENT_SYSTEM
    else:
        system_prompt = prompts.PATCH_SYSTEM

    # Between attempts, surface the prior attempt's intent log so
    # the agent doesn't re-emit an intent that already failed (the
    # attempt-boundary amnesia symptom). Only wired for the intent
    # flow + a known origin — the legacy flow has no intent log.
    prior_summary = None
    if origin and tools.patch_use_intent_enabled():
        from . import worker  # noqa: PLC0415
        prior_summary = _build_intent_summary_provider(worker, env, origin)

    return attempt_loop.run(
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
        system_prompt=system_prompt,
        tool_whitelist=tools.patch_tool_names(),
        prior_attempt_summary=prior_summary,
    )


def _build_intent_summary_provider(worker_mod, env: str, origin: str):
    """Return a zero-arg callable that snapshots the current intent
    log and renders a compact one-line-per-entry summary, or None
    if the log is empty.

    Deliberately stateless — re-runs the peek + render each call so
    the callable reflects the log at the moment of the next attempt
    boundary, not the moment the callable was constructed.
    """
    def _snapshot() -> str | None:
        log = worker_mod.peek_intent_log(env, origin)
        if log is None or not getattr(log, "intents", None):
            return None
        return _format_intent_log_summary(log)
    return _snapshot


def _format_intent_log_summary(log) -> str:
    """Render an IntentLog as compact lines for an LLM context.

    One line per entry: `- seq N: <type>(<headline>) ok|FAIL[reason]`.
    Headline picks the most identifying field per intent type so the
    agent can spot duplicate intents at a glance. Capped at 50
    entries (the log's count cap is 100; if we hit the cap an
    overflow note appears).
    """
    lines: list[str] = []
    cap = 50
    intents = list(getattr(log, "intents", []) or [])
    for entry in intents[:cap]:
        intent = entry.intent or {}
        itype = intent.get("type", "?")
        headline = _intent_headline(intent)
        outcome = "ok" if entry.ok else (
            f"FAIL[{(entry.error or '?')[:60]}]"
        )
        lines.append(f"- seq {entry.seq}: {itype}({headline}) {outcome}")
    if len(intents) > cap:
        lines.append(f"- … {len(intents) - cap} more entries truncated")
    return "\n".join(lines)


def _intent_headline(intent: dict) -> str:
    """Pick the most identifying field(s) of an intent for the
    summary line. Keeps the rendered summary readable across the
    seven intent types without dumping the full payload."""
    itype = intent.get("type", "")
    if itype == "change_makefile":
        return f"{intent.get('key', '?')}={intent.get('value', '?')!s}"
    if itype in {"replace_in_patch", "add_patch", "drop_patch"}:
        return str(intent.get("target", intent.get("path", "?")))
    if itype == "add_file":
        return str(intent.get("dest", intent.get("source", "?")))
    if itype == "bump_portrevision":
        return ""
    if itype == "replace_in_dops_block":
        return f"block={intent.get('block_name', '?')}"
    return ""
