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
    session_dump=None,
) -> PatchResult:
    """Run the patch agent for one bundle. Returns the PatchResult.

    The runner is responsible for persisting the result to the bundle
    (patch.md, rebuild_proof.json, changes.diff, audit JSON).

    ``on_event`` is a callback invoked with structured dicts as the
    loop progresses: ``attempt_start``, ``tool_call``, ``attempt_end``.
    Used by the runner for live activity-log writes and to build a
    tool-trace artifact. Exceptions inside the callback are swallowed.

    The patch agent edits ``ports/<origin>/overlay.dops`` directly in
    dops DSL (``put_file`` + ``validate_dops`` + ``dops_reference``)
    plus the build-loop tools — the surface returned by
    :func:`tools.patch_tool_names`.
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
        system_prompt=prompts.PATCH_SYSTEM,
        tool_whitelist=tools.patch_tool_names(),
        session_dump=session_dump,
    )
