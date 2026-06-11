# `scripts/generator/dportsv3` — package map

Python package implementing the DeltaPorts v3 DSL, the compose pipeline,
the migration program, the build tracker, the agentic failure-repair
loop, and operator-facing delivery of accepted fixes. Entry points:
`python -m dportsv3` and the `dportsv3` console script (both → `cli.main`).

## Top-level modules

- `__main__.py` / `cli.py` — argparse front-end. Registers subcommands
  (`compose`, `compose-report`, `dsl`, `migrate`, `tracker`, `verify-fix`)
  and two thin forwarders that bypass the main parser:
  `artifact-store` → `artifact_store.main`, `agent-queue-runner` →
  `agent.runner.main`.
- `compose.py` — facade/orchestrator for the full compose pipeline
  (delta + FreeBSD upstream + optional lock tree → composed output).
- `compose_stages.py` — stage implementations (preflight, seed,
  apply-special, semantic, system-replacements, fallback, prune-stale,
  finalize).
- `compose_discovery.py` — overlay discovery, target normalization
  (`@main` / `@YYYYQ[1-4]`), port-origin enumeration.
- `compose_patching.py` — non-interactive `patch(1)` driver and
  treetop identity-file helpers.
- `compose_reporting.py` — JSON-report → human overview formatting.
- `compose_models.py` — `ComposeStageResult`, `ComposePortReport`,
  `ComposeResult`, `ComposePortContext` dataclasses.
- `compat.py` — legacy compatibility-merge path (still used for
  non-`dops` plan types during migration).
- `policy.py` — compose constants (`EXCLUDED_TOP_LEVEL`,
  `PATCH_TIMEOUT_SECONDS`, `SPECIAL_COMPONENTS`,
  `TREETOP_IDENTITY_RULES`).
- `system_replacements.py` — global regex rewrite rules applied to
  composed output (e.g. amd64 → x86_64 option names).
- `plan_types.py` — materializes a port's runtime root by plan type
  (`dops`, `lock`, `compat`, etc.).
- `fsutils.py` — content-aware `dircmp` + `copy_tree` (preserves
  unchanged-content mtimes during reconcile).
- `artifact_store.py` — HTTP service that is the **single writer** for
  `state.db` plus bundle blobs and full logs. Schema in `db/schema.py`.
- `verify_fix.py` — operator command (not in the agent loop) that
  glues `dev-env apply-and-build` to
  `POST /api/bundles/<id>/verification`.

## Subpackages

### `engine/` — the DSL
- `lexer.py`, `parser.py`, `ast.py` — `overlay.dops` → AST.
- `semantic.py` — semantic analysis / diagnostics.
- `planner.py` — AST → normalized plan (`compile_plan`).
- `apply.py` + `apply_common.py` + `executors/` (`file_text_patch.py`,
  `mk_ops.py`) — plan → filesystem mutations.
- `fsops.py` — `FileTransaction` for atomic-per-file writes.
- `makefile_cst.py`, `makefile_rewrite.py` — BSD Makefile CST parser
  and deterministic rewrite/query primitives (used by mk-op executors).
- `oracle.py` — constrained `bmake` post-rewrite validation
  (`off` / `local` / `ci` profiles).
- `models.py` — `Diagnostic`, `Token`, `SourceSpan`, `LexResult`,
  `ApplyContext`, etc.
- `api.py` — public facade: `parse_dsl`, `check_dsl`, `build_plan`,
  `apply_dsl`.

### `agent/` — agentic build-failure repair loop
On import, installs a `tokenizers` stub for DragonFly (broken
`py311-tokenizers.so`) so `litellm` imports cleanly.

- `runner.py` — agent-queue-runner main: claims dsynth failure jobs and
  drives them through the triage/patch flow.
- `worker.py` — host-side filesystem ops + chroot `dev-env exec` —
  these are the **tool bodies** the LLM ultimately invokes.
- `tools.py` — OpenAI-style tool registry (name → fn + JSON schema)
  consumed by `tool_loop`.
- `tool_loop.py` — multi-turn LLM-with-tools driver (one conversation).
- `attempt_loop.py` — budget-bounded retry loop around `tool_loop`
  (each attempt starts fresh from `[system, user]`; failure context
  appended between attempts).
- `triage.py` — single-turn LLM call with iterative snippet rounds.
- `patch.py` — thin wrapper around `attempt_loop` for the patch flow.
- `llm.py` — `litellm` wrapper with normalized response shape.
- `prompts.py` — system prompts (loop scaffolding, tool surface).
- `playbooks.py` — parses `docs/agent-playbooks/*.md` (YAML frontmatter)
  and renders contextually selected entries into the payload. Replaces
  the legacy `load_kedb`.
- `context.py` — composable payload assembly (replaces hard-coded
  `parts.append(...)` walls).
- `decision.py` — policy decisions: tier resolution + per-(target,
  origin) retry cap.
- `policy.py` — loads `config/agentic-policy.json`; maps
  (classification, confidence) → tier + budget.
- `health.py` — structured `EnvHealth` probe of a dev-env chroot
  (replaces stderr-sniffing).
- `env_resolver.py` — single precedence rule for per-job dev-env
  selection.
- `lifecycle.py` — typed job state machine; each transition writes one
  `job_events` row under a `BEGIN IMMEDIATE` transaction.
- `step.py`, `steps.py` — `Step` protocol + concrete steps the
  orchestrator drives.
- `phase_result.py` — typed `analysis/<phase>_result.json` artifacts
  shared between phases (replaces regex-fishing markdown).
- `overlay_state.py` — facts → rules → assessment for dops conversion
  decisions (substrate-agnostic).
- `dops.py` — port-scoped overlay-artifact classification (uses
  `compose_discovery` logic, not the older inventory shape).
- `markdown.py` — section extraction shared by delivery + runner.
- `snippets.py` — wrapper around `scripts/snippet-extractor`.
- `proposed_fix.py` — writes `analysis/proposed_fix.md` on patch
  success.
- `manual_handoff.py` — writes `analysis/manual_handoff.md` when a job
  escalates (MANUAL tier, retry cap, budget exhaustion, gave-up).
- `session_dump.py` — optional gzipped-JSONL dump of LLM
  conversations (gated by `DP_HARNESS_DUMP_SESSION`).
- `edit_intent/` — (deleted per Step 42 — patch agent now overlays
  `overlay.dops` free-hand; this dir may still exist as a stub).

### `tracker/` — read+write consumer of `state.db`
- `server.py` — FastAPI app factory.
- `db.py` — SQLite helpers (WAL, single-writer-at-a-time).
- `agentic_queries.py` — SQL for the bundles/jobs/activity/events
  read endpoints consumed by operators and the analyzer subagent.
- `progress_adapter.py` — projects tracker rows into the
  `dsynth-progress` UI's `summary.json` + `<NN>_history.json` shape.
- `client.py` — HTTP client helpers (`start_build`, `record_result`,
  etc.) used by build hooks.
- `models.py` — pydantic request/response models.
- `static/`, `templates/` — UI assets.

### `delivery/` — operator-triggered upstream delivery (Step 11d)
Runs **after** a bundle is verified + accepted; never invoked from
inside the agent loop.

- `__init__.py` — defines `ReviewProvider` protocol,
  `ReviewRequestResult`, and the `DeliveryError` hierarchy
  (`Auth`, `RateLimit`, `Conflict`, `Config`).
- `orchestrator.py` — entry point invoked from the Accept endpoint.
- `config.py` — loads `config/delivery.toml`.
- `local_patch.py` — no-network outbox provider (default).
- `github.py` — full GitHub PR provider (Step 11d-3).
- `_git.py` — subprocess git driver for clone operations.
- `_http.py` — shared REST wrapper (token injection, retries) for
  network providers.

### `migration/` — compose-first rollout program
- `inventory.py` — scans `ports/` into raw migration records.
- `classify.py` — buckets records by migration class.
- `convert.py` — single-origin legacy→`overlay.dops` translator
  (deterministic path; derives `type` from STATUS — `dport`/`mask`
  render header-only, `lock` is left for manual). The LLM tail was
  offline tooling (`mass_convert.py` + the `dops-convert` skill), not a
  runtime agent.
- `batch.py` — wave-scoped batch driver around `convert_record`.
- `policy.py` — forward-migration policy evaluator (`policy-check`).
- `progress.py` — completion-threshold evaluator.
- `dashboard.py` — combined policy+progress dashboard for CI.
- `waves.py` — wave selection + result evaluation.
- `touched.py` — `changed file paths → touched origin set`.
- `models.py` — `MigrationWaveRecord` + adapters.

### `commands/` — argparse handlers (called from `cli.main`)
- `compose.py` (`cmd_compose`) — compose pipeline.
- `compose_report.py` (`cmd_compose_report`) — formats a compose JSON
  report.
- `dsl.py` (`cmd_dsl`) — `parse` / `check` / `plan` / `apply`.
- `migrate.py` (`cmd_migrate`) — all migration actions.
- `tracker.py` (`cmd_tracker`) — start/finish/record-result/enqueue +
  status + `get-bundle` / `list-bundles` / `get-job` / `list-jobs` /
  `get-activity` / `fetch-artifact` reads.

### `common/` — cross-cutting helpers
- `io.py` — JSON/TOML read/write, `emit_json`.
- `validation.py` — target patterns (`@main`, `@YYYYQ[1-4]`, `@any`),
  scoped-target / on-missing normalization.
- `text.py` — `safe_read_text`.
- `metrics.py` — `count_by` and similar small aggregations.

### `db/` — shared schema
- `schema.py` — single source of truth for `state.db` (artifact-store
  writer, tracker reader). Includes the folded-in tracker tables
  (`build_types`, `build_runs`, `build_results`, `port_status`).

## Data flow at a glance

```
overlay.dops ──► engine (lex/parse/sema/plan) ──► apply ──► port tree
                                                          │
freebsd upstream (+ lock) ──────────────────────► compose pipeline ──► output tree

dsynth build failure ──► artifact-store (state.db, single writer)
                              │
                              ├─► tracker (read+write, HTTP API + UI)
                              └─► agent.runner (claims jobs)
                                       │
                                       ├─► triage  ──► triage_result.json
                                       │      (bootstrap overlay.dops, or abort to manual)
                                       └─► patch   ──► proposed_fix.md
                                                          │
                                       operator Accept ──► delivery.orchestrator
                                                              │
                                                              └─► local_patch / github
```

## Key invariants

- **One writer for `state.db`**: `artifact_store.py`. Tracker reads +
  writes via that file under WAL; schema in `db/schema.py`.
- **Triage bootstraps the dops substrate or aborts** — at a failure with
  no `overlay.dops`, the runner (`_ensure_overlay_or_abort` →
  `overlay_state.bootstrap_decision`) deterministically writes a header
  overlay (`type port`/`dport` per STATUS) so patch can author the body,
  or aborts to manual when non-dport compat artifacts are present. There
  is no runtime convert agent (Step 48 cutover deleted it).
- **Patch agent edits `overlay.dops` directly** (Step 42 deleted the
  edit-intent layer).
- **Each attempt is a fresh `[system, user]` conversation** — prior
  attempt history is not chained into the next attempt; only a short
  failure-context message is appended.
- **Delivery is operator-triggered**, not part of the agent loop.
