# Agentic build assistance — operator guide

This document describes how the agentic loop fits into the DeltaPorts
build flow today (post-Phase-5). For the design history behind it, see
the per-phase commits referenced from `docs/agentic-consolidation-plan.md`.

## What the loop does

For every dsynth build a per-port hook fires when a port fails. The hook
uploads the failure evidence as a "bundle" (logs, port files, metadata)
to the artifact-store. A queue runner picks the bundle up, runs an LLM
triage pass on it, classifies the failure, and — if the trust tier
permits — runs a patch attempt loop against a writable copy of the
DeltaPorts tree inside a chrooted dev-env. The patch loop ends at a
`rebuild_proof.json` recording whether dsynth liked the change. No
commits, branches, pushes, or PRs are produced; promoting a fix is a
manual operator step.

```
dsynth build ──fail──▶ hook ──▶ artifact-store (bundle)
                                       │
                                       ▼
                              agent-queue-runner
                                       │
                          ┌────────────┴────────────┐
                          ▼                         ▼
                   dportsv3.agent.triage   dportsv3.agent.patch
                   (LLM classify)          (LLM + 13 tools,
                                            attempt loop)
                                       │
                                       ▼
                              bundle.analysis/
                              ├── triage.md
                              ├── patch_audit.json
                              ├── rebuild_proof.json
                              └── changes.diff
```

## Architecture in one breath

- **One DB**: `state.db` (WAL + `busy_timeout=5000`, `foreign_keys=ON`
  on every connection). Two writers — artifact-store and tracker —
  share it.
- **One serve process** for the UI/read surface: FastAPI
  `dportsv3 tracker serve` at `:8080` by default. Builds, agentic
  bundles/jobs/runner, and HTML views are all routed through it.
- **One hook set**: `scripts/dsynth-hooks/` writes the failure bundle
  to artifact-store and the run-state to the tracker.
- **One per-port workspace primitive**: `dportsv3 dev-env` (chroot +
  writable copy-on-write overlay). The patch agent edits files in the
  overlay; the host tree is never modified.

## Services to run

| Service | Purpose | Default port |
|---|---|---|
| `dportsv3 artifact-store` | Receives bundles + `/v1/user-context` POSTs; writes to `state.db`; serves artifact streams under `/v1/artifacts/get` | 8788 |
| `dportsv3 tracker serve` | Read API + HTML views (`/`, `/target/{target}`, `/builds/{id}`, `/agentic/*`); SSE event tail | 8080 |
| `dportsv3 agent-queue-runner` | Pops bundles from `state.db`, runs triage + patch jobs against the LLM provider | — |

All three read the same `state.db`. Order of startup doesn't matter —
each one is idempotent on the schema.

## Environment

Required by the runner:

| Var | Meaning | Example |
|---|---|---|
| `DPORTSV3_STATE_DB` | Path to state.db (shared with artifact-store) | `/build/synth/logs/evidence/state.db` |
| `DPORTSV3_TRACKER_URL` | Base URL for runner→tracker lookups (bundles, ports) | `http://127.0.0.1:8080` (default) |
| `ARTIFACT_STORE_URL` | Base URL for runner→artifact-store artifact GETs | `http://127.0.0.1:8788` (default) |
| `DP_HARNESS_TRIAGE_MODEL` | LiteLLM model string for triage | `openai/gpt-5-nano` |
| `DP_HARNESS_PATCH_MODEL` | LiteLLM model string for patch | `anthropic/claude-sonnet-4` |
| `DP_HARNESS_TRIAGE_API_KEY` / `_BASE` | Provider key + optional custom endpoint | — |
| `DP_HARNESS_PATCH_API_KEY` / `_BASE` | Same for patch | — |

Required by the tracker:

| Var | Meaning |
|---|---|
| `DPORTSV3_STATE_DB` | State DB path (defaults to `$PWD/state.db`) |
| `DPORTSV3_ARTIFACT_ROOT` | Evidence dir for blob streaming (defaults to `/build/synth/logs/evidence`) |

Required by the hooks (set in `/etc/dsynth/hooks.conf` or wherever your
dsynth profile sources environment from):

| Var | Meaning |
|---|---|
| `DPORTSV3_TRACKER_URL` | Where to write tracker run state |
| `DPORTSV3_TRACKER_TARGET` | Target (e.g. `@2026Q2`); defaults from `$PROFILE` |
| `ARTIFACT_STORE_URL` | Where to upload bundles |

## Trust tiers

`config/agentic-policy.json` decides whether a triaged failure
auto-advances into a patch attempt:

- **AUTO** (`plist-error`, `fetch-checksum`, `pkg-format`): up to 2
  patch iterations, 30k tokens.
- **ASSIST** (`compile-error`, `patch-error`, `link-error`,
  `configure-error`): up to 4 iterations, 120k tokens.
- **MANUAL** (`missing-dep`, `fetch-error`, `runtime-error`,
  `dependency-conflict`, `unknown`): triage runs but no patch job is
  enqueued. An operator can still hand-fire a patch via the queue.

A `confidence_floor` downgrades the tier when the triage LLM's
reported confidence is below the floor for that tier.

## Reading the tracker UI

`http://<host>:8080/`

| Route | What |
|---|---|
| `/` | Target grid — one card per target with cumulative stat pills |
| `/target/{target}` | Live dsynth-progress UI for the latest build run on this target |
| `/builds` | All build runs across targets |
| `/builds/{run_id}` | Dsynth-progress UI scoped to one specific run |
| `/diff` | Compare two targets' current port status |
| `/agentic` | Bundles + jobs summary dashboard |
| `/agentic/bundles[?target=]` | Failure-evidence bundles |
| `/agentic/bundles/{id}` | One bundle's metadata + artifact list (links stream files from the artifact-store) |
| `/agentic/jobs[?state=&target=]` | Queue state (pending / inflight / done / failed) |
| `/agentic/runner` | Current runner heartbeat |
| `/agentic/activity` | Stage-transition log |

The progress UI polls `/api/progress/{target}/summary.json` every 10s
when a build is active; SSE on `/api/events?target=...` streams
per-target events live.

## When a fix lands

1. Look at the bundle in `/agentic/bundles/{id}`. Read `analysis/triage.md`,
   `analysis/patch_audit.json`, and `analysis/rebuild_proof.json`.
2. If `rebuild_ok` is true, the dirty edits live in the dev-env's
   writable overlay at `$(dportsv3 dev-env path NAME --writable)`.
3. Inspect with `git -C $(dportsv3 dev-env path NAME --writable)/work/DeltaPorts diff`.
4. If you accept the change, copy the diff into your own DeltaPorts
   clone, review, sign, and commit there. The agentic loop never
   touches your authoritative working tree.

## Troubleshooting

| Symptom | Check |
|---|---|
| Runner sees no bundles | Hook output (`dsynth` logs); `state.db` `bundles` table |
| Triage 401s | `DP_HARNESS_TRIAGE_API_KEY` and `_API_BASE` |
| Patch loop stalls | Tracker `/agentic/jobs/{id}` → state=inflight + last_error |
| Artifact 404 | `DPORTSV3_ARTIFACT_ROOT` on the tracker matches `--logs-root` on the artifact-store |
| Hook can't reach tracker | `DPORTSV3_TRACKER_URL` from the dsynth env; some chroot setups need a bind-mount or 127.0.0.1 only |

For deeper triage of the agent runtime itself, see `docs/TESTING_E2E.md`
for the manual fixtures under `scripts/generator/dportsv3/agent/`.
