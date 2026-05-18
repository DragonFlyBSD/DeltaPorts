"""Manual smoke test for dportsv3.agent.patch against a real LLM + dev-env.

Fixtures a minimal bundle under /tmp, calls patch.run directly (bypassing
the queue runner — orchestration is tested elsewhere). The default
target port is devel/readline, which builds cleanly, so the agent
should reach rebuild_ok=true within one or two attempts.

Usage:
    export DP_TEST_MODEL='deepseek/deepseek-v4-flash'
    export DP_TEST_API_KEY='YOUR_KEY'
    export DP_TEST_ENV='2026Q2'
    export DP_TEST_ORIGIN='devel/readline'           # default
    export DP_TEST_TIER_ITERATIONS='2'               # default
    export DP_TEST_TIER_TOKENS='40000'               # default
    ./scripts/generator/.venv/bin/python -m dportsv3.agent._manual_test_patch_flow

Prints per-attempt token counts, final status, and the rebuild_proof
JSON (or lack thereof). Bundle dir is preserved so you can inspect
the written artifacts (patch.md, rebuild_proof.json, patch_audit.json,
changes.diff).
"""

from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
from pathlib import Path

from dportsv3.agent import patch
from dportsv3.agent.policy import Tier


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(name)s %(message)s")
    logging.getLogger("dportsv3.agent").setLevel(logging.DEBUG)

    model = os.environ.get("DP_TEST_MODEL")
    if not model:
        print("error: set DP_TEST_MODEL (e.g. deepseek/deepseek-v4-flash)", file=sys.stderr)
        return 2
    api_base = os.environ.get("DP_TEST_API_BASE") or None
    api_key = os.environ.get("DP_TEST_API_KEY") or None
    provider = os.environ.get("DP_TEST_PROVIDER") or None
    env = os.environ.get("DP_TEST_ENV", "2026Q2")
    origin = os.environ.get("DP_TEST_ORIGIN", "devel/readline")

    tier = Tier(
        name="MANUAL_TEST",
        max_iterations=int(os.environ.get("DP_TEST_TIER_ITERATIONS", "2")),
        max_tokens=int(os.environ.get("DP_TEST_TIER_TOKENS", "40000")),
    )

    # Fixture a minimal bundle directory.
    bundle_dir = Path(tempfile.mkdtemp(prefix="dp-patch-smoke-"))
    (bundle_dir / "analysis").mkdir()
    (bundle_dir / "meta.txt").write_text(
        f"origin={origin}\nprofile=DragonFly\nbundle_id=smoke-fixture\n"
    )
    # Synthetic "error": port may or may not actually fail; the agent
    # decides by calling dsynth_build. Phrasing avoids over-priming.
    (bundle_dir / "errors.txt").write_text(
        f"# fixture bundle\n"
        f"Port: {origin}\n"
        f"Reported state: a recent dsynth run may have failed. Please run\n"
        f"dsynth_build to verify the current state of the port and, if\n"
        f"it fails, propose minimal edits to make it build.\n"
    )
    (bundle_dir / "analysis" / "triage.md").write_text(
        "## Classification\nunknown\n\n## Confidence\nlow\n"
    )

    payload = f"""# Patch Job (fixture)

## Origin
{origin}

## Bundle
{bundle_dir}

## Errors (fixture)
{(bundle_dir / "errors.txt").read_text()}

## Triage
Classification: unknown
Confidence: low

Verify the port's current state in the dev-env. If it builds with
dsynth_build, emit rebuild_proof.json with rebuild_ok=true. If it
fails, propose minimal DeltaPorts edits and retry.
"""

    print("---")
    print(f"bundle:  {bundle_dir}")
    print(f"env:     {env}")
    print(f"origin:  {origin}")
    print(f"model:   {model}")
    print(f"tier:    iter={tier.max_iterations} tokens={tier.max_tokens}")
    print("---")

    result = patch.run(
        payload,
        tier=tier,
        env=env,
        model=model,
        api_base=api_base,
        api_key=api_key,
        custom_llm_provider=provider,
    )

    print()
    print(f"status:       {result.status}")
    print(f"attempts:     {len(result.attempts)}")
    print(f"tokens (p/c/t): {result.usage.prompt_tokens}/"
          f"{result.usage.completion_tokens}/{result.usage.total_tokens}")
    for a in result.attempts:
        print(f"  attempt {a.attempt}: {a.tokens} tokens, rebuild_ok={a.rebuild_ok}")
    print()
    print("rebuild_proof:")
    if result.proof:
        print(json.dumps(result.proof, indent=2))
    else:
        print("  (none parsed)")
    print()
    print("final text tail (last 600 chars):")
    print(result.final_text[-600:] if result.final_text else "(empty)")
    print()
    print(f"bundle artifacts preserved at: {bundle_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
