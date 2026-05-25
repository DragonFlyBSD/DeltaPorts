"""Patch flow — thin wrapper over attempt_loop."""

from __future__ import annotations

from . import attempt_loop, tools
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
    (patch.md, rebuild_proof.json, changes.diff, audit JSON).

    ``on_event`` is a callback invoked with structured dicts as the
    loop progresses: ``attempt_start``, ``tool_call``, ``attempt_end``.
    Used by the runner for live activity-log writes and to build a
    tool-trace artifact. Exceptions inside the callback are swallowed.

    The tool surface comes from :func:`tools.patch_tool_names`, which
    conditionally includes the Step 25 edit-intent tools
    (``apply_intent`` / ``intent_reference``) based on
    ``DP_HARNESS_PATCH_USE_INTENT``. Default is the pre-Step-25 set
    so 25c lands without disturbing production behavior; 25d swaps
    the prompt once the new surface is proven.
    """
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
        tool_whitelist=tools.patch_tool_names(),
    )
