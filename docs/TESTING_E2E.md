# End-to-end testing — agentic loop

Three layers of testing exist for the agentic pipeline. Use the layer
that matches the change you're validating.

## 1. Unit / integration tests (`pytest`)

Fast, deterministic, no LLM, no real dsynth, no real dev-env. Run as
part of every change.

```
scripts/generator/.venv/bin/python -m pytest scripts/generator/tests/ -q
```

Coverage:

| Area | Test module |
|---|---|
| Tracker DB schema + queries | `test_tracker_api.py`, `test_tracker_integration.py`, `test_tracker_queue.py` |
| State DB shared-writer concurrency | `test_state_db_concurrency.py` |
| Agentic read endpoints | `test_tracker_agentic_endpoints.py` |
| Agentic HTML views | `test_tracker_agentic_views.py` |
| Tracker progress UI adapter | `test_tracker_progress.py` |

The venv at `scripts/generator/.venv` is created by the `dportsv3`
wrapper on first run. To install pytest + mypy into it:
`scripts/generator/.venv/bin/pip install -e 'scripts/generator[dev]'`.

## 2. Manual harness fixtures

For changes to the agent runtime (`dportsv3.agent.*`) where a real
LLM + a real dev-env round-trip is the only way to be sure. Each
fixture takes its config from environment variables and prints what
landed on disk.

| Fixture | What it exercises |
|---|---|
| `scripts/generator/dportsv3/agent/_manual_test_tool_loop.py` | LLM + tool dispatch loop against a real dev-env, one-shot inspection task |
| `scripts/generator/dportsv3/agent/_manual_test_triage_tier.py` | Triage flow + tier dispatch on fabricated bundles (compile / plist / unknown) |
| `scripts/generator/dportsv3/agent/_manual_test_patch_flow.py` | Full patch flow + attempt loop end-to-end against a real port |

Common env vars:

| Var | Meaning |
|---|---|
| `DP_TEST_MODEL` | LiteLLM model string (e.g. `deepseek/deepseek-v4-flash`) |
| `DP_TEST_API_KEY` | Provider key |
| `DP_TEST_API_BASE` | Optional custom endpoint |
| `DP_TEST_ENV` | Name of a prepared `dportsv3 dev-env` (must be `ready`) |

The patch fixture produces `rebuild_proof.json` + `changes.diff` +
`patch_audit.json` on disk in the bundle output dir; check those to
verify the loop actually built something.

## 3. Live end-to-end on a real builder

For shaking out hook / queue / runner / tracker integration as a whole
on the dfly box. Slowest path; use when:

- A hook contract or bundle schema changed
- Tracker endpoints changed shape
- Trust-tier policy or `agentic-policy.json` was edited

### Prerequisites

```
pkg install py311-sqlite3 py311-pydantic2 py311-pydantic-core \
            py311-fastapi py311-uvicorn py311-watchfiles \
            py311-uvloop py311-httptools py311-websockets \
            py311-python-dotenv
```

A prepared dev-env for the target: `dportsv3 dev-env create NAME @TARGET`,
then `dportsv3 dev-env status NAME` must report `ready`.

### Run

```
# 1. Start artifact-store (writes bundles + state.db rows)
dportsv3 artifact-store --logs-root /build/synth/logs &

# 2. Start tracker (reads state.db, serves UI + read endpoints)
DPORTSV3_STATE_DB=/build/synth/logs/evidence/state.db \
DPORTSV3_ARTIFACT_ROOT=/build/synth/logs/evidence \
  dportsv3 tracker serve --port 8080 &

# 3. Start the queue runner
DPORTSV3_STATE_DB=/build/synth/logs/evidence/state.db \
DPORTSV3_TRACKER_URL=http://127.0.0.1:8080 \
ARTIFACT_STORE_URL=http://127.0.0.1:8788 \
DP_HARNESS_TRIAGE_MODEL=... \
DP_HARNESS_PATCH_MODEL=... \
DP_HARNESS_TRIAGE_API_KEY=... \
DP_HARNESS_PATCH_API_KEY=... \
  ./dportsv3 agent-queue-runner --queue-root /build/synth/logs/evidence/queue &

# 4. Trigger a real build (the dsynth profile should source the hooks)
dsynth -p 2026Q2 -S -y build devel/known-failing-port
```

### Verify

| Step | Check |
|---|---|
| Hook fires on failure | New row in `bundles` for the origin |
| Bundle uploaded | `ls /build/synth/logs/evidence/blobstore/objects/sha256/...` |
| Triage queued | `curl localhost:8080/api/jobs?state=pending` |
| Triage ran | `curl localhost:8080/api/bundles/<id>` → `artifacts` includes `analysis/triage.md` |
| Patch ran (if AUTO/ASSIST) | `analysis/rebuild_proof.json` present, `rebuild_ok` set |
| Tracker UI | Open `http://host:8080/target/@2026Q2` and watch the progress view update |

### Smoking individual surfaces without a real build

`docs/agentic-phase4-db.md` has a seed-script for state.db that
populates bundles/jobs/runs with mixed targets. Useful for UI smokes
when you don't want to wait for a real dsynth run. The same snippet
works for shaking the agentic read endpoints (`/api/bundles`,
`/api/jobs`, `/api/events`) and the progress adapter
(`/api/progress/{target}/summary.json`).

## Where things go wrong

| Symptom | Layer | Diagnosis |
|---|---|---|
| pytest can't import dportsv3 | venv | `pip install -e scripts/generator[dev]` into the venv, not the host Python |
| Manual fixture hangs at the first LLM call | manual harness | API key, custom base URL, or model name routing — check the litellm error string |
| Bundle reaches state.db but no job is enqueued | live e2e | Trust tier resolved to MANUAL, or the runner isn't subscribed to that target |
| Patch loop stops with `budget-exhausted` | manual / live | Token budget too small for the failure class; bump the tier in `config/agentic-policy.json` |
| Tracker UI shows stale data | live e2e | Restart tracker — uvicorn doesn't auto-reload templates/static |
