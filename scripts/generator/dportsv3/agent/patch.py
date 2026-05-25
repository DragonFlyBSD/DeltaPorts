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
    )
