"""Manual smoke test for the triage flow + tier dispatch (phase 3 step 5).

Fixtures a bundle with a synthetic but realistic error log, calls
dportsv3.agent.triage.run against a real LLM, then resolves the
trust tier via policy.tier_for and reports what the runner would
do (auto-enqueue patch vs. drop to MANUAL).

Usage:
    export DP_TEST_MODEL='deepseek/deepseek-v4-flash'
    export DP_TEST_API_KEY='YOUR_KEY'
    export DP_TEST_PROVIDER=          # optional; for openai-compat relays
    export DP_TEST_API_BASE=          # optional
    export DP_TEST_FIXTURE='compile-error'    # or 'plist-error' | 'unknown'
    ./scripts/generator/.venv/bin/python -m dportsv3.agent._manual_test_triage_tier

Fixtures available:
- compile-error  — readline-like 'lvalue required' compile error (expected
                   tier: ASSIST given decent confidence)
- plist-error    — a missing-file/extra-file pkg-plist error (expected
                   tier: AUTO if model is confident)
- unknown        — an opaque generic failure (expected tier: MANUAL)
"""

from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
from pathlib import Path

from dportsv3.agent import llm, policy, triage


# -----------------------------------------------------------------------------
# Fixture bundles — drop-in error.txt snippets representative of real
# dsynth failures. We don't fabricate the LLM's response; we feed it a
# real failure shape and let it classify.
# -----------------------------------------------------------------------------

FIXTURES = {
    "compile-error": {
        "origin": "devel/readline",
        "errors": """\
===> Building for readline-8.3
gcc -DHAVE_CONFIG_H -DRL_LIBRARY_VERSION='"8.3"' -DSHELL -O2 -pipe -fPIC \\
    -c -o terminal.o terminal.c
./terminal.c:583:13: error: lvalue required as left operand of assignment
  dumbterm = STREQ (term, "dumb") || STREQ (term, "vt52");
           ^
1 error generated.
*** Error code 1
Stop.
make[1]: stopped making "all" in /construction/devel/readline/work/readline-8.3
*** Error code 1
""",
        "meta": "origin=devel/readline\nprofile=DragonFly\nbundle_id=fixture-compile\n",
    },
    "plist-error": {
        "origin": "lang/python311",
        "errors": """\
===> Stage Compare reports
===> Found user-supplied files in PREFIX but not in pkg-plist:
     lib/python3.11/site-packages/__pycache__/test_module.cpython-311.pyc
===> Building of pkg-plist failed; some files are extra
*** Error code 1
""",
        "meta": "origin=lang/python311\nprofile=DragonFly\nbundle_id=fixture-plist\n",
    },
    "unknown": {
        "origin": "deskutils/somewhere",
        "errors": """\
===> Trying to stage something
[error output truncated due to log rotation]
*** Error code 137
""",
        "meta": "origin=deskutils/somewhere\nprofile=DragonFly\nbundle_id=fixture-unknown\n",
    },
}


def _install_session_dump(trace_path: Path) -> None:
    """Wrap llm.complete so every triage turn is logged to a JSONL file."""
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    trace_path.write_text("")
    real_complete = llm.complete

    def traced_complete(messages, **kw):
        resp = real_complete(messages, **kw)
        rec = {
            "kind": "llm_call",
            "model": kw.get("model"),
            "n_messages": len(messages),
            "response": {
                "text": (resp.text or "")[:1500],
                "reasoning_content": (resp.reasoning_content or "")[:600],
                "usage": {
                    "prompt": resp.usage.prompt_tokens,
                    "completion": resp.usage.completion_tokens,
                    "total": resp.usage.total_tokens,
                },
            },
        }
        with trace_path.open("a") as f:
            f.write(json.dumps(rec) + "\n")
        return resp

    llm.complete = traced_complete
    triage.llm.complete = traced_complete


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(name)s %(message)s")

    model = os.environ.get("DP_TEST_MODEL")
    if not model:
        print("error: set DP_TEST_MODEL", file=sys.stderr)
        return 2
    api_base = os.environ.get("DP_TEST_API_BASE") or None
    api_key = os.environ.get("DP_TEST_API_KEY") or None
    provider = os.environ.get("DP_TEST_PROVIDER") or None
    fixture_name = os.environ.get("DP_TEST_FIXTURE", "compile-error")

    fixture = FIXTURES.get(fixture_name)
    if fixture is None:
        print(f"error: unknown fixture {fixture_name!r}; choose from "
              f"{list(FIXTURES)}", file=sys.stderr)
        return 2

    # Fixture a minimal bundle directory.
    bundle_dir = Path(tempfile.mkdtemp(prefix=f"dp-triage-{fixture_name}-"))
    (bundle_dir / "analysis").mkdir()
    (bundle_dir / "meta.txt").write_text(fixture["meta"])
    (bundle_dir / "errors.txt").write_text(fixture["errors"])

    trace_path = bundle_dir / "session.jsonl"
    _install_session_dump(trace_path)

    # Build a triage payload directly — short and focused.
    payload = f"""# Triage Job (fixture)

## Origin
{fixture["origin"]}

## Reported errors
```
{fixture["errors"]}
```

Classify the failure and assign confidence according to the system prompt
output format.
"""

    print("---")
    print(f"fixture: {fixture_name}")
    print(f"origin:  {fixture['origin']}")
    print(f"model:   {model}")
    print(f"bundle:  {bundle_dir}")
    print("---")
    print()

    result = triage.run(
        payload,
        bundle_dir=bundle_dir,
        model=model,
        api_base=api_base,
        api_key=api_key,
        custom_llm_provider=provider,
    )

    print(f"classification: {result.classification!r}")
    print(f"confidence:     {result.confidence!r}")
    print(f"snippet_rounds: {result.snippet_rounds}")
    print(f"tokens (p/c/t): {result.usage.prompt_tokens}/"
          f"{result.usage.completion_tokens}/{result.usage.total_tokens}")
    print()

    # Resolve tier via the same policy the runner uses.
    policy_path = os.environ.get(
        "DP_HARNESS_POLICY",
        str(Path(__file__).resolve().parents[4] / "config" / "agentic-policy.json"),
    )
    pol = policy.load_policy(policy_path)
    tier = policy.tier_for(pol, result.classification, result.confidence)

    print(f"policy:         {policy_path}")
    print(f"tier:           {tier.name}")
    print(f"max_iterations: {tier.max_iterations}")
    print(f"max_tokens:     {tier.max_tokens}")
    print()
    if tier.name == "MANUAL":
        print("decision:       no auto-enqueue (MANUAL tier)")
        print("                operator must hand-fire a patch job if they want to proceed")
    else:
        print(f"decision:       AUTO-ENQUEUE patch job (tier={tier.name})")
        print(f"                patch flow gets budget: {tier.max_iterations} attempts × "
              f"{tier.max_tokens} tokens")
    print()
    print("final triage text (first 600 chars):")
    print(result.text[:600])
    print()
    print(f"trace: {trace_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
