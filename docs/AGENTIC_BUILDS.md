# AI-assisted workflow for fixing DeltaPorts (dsynth-based)

This document describes a practical, low-friction workflow for using AI agents to assist in fixing DragonFlyBSD DeltaPorts ports, without changing the core “build-driven iteration” process.

The key idea is **evidence-first, bounded-context assistance**:

- `dsynth` remains authoritative for builds.
- Agents only see **small, distilled artifacts** by default.
- Large logs and large upstream codebases stay local unless explicitly sliced.

This workflow is not meant to be a CI bot or autonomous fixer. It is a disciplined augmentation for a human porter.

## Background / Context

DeltaPorts is an overlay/fork of the FreeBSD ports tree used to generate DragonFlyBSD DPorts.

The typical port-fixing loop is:

1. Identify failed ports from staged/bulk builds.
2. Prioritize by impact (reverse dependencies).
3. Rebuild with `dsynth` until failure.
4. Inspect very large logs to find the real failure signal.
5. Create DeltaPorts-style patches.
6. Rebuild until success.
7. Commit fixes and handle backports later.

## Problems we are addressing

### 1) Build iteration cost
Some ports have long build times due to large dependency graphs. Iteration is slow when a fix requires multiple rebuilds.

### 2) Huge logs
`dsynth` logs can be hundreds of MB. Manually finding the true failure signal is expensive, and feeding full logs into an AI context is not feasible.

### 3) Massive upstream codebases
Many ports build large upstream projects (C/C++, Rust, Python, etc.). Sending whole trees into AI context is both slow and error-prone.

### 4) Preserving debug evidence without bypassing dsynth
Bypassing `dsynth` to keep workdirs/logs can lead to state conflicts (packages, chroot state). We want to keep dsynth as the executor while still preserving enough evidence to debug efficiently.

## Design principles

1. **Builds remain authoritative**
   - `dsynth` output is the source of truth. Agents do not “guess” without build evidence.

2. **Evidence-first, bounded context**
   - Agents see `errors.txt` (distilled) and small port metadata by default.
   - Full logs and source trees are only accessed via explicit, size-capped snippet requests.

3. **Hooks instead of manual hacks**
   - dsynth hooks are the primary integration point for evidence capture.

4. **Small agents with narrow roles**
   - Triage, patch authoring, and review are separated so that each agent’s job is explicit and bounded.

## Workflow overview

### Phase A — Candidate selection (optional)
Input: staged build UI data or dsynth summaries.

Goal: choose what to fix next, prioritizing by impact.

Typical ranking heuristics:

- Ports with many reverse dependencies first.
- Ports that block large frameworks first (e.g., toolchain, Python/Rust ecosystem pieces).
- Prefer “easy wins” only when they unblock a lot.

Implementation options:

- Parse `dsynth` summary lists (`failure_list.log`, etc.) from `Directory_logs`.
- Compute reverse dependencies using INDEX data (e.g., `INDEX-*`) or pkg repo metadata.

### Phase B — Failure capture (via dsynth hooks)
On build failure, hooks automatically generate a **run artifact bundle** containing:

- A compressed copy of the full per-port log.
- A distilled, bounded error extract (`errors.txt`).
- Snapshot of port metadata (Makefile, distinfo, plist, existing patches).
- dsynth profile/config snapshot.

This means you do not need to bypass `dsynth` just to preserve failure evidence.

### Phase C — Triage agent
Inputs:

- `errors.txt` (bounded, high-signal)
- port context snapshot (Makefile/distinfo/pkg-plist/patches)

Outputs:

- failure classification
- the exact log lines supporting the diagnosis
- a patch plan (DeltaPorts style)
- a list of precise snippet requests (only if needed)

### Phase D — Snippet extraction (non-AI)
When the triage agent requests more context, extract only what’s needed:

- failing source file `path:line ±N`
- nearest build system file (`CMakeLists.txt`, `meson.build`, `configure.ac`, etc.)
- cap output size (e.g., 50–200KB total)

Prefer extracting from:

- compiler diagnostics already present in the log (often includes the relevant line)
- targeted source slices, not full trees

### Phase E — Patch author agent
Inputs:

- patch plan
- port context
- bounded snippets

Outputs:

- DeltaPorts-style diffs (`files/patch-*`, Makefile/plist changes when required)
- short explanation tied to evidence

### Phase F — Reviewer agent (optional)
Checks for:

- ports framework conventions
- accidental dependency/option drift
- obvious mistakes
- whether a fix is DF-only vs upstreamable

### Phase G — Rebuild loop
- Rebuild the target port with `dsynth` (cached dependencies).
- Iterate until success.
- Only do clean rebuilds / broader reverse-dep rebuilds when warranted.

## Evidence bundles (contract)

### What agents see by default
The default agent input should be:

- `logs/errors.txt` (distilled, bounded)
- `port/Makefile`, `port/distinfo`, `port/pkg-plist`, `port/files/patch-*` (when present)
- `meta.txt` and `logfile.txt` (build metadata)

### Size limits
The distiller is intentionally conservative:

- `logs/errors.txt` is capped at **200KB**.
- full log is stored as `logs/full.log.gz` for humans.

### Naming and layout
Evidence bundles are written under:

- `${Directory_logs}/evidence/`

Run grouping:

- `${Directory_logs}/evidence/runs/<run-id>/...`

Per-failure bundle:

- `${run_dir}/ports/<origin>[@<flavor>]-<timestamp>/...`

## dsynth hook implementation (in this repo)

This repository includes a ready-to-install set of dsynth hooks under:

- `scripts/dsynth-hooks/`

### Which hooks are implemented
- `scripts/dsynth-hooks/hook_run_start` — creates a per-run directory and records run metadata.
- `scripts/dsynth-hooks/hook_run_end` — records end-of-run counts and copies dsynth summary lists.
- `scripts/dsynth-hooks/hook_pkg_failure` — generates an evidence bundle for failed ports (log distillation + port context snapshot).
- `scripts/dsynth-hooks/hook_pkg_success` — currently a no-op stub (kept lightweight).
- `scripts/dsynth-hooks/hook_pkg_skipped` — currently a no-op stub.
- `scripts/dsynth-hooks/hook_pkg_ignored` — currently a no-op stub.
- `scripts/dsynth-hooks/hook_common.sh` — shared helpers (PATH setup, log path reconstruction, distillation, truncation).

### What dsynth provides to hooks
dsynth executes hooks with a minimal environment and no argv arguments.

The hook environment includes (not exhaustive):

- `PROFILE`
- `DIR_LOGS`, `DIR_PORTS`, `DIR_BUILDBASE`, `DIR_PACKAGES`, `DIR_REPOSITORY`, `DIR_OPTIONS`, `DIR_DISTFILES`
- Per-port hooks: `RESULT`, `ORIGIN`, `FLAVOR`, `PKGNAME`

Important detail: dsynth sets `FLAVOR=$ORIGIN` when there is no flavor. The hook helper treats `FLAVOR` as “real” only when it differs from `ORIGIN`.

### Log distillation
`hook_pkg_failure` reconstructs dsynth’s per-port log filename from `ORIGIN` and `FLAVOR` and writes:

- `logs/errors.txt` consisting of:
  - first “error candidates” matches (bounded)
  - contextual error blocks (`rg -C 2`)
  - final tail of the log (`tail -n 200`)

Then it compresses the full log:

- `logs/full.log.gz`

### Port context snapshot
The failure hook copies a bounded set of port metadata into `port/`:

- `Makefile`, `Makefile.local` (if present)
- `distinfo`
- `pkg-plist`
- `pkg-descr`, `pkg-message` (if present)
- `files/patch-*` (if present)

### Installing the hooks
dsynth looks for executable hook scripts in its configuration base:

- `/etc/dsynth/` or
- `/usr/local/etc/dsynth/`

To enable these hooks, copy or symlink the scripts into that directory with the exact filenames:

- `hook_run_start`
- `hook_run_end`
- `hook_pkg_failure`
- `hook_pkg_success`
- `hook_pkg_skipped`
- `hook_pkg_ignored`

Ensure they are executable.

Example (adjust destination as needed):

- `cp scripts/dsynth-hooks/hook_* /etc/dsynth/`
- `cp scripts/dsynth-hooks/hook_common.sh /etc/dsynth/`
- `chmod +x /etc/dsynth/hook_* /etc/dsynth/hook_common.sh`

## How to use this in practice (human loop)

1. Run `dsynth` normally (bulk build or targeted build).
2. When a port fails, open the newest evidence bundle under `${Directory_logs}/evidence/runs/.../ports/.../`.
3. Feed only `logs/errors.txt` + `port/*` into your triage agent.
4. If needed, perform a targeted snippet extraction and provide only those slices.
5. Apply the patch, rebuild the port in dsynth, iterate.

## Integration plan (opencode serve)

This section captures the concrete next steps for wiring the evidence bundles produced by dsynth into AI agents via `opencode serve`.

Assumptions (current):

- `opencode serve` runs on a separate host from the dsynth builder.
- The dsynth builder can reach the opencode server over LAN HTTP.
- The integration posts **bounded payloads** (not full logs, not full source trees).
- Hook stdout is not reliable; agent outputs must be written to the evidence bundle.

### Step-by-step

1. **Run and secure the server**
   - Start: `opencode serve --hostname <LAN-IP> --port 4096`.
   - Because the server API is unauthenticated by default, restrict access via firewall to the dsynth builder(s) only.

2. **Smoke-test connectivity from the builder**
   - `GET http://<opencode-host>:4096/global/health`
   - `POST http://<opencode-host>:4096/session` to create a session.
   - `POST http://<opencode-host>:4096/session/<id>/message` with `parts: [{"type":"text","text":"..."}]`.

3. **Create dedicated agents on the opencode host (recommended)**
   - Define agents such as `dports-triage`, `dports-patch`, and `dports-review`.
   - Disable tools (no file reads/writes/shell) so requests cannot stall on permissions and cannot explode context.
   - The builder then sets `agent: "dports-triage"` (etc.) in API requests.

4. **Define evidence → payload mapping and caps**
   - Payload should include only:
     - distilled `logs/errors.txt`
     - selected `port/*` context (Makefile/distinfo/pkg-plist/patches)
     - `meta.txt` (origin/profile/result)
   - Do not send `logs/full.log.gz` unless explicitly requested later.

5. **Avoid blocking dsynth hooks on network calls (use a central queue)**
   - Hooks can run concurrently (many failures in a bulk run), so they should stay fast and purely local.
   - Recommended architecture:
     - `hook_pkg_failure` writes the evidence bundle (already implemented).
     - `hook_pkg_failure` also **enqueues one job per failure instance** into a central spool under `${Directory_logs}/evidence/queue/`.
     - A separate job runner (cron/daemon/manual) drains the queue and POSTs bounded payloads to `opencode serve`.
   - Suggested central spool layout (crash-safe and easy to inspect):
     - `${Directory_logs}/evidence/queue/pending/` — newly enqueued jobs
     - `${Directory_logs}/evidence/queue/inflight/` — jobs claimed by a runner (atomic rename)
     - `${Directory_logs}/evidence/queue/done/` — successfully processed jobs (optional, for auditing)
     - `${Directory_logs}/evidence/queue/failed/` — jobs that exceeded retry policy (optional)
    - Job semantics:
      - **No dedupe by port**: every failure bundle becomes its own job, because later failures can represent progress after partial fixes.
      - Each job points at exactly one evidence bundle directory (e.g. `${Directory_logs}/evidence/runs/<run-id>/ports/<origin>[@<flavor>]-<timestamp>/`).
      - The runner writes outputs back into that bundle (e.g. `analysis/triage.md`) and then marks the job done.
    - Job file format (implementation contract):
      - One job file per failure bundle, written as plain `key=value` lines (similar to `meta.txt`).
      - Minimum keys:
        - `created_ts_utc=...`
        - `profile=...`
        - `origin=...`
        - `flavor=...`
        - `bundle_dir=/absolute/path/to/.../ports/<origin>...-<timestamp>/`
        - `run_id=...`
      - Recommended filename scheme (sortable, unique):
        - `YYYYmmdd-HHMMSSZ-<profile>-<origin>[@<flavor>]-<pid>.job` (with filename-safe sanitization).
    - Atomicity / lifecycle:
      - Enqueue by writing a temp file in the same filesystem and `mv` into `.../queue/pending/`.
      - Claim by atomic rename: `pending/` → `inflight/`.
      - On success: `inflight/` → `done/` (or delete if you do not want auditing).
      - On permanent failure: `inflight/` → `failed/` and write a short error note alongside.
    - Runner behavior (single-worker):
      - One runner process drains the queue (cron/daemon/manual).
      - Recommended runner script: `scripts/agent-queue-runner --queue-root ${Directory_logs}/evidence/queue`.
      - Session-per-job is acceptable (sync): create session → send bounded evidence → write `analysis/triage.md` + `analysis/triage.json` → mark job done.
      - Runner configuration may use env vars like `OPENCODE_URL`, `OPENCODE_AGENT`, and `OPENCODE_TIMEOUT`.
      - Implementation may use `curl` and `python3` for HTTP + JSON.

6. **Write agent outputs back into the evidence bundle**

   - Store artifacts next to the evidence, e.g.:
     - `analysis/triage.md`
     - `analysis/triage.json` (raw response payload)
     - `analysis/patch.diff`
     - `analysis/review.md`
     - `analysis/snippet_requests.json`


7. **Snippet extraction escalation (non-AI, size-capped)**
   - Only if triage requests more context, extract the smallest relevant source/config snippets and append them to a follow-up request.
   - Keep strict caps so the patch-author agent stays accurate.

8. **Dry-run the full loop on one known failure**
   - dsynth failure → evidence bundle → triage output → (optional snippet extraction) → patch diff output → rebuild.

### Observability / UI (State Server)

To support a remote UI without requiring filesystem access to the builder, add a small **State Server** that acts as the UI’s single source of truth.

Key ideas:

- The State Server runs on the dsynth builder and **observes** `${Directory_logs}/evidence/` and `${Directory_logs}/evidence/queue/`.
- It is **read/observe-only**: it does not process jobs, does not talk to `opencode serve`, and does not run builds.
- It normalizes observed state into a durable store (recommended: SQLite) so the UI can show full history.
- It exposes a UI-friendly HTTP API plus a live event stream for progress updates.

What it can visualize:

- Queue progress: how many jobs are in `pending/`, `inflight/`, `done/`, `failed/`.
- Per-port iteration: multiple evidence bundles over time for the same `origin[@flavor]`.
- Agent progress: whether `analysis/triage.md` / `analysis/triage.json` exists yet for a bundle.
- Build cycle: run start/end + dsynth summaries from `runs/<run-id>/`.

Suggested HTTP interface (example):

- `GET /status` — aggregate counts and current inflight job (if any)
- `GET /jobs?state=pending|inflight|done|failed` — list jobs
- `GET /jobs/<job_id>` — job details + linked bundle
- `GET /runs` and `GET /runs/<run_id>` — build run overview
- `GET /ports/<origin>[@<flavor>]` — timeline of attempts/bundles
- `GET /bundles/<bundle_id>/artifacts/<name>` — serve evidence artifacts, including `logs/full.log.gz`

Live progress:

- `GET /events` (SSE) — stream events such as:
  - `run_started`, `run_ended`
  - `bundle_created`
  - `job_enqueued`, `job_claimed`, `job_done`, `job_failed`
  - `triage_written`

Ingestion strategy:

- Start with a periodic reconcile loop (e.g. poll every 1s) and emit events when new/changed items are detected.
- Optionally later, allow local push notifications from the runner for lower latency (but keep the observer-only design).

### Implementation plan (phased)

This section turns the workflow above into an incremental, testable buildout. Each phase has concrete tasks and a “done when” gate.

#### Phase 1 — Evidence capture (DONE)

Goal: dsynth failures reliably produce bounded evidence bundles.

Tasks:

- [x] Install the dsynth hooks from `scripts/dsynth-hooks/` into dsynth's config base.
- [x] Verify that failures generate bundles with `meta.txt`, `logs/errors.txt` (capped), `logs/full.log.gz`, and `port/*` context.

Done when:

- [x] A known failing port produces an evidence bundle under `${Directory_logs}/evidence/runs/.../ports/.../` with the expected files.

Validated 2026-01-10 on DragonFlyBSD VM with `devel/gettext-tools` failure. Bundle structure:

```
runs/run-LiveSystem-20260110-180454Z-1291/
├── dsynth.ini
├── LiveSystem-make.conf
├── run_start.txt
├── run_end.txt
├── 00_last_results.log
└── ports/
    └── devel_gettext-tools-20260110-181433Z/
        ├── meta.txt
        ├── logfile.txt
        ├── logs/
        │   ├── errors.txt
        │   └── full.log.gz
        └── port/
            ├── Makefile
            ├── pkg-descr
            └── pkg-plist
```

#### Phase 2 — Central queue enqueue (DONE)

Goal: every failure instance becomes a queue job; hooks remain non-blocking.

Tasks:

- [x] Update `scripts/dsynth-hooks/hook_common.sh` to add:
  - `queue_root()` → `${Directory_logs}/evidence/queue/`
  - `ensure_queue_dirs()` → create `pending/`, `inflight/`, `done/`, `failed/`
  - `enqueue_job(...)` → write a `key=value` job file and atomically enqueue it (temp + `mv`)
- [x] Update `scripts/dsynth-hooks/hook_run_start` to call `ensure_queue_dirs`.
- [x] Update `scripts/dsynth-hooks/hook_pkg_failure` to enqueue one job per failure instance after writing the evidence bundle.

Done when:

- [x] Each new failure bundle produces exactly one `.job` in `${Directory_logs}/evidence/queue/pending/`.

Validated 2026-01-10 on DragonFlyBSD VM. Queue structure after failure:

```
queue/
├── pending/
│   └── 20260110-185415Z-LiveSystem-devel_gettext-tools-88632.job
├── inflight/
├── done/
└── failed/
```

Job file contents:
```
created_ts_utc=20260110-185415Z
profile=LiveSystem
origin=devel/gettext-tools
flavor=devel/gettext-tools
bundle_dir=/build/synth/logs/evidence/runs/run-LiveSystem-20260110-185412Z-88533/ports/devel_gettext-tools-20260110-185415Z
run_id=run-LiveSystem-20260110-185412Z-88533
```

#### Phase 3 — Worker: triage jobs (runner-side) (DONE)

Goal: asynchronously triage failures via `opencode serve` and write outputs back into the bundle.

Tasks:

- [x] Add `scripts/agent-queue-runner` (Python3, single worker).
- [x] Runner interface:
  - `scripts/agent-queue-runner --queue-root <path>`
  - Optional flags: `--once` (process one job and exit), `--dry-run` (print payload, don't call opencode)
- [x] Configuration via env vars:
  | Var | Required | Default | Description |
  |-----|----------|---------|-------------|
  | `OPENCODE_URL` | yes | — | e.g., `http://192.168.1.10:4096` |
  | `OPENCODE_PROVIDER` | no | `opencode` (when using `dports-triage` agent) | e.g., `opencode` |
  | `OPENCODE_MODEL` | no | `gpt-5-nano` (when using `dports-triage` agent) | e.g., `gpt-5-nano` |
  | `OPENCODE_AGENT` | no | `dports-triage` | Custom agent name |
  | `OPENCODE_TIMEOUT` | no | 120 | Request timeout (seconds) |
  | `OPENCODE_MAX_RETRIES` | no | 3 | Retry attempts |
  | `OPENCODE_RETRY_DELAY` | no | 8 | Base delay between retries (seconds) |
- [x] Implement job lifecycle:
  - Claim by atomic rename: `pending/` → `inflight/`
  - Validate `bundle_dir` exists, else fail immediately
  - On success: `inflight/` → `done/`
  - On permanent failure: `inflight/` → `failed/` (+ `<job>.error` file)
- [x] Implement retry logic:
  - Retry up to `OPENCODE_MAX_RETRIES` times on API errors
  - Exponential backoff: `delay * 2^attempt` (capped at 60s)
- [x] Implement synchronous opencode calls (session-per-job):
  - `POST /session` → get session ID
  - `POST /session/<id>/message` → send payload with `agent` parameter
- [x] Write outputs into `bundle_dir/analysis/`:
  - `analysis/triage.md` (response text)
  - `analysis/triage.json` (full API response)
  - `analysis/session_id.txt`
- [x] Logging:
  - Write to `<queue-root>/runner.log`
  - Also print to stderr for immediate visibility
- [x] Behavior:
  - Normal mode: loop forever, sleep 5s when queue is empty
  - `--once` mode: process one job (if any) and exit
- [x] Configure `dports-triage` agent on opencode server (see Agent Configuration below)

Done when:

- [x] A pending job becomes `done/` and the referenced bundle contains `analysis/triage.md` + `analysis/triage.json`.
- [x] Triage output follows the structured format (Classification, Platform, Root Cause, Evidence, Suggested Fix, Confidence, Notes).

Validated 2026-01-11 on DragonFlyBSD VM. Runner output:

```
2026-01-11T00:23:40Z INFO  starting runner (once=True, dry_run=False, agent=dports-triage, model=opencode/gpt-5-nano)
2026-01-11T00:23:40Z INFO  processing job 20260111-002334Z-LiveSystem-devel_gettext-tools-89907.job
2026-01-11T00:23:40Z INFO  calling opencode (attempt 1/3, agent=dports-triage, model=opencode/gpt-5-nano)
2026-01-11T00:23:58Z INFO  wrote analysis to .../analysis/
2026-01-11T00:23:58Z INFO  moved job to done/
```

Example triage output (`analysis/triage.md`):
```markdown
## Classification
missing-dep

## Platform
dragonfly-specific

## Root Cause
The gettext-tools build cannot proceed because the build environment cannot fetch
the required prebuilt libtextstyle package (libtextstyle-0.22.5.pkg) from the local
package repository.

## Evidence
- "pkg: No packages available to install matching '/packages/All/libtextstyle-0.22.5.pkg'
  have been found in the repositories"

## Suggested Fix
- Publish the required libtextstyle package to the DeltaPorts DragonFly package repo...

## Confidence
medium

## Notes
- This is a dependency delivery issue rather than a code/config error in gettext-tools itself.
```

Free models available on `opencode` provider (no billing required):
- `gpt-5-nano`, `big-pickle`, `grok-code`, `glm-4.7-free`, `minimax-m2.1-free`

Example invocation:
```sh
OPENCODE_URL=http://10.0.2.2:4097 \
/build/synth/DeltaPorts/scripts/agent-queue-runner \
  --queue-root /build/synth/logs/evidence/queue --once
```

##### Agent Configuration

The `dports-triage` agent must be configured on the opencode server. Add the following to `opencode.json`:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "agent": {
    "dports-triage": {
      "description": "Triages dsynth build failures for DragonFlyBSD DeltaPorts",
      "mode": "subagent",
      "model": "opencode/gpt-5-nano",
      "tools": {
        "write": false, "edit": false, "bash": false,
        "read": false, "glob": false, "grep": false,
        "webfetch": false, "task": false
      },
      "prompt": "<system prompt - see below>"
    }
  }
}
```

The agent's system prompt includes:
- DeltaPorts/DPorts context (what they are, how they relate)
- dsynth build system overview
- Common DragonFlyBSD-specific issues (missing syscalls, pthread in libc, procfs differences, etc.)
- DeltaPorts patching style conventions
- **Required output format** (structured sections)

**Important:** The agent must be passed in the **message body** (not session creation) when calling the opencode API. The runner handles this automatically.

##### Triage Output Format

The agent is instructed to produce output in this exact structure:

| Section | Values | Description |
|---------|--------|-------------|
| `## Classification` | `compile-error`, `configure-error`, `missing-dep`, `plist-error`, `fetch-error`, `patch-error`, `unknown` | Type of failure |
| `## Platform` | `dragonfly-specific`, `freebsd-upstream`, `generic` | Where the issue originates |
| `## Root Cause` | free text | 1-3 sentences explaining the cause |
| `## Evidence` | quoted log lines | Direct quotes supporting the diagnosis |
| `## Suggested Fix` | free text | DeltaPorts-style fix approach |
| `## Confidence` | `high`, `medium`, `low` | Confidence in the diagnosis |
| `## Notes` | free text (optional) | Additional context or caveats |

##### Known Error Database (KEDB)

To improve triage accuracy, the agent can be augmented with a database of known issues. Place markdown files in:

```
docs/kedb/*.md
```

These files will be included in the agent's context (injected into the payload by the runner). Suggested format:

```markdown
# Known Issue: pthread linking errors

## Pattern
- `undefined reference to 'pthread_create'`
- `undefined reference to 'pthread_mutex_*'`

## Cause
DragonFlyBSD has pthread integrated into libc, unlike FreeBSD which uses a separate libpthread.
Ports that explicitly link `-lpthread` or check for `libpthread.so` may fail.

## Fix
- Remove explicit `-lpthread` from LDFLAGS if present
- Or add `LDFLAGS+=-lpthread` to satisfy linker (harmless on DragonFly)
- For configure scripts: patch to skip libpthread checks on DragonFly

## Examples
- `devel/some-port`: Fixed in commit abc123
- `net/another-port`: Marked BROKEN_DragonFly
```

The runner will automatically read `docs/kedb/*.md` and append them to the triage payload.

#### Phase 4 — Patch generation (agent output) (DONE)

Goal: generate DeltaPorts overlay changes as a patch suitable for review and application.

Tasks:

- [x] Extend the job format to support `type=patch`.
- [x] Add a patch agent (`dports-patch`) that consumes bounded evidence + triage output.
- [x] Implement auto-enqueue: after triage completes, automatically enqueue a patch job for patchable classifications.
- [x] Store outputs into `bundle_dir/analysis/`:
  - `analysis/patch.diff` (unified diff that applies to DeltaPorts overlay root)
  - `analysis/patch.md` (full response including rationale)
  - `analysis/patch.json` (raw API response)
- [x] Validate diff output before writing `patch.diff` (write `patch.diff.invalid` on failure).

Done when:

- [x] For a known failure, a patch job produces a usable `analysis/patch.diff`.
- [x] Auto-enqueue works for patchable classifications with sufficient confidence.

##### Job Types

The runner now supports two job types:

| Type | Agent | Description |
|------|-------|-------------|
| `triage` (default) | `dports-triage` | Analyzes failure, produces structured triage report |
| `patch` | `dports-patch` | Generates unified diff from triage + evidence |

##### Auto-Enqueue Rules

After a triage job completes successfully, the runner automatically enqueues a patch job if:

1. **Classification** is one of:
   - `compile-error`
   - `configure-error`
   - `patch-error`
   - `plist-error`

2. **Confidence** is `high` or `medium`

Classifications that do NOT auto-enqueue:
- `missing-dep` (infrastructure issue, not patchable)
- `fetch-error` (upstream issue)
- `unknown` (needs investigation)

##### Patch Job File Format

```
type=patch
created_ts_utc=20260111-020000Z
profile=LiveSystem
origin=devel/gettext-tools
flavor=devel/gettext-tools
bundle_dir=/build/synth/logs/evidence/runs/.../ports/devel_gettext-tools-...
run_id=run-LiveSystem-...
triage_file=/build/synth/logs/evidence/.../analysis/triage.md
```

##### Patch Agent Configuration

The `dports-patch` agent must be configured on the opencode server. Key differences from triage agent:

- All tools disabled (relies on payload only)
- System prompt focuses on generating valid unified diffs
- Output format: `## Patch` with diff block, `## Rationale`, `## Files Modified`

##### Diff Validation

Before writing `patch.diff`, the runner validates:

1. Has `---` and `+++` file headers
2. Has at least one `@@ ... @@` hunk header
3. Lines in hunks have valid prefixes (`+`, `-`, ` `, or `\`)

If validation fails:
- `patch.diff.invalid` is written (with error note)
- Job is marked as failed
- No retry (likely a model output issue)

##### Example Workflow

```
1. dsynth failure → hook creates evidence bundle + triage job
2. Runner picks up triage job:
   - Calls dports-triage agent
   - Writes analysis/triage.md
   - Parses Classification=compile-error, Confidence=medium
   - Auto-enqueues patch job
3. Runner picks up patch job:
   - Calls dports-patch agent with triage + evidence
   - Extracts diff from response
   - Validates diff syntax
   - Writes analysis/patch.diff
4. Patch is ready for review/application
```

Example runner output:
```
2026-01-11T02:00:00Z INFO  processing job 20260111-020000Z-LiveSystem-devel_foo-12345.job
2026-01-11T02:00:00Z INFO  calling opencode (attempt 1/3, agent=dports-triage, model=opencode/gpt-5-nano)
2026-01-11T02:00:15Z INFO  wrote triage to .../analysis/
2026-01-11T02:00:15Z INFO  auto-enqueued patch job: 20260111-020015Z-...-patch.job (classification=compile-error, confidence=medium)
2026-01-11T02:00:15Z INFO  moved job to done/
2026-01-11T02:00:15Z INFO  processing job 20260111-020015Z-...-patch.job
2026-01-11T02:00:15Z INFO  calling opencode (attempt 1/3, agent=dports-patch, model=opencode/gpt-5-nano)
2026-01-11T02:00:30Z INFO  wrote patch.diff to .../analysis/
2026-01-11T02:00:30Z INFO  moved job to done/
```

Validated 2026-01-11 on DragonFlyBSD VM. Manual patch job processed successfully:

```
2026-01-11T10:56:17Z INFO  starting runner (once=True, dry_run=False, model=opencode/gpt-5-nano, kedb=none)
2026-01-11T10:56:17Z INFO  processing job test-patch-job.job
2026-01-11T10:56:17Z INFO  calling opencode (attempt 1/3, agent=dports-patch, model=opencode/gpt-5-nano)
2026-01-11T10:56:40Z INFO  wrote patch.diff to .../analysis/
2026-01-11T10:56:40Z INFO  moved job to done/
```

Generated patch applied `BROKEN_DragonFly` to the port Makefile with rationale explaining the missing dependency issue.

#### Phase 5 — Apply patch to DeltaPorts overlay, sync to DPorts, rebuild, open PR (DONE)

Goal: integrate with the existing staged build workflow while ensuring the system never pushes to master.

##### Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                         HOST MACHINE                                 │
│  ┌────────────────────────────────────────────────────────────────┐ │
│  │  scripts/apply-patch                                           │ │
│  │  - Reads patch.diff + triage.md from evidence bundle           │ │
│  │  - Operates on SAFE CLONE only (/home/.../DeltaPorts-ai-fix)   │ │
│  │  - Hard refuses if pointed at protected checkout               │ │
│  │  - Creates branch: ai-fix/<cat>-<port>-<bugslug>               │ │
│  │  - Applies patch, commits (no PR yet)                          │ │
│  │  - Pushes branch so VM can fetch                               │ │
│  └────────────────────────────────────────────────────────────────┘ │
│                              │ SSH                                   │
│                              ▼                                       │
│  ┌────────────────────────────────────────────────────────────────┐ │
│  │  VM (DragonFlyBSD)                                             │ │
│  │  - Fetches branch into /build/synth/DeltaPorts                 │ │
│  │  - Runs sync1.sh <cat/port> to update DPorts                   │ │
│  │  - Runs dsynth just-build <cat/port>                           │ │
│  │  - Writes results back to evidence bundle                      │ │
│  └────────────────────────────────────────────────────────────────┘ │
│                              │                                       │
│                              ▼                                       │
│  ┌────────────────────────────────────────────────────────────────┐ │
│  │  PR Creation (only if rebuild succeeds)                        │ │
│  │  - gh pr create from safe clone                                │ │
│  │  - Records PR URL in bundle                                    │ │
│  └────────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────┘
```

##### Safety Rules

- **Protected checkout**: `/home/antonioh/s/DeltaPorts` is never modified by automation.
- **Safe clone**: All apply/commit/push operations use `/home/antonioh/s/DeltaPorts-ai-fix`.
- **Never push to master**: Always create a new branch and open a PR.
- **Rebuild gate**: PR is only opened after dsynth rebuild succeeds on VM.

##### Patch Output Contract

The `dports-patch` agent must output diffs that modify only DeltaPorts overlay paths (per `README.md`):

| Path Pattern | Purpose |
|--------------|---------|
| `ports/<cat>/<port>/STATUS` | Port status (PORT/DPORT/MASK) |
| `ports/<cat>/<port>/Makefile.DragonFly` | DragonFly-specific Makefile additions (preferred) |
| `ports/<cat>/<port>/diffs/*.diff` | Patches to FreeBSD port files |
| `ports/<cat>/<port>/dragonfly/*` | Extra patches/files (applied after `files/`) |
| `ports/<cat>/<port>/newport/*` | Complete port created from scratch |

Paths like `DeltaPorts/...` or direct edits to FreeBSD ports files are rejected.

##### VM Prerequisites

The VM requires:

1. **FreeBSD ports checkout** at `/build/synth/freebsd-ports` (2025Q2 branch)
2. **Generator config** at `/usr/local/etc/dports.conf`:
   ```sh
   FPORTS=/build/synth/freebsd-ports
   DELTA=/build/synth/DeltaPorts
   DPORTS=/build/synth/DPorts
   MERGED=/build/synth/DPorts
   POTENTIAL=/build/synth/potential
   INDEX=/build/synth/freebsd-ports/INDEX-14
   ```
3. **Directories**: `MERGED`, `POTENTIAL` must exist

##### Helper Script: `scripts/apply-patch`

Interface:
```sh
scripts/apply-patch --bundle <path> [--dry-run] [--no-rebuild] [--no-pr]

# Environment variables:
BUNDLE_DIR=/path/to/evidence/bundle    # Required if --bundle not given
SAFE_DELTAPORTS_DIR=...                # Default: /home/antonioh/s/DeltaPorts-ai-fix
VM_SSH_KEY=...                         # Default: ~/.go-synth/vm/id_ed25519
VM_SSH_PORT=...                        # Default: 2222
VM_SSH_HOST=...                        # Default: root@localhost
```

Workflow:
1. Validate bundle contains `patch.diff` and `meta.txt`
2. Parse origin from `meta.txt`, classification/confidence from `triage.md`
3. Ensure safe clone exists and is up-to-date with origin/master
4. Create branch `ai-fix/<cat>-<port>-<bugslug>`
5. Apply `patch.diff` to safe clone
6. Commit with message including classification and evidence bundle path
7. Push branch to origin
8. SSH to VM: fetch branch, run `sync1.sh`, run `dsynth just-build`
9. If rebuild succeeds: `gh pr create` and record PR URL
10. Write artifacts to `bundle/analysis/`: `branch.txt`, `commit.txt`, `rebuild_status.txt`, `pr_url.txt`

##### Branch Naming

Format: `ai-fix/<category>-<port>-<bugslug>`

Examples:
- `ai-fix/devel-gettext-tools-missing-dep`
- `ai-fix/lang-rust-pthread-link`
- `ai-fix/www-nginx-configure-dragonfly`

The `<bugslug>` is derived from the triage classification and a sanitized keyword from the root cause.

##### PR Format

Title: `fix(<cat>/<port>): <brief description>`

Body:
```markdown
## Summary
AI-assisted fix for `<origin>` build failure on DragonFlyBSD.

## Triage Analysis
- **Classification**: <classification>
- **Confidence**: <confidence>
- **Root Cause**: <excerpt>

## Changes
<list of files modified>

## Rebuild Result
Build succeeded on DragonFlyBSD VM (dsynth just-build).

## Evidence
Bundle: `<bundle_dir>`

---
*This PR was generated by the agentic build system.*
```

##### Tasks

- [x] Document Phase 5 plan
- [x] Create `scripts/apply-patch` helper
- [x] Provision VM `/usr/local/etc/dports.conf` and required directories
- [x] Test sync1 + rebuild gate on VM
- [x] Test PR creation flow
- [x] Validate end-to-end with a real failing port
- [x] Update `hook_pkg_failure` to capture `Makefile.DragonFly`

Done when:

- [x] A patch can be applied to the safe clone, synced into the staged DPorts checkout on VM, rebuilt successfully, and a PR is opened with results tracked back into the evidence bundle.

Validated 2026-01-11 on DragonFlyBSD VM with `net/bsdrcmds` port:

1. **Failure captured**: Port failed with `-Werror` compiler warnings in `rshd.c`
2. **Triage**: Agent correctly classified as `compile-error` with `high` confidence
3. **Patch generated**: Agent created `Makefile.DragonFly` with CFLAGS to suppress warnings:
   ```makefile
   OPTIONS_DEFAULT:=	${OPTIONS_DEFAULT:NLIBBLACKLIST}
   CFLAGS+=	-Wno-misleading-indentation
   CFLAGS+=	-Wno-unused-const-variable
   ```
4. **Apply and sync**: `scripts/apply-patch` applied patch to safe clone, pushed branch
5. **VM rebuild**: `sync1.sh` merged changes, `dsynth force` built successfully
6. **PR created**: https://github.com/DragonFlyBSD/DeltaPorts/pull/1518

Example `apply-patch` output:
```
2026-01-11T16:46:30Z INFO  Bundle: /tmp/test-bundle
2026-01-11T16:46:30Z INFO  Origin: net/bsdrcmds
2026-01-11T16:46:30Z INFO  Classification: compile-error, Confidence: high
2026-01-11T16:46:30Z INFO  Branch: ai-fix/net-bsdrcmds-compile-error
2026-01-11T16:46:31Z INFO  Applying patch (231 bytes)
2026-01-11T16:46:31Z INFO  Patch output: patching file ports/net/bsdrcmds/Makefile.DragonFly
2026-01-11T16:46:32Z INFO  Commit: dac0d57213b...
2026-01-11T16:46:34Z INFO  Pushed to origin
```

VM rebuild output:
```
[000] SUCCESS net/bsdrcmds                                             00:00:03
    packages built: 1
           failed: 0
```

#### Phase 6 — Observability/UI (State Server, observe-only) (DONE)

Goal: provide a single API for a remote UI with live progress and full historical detail.

Tasks:

- [x] Implement a builder-side State Server that:
  - Observes `${Directory_logs}/evidence/` and `${Directory_logs}/evidence/queue/`.
  - Persists state/history to SQLite.
  - Provides a REST API (status/jobs/runs/ports) and SSE (`GET /events`).
  - Serves artifacts including `logs/full.log.gz`.
- [x] Keep it observe-only:
  - It does not drain jobs.
  - It does not call `opencode serve`.
  - It does not run builds.

Done when:

- [x] A remote UI can subscribe to `/events` and see queue/job progression live while also querying full history and artifacts.

##### Implementation: `scripts/state-server`

A Python 3 HTTP server (~700 lines) using only stdlib (http.server, sqlite3, json).

**Requirements:**
- Python 3.11+ with sqlite3 module
- On DragonFlyBSD: `pkg install py311-sqlite3`

**CLI:**
```sh
scripts/state-server [options]

Options:
  --logs-root PATH       Evidence root (default: /build/synth/logs if exists)
  --db-path PATH         SQLite database (default: <logs-root>/evidence/state.db)
  --bind ADDR            Bind address (default: 127.0.0.1)
  --port PORT            Port number (default: 8787)
  --poll-interval SECS   Filesystem poll interval (default: 1.0)
```

**Example:**
```sh
# On the VM:
/build/synth/DeltaPorts/scripts/state-server --logs-root /build/synth/logs

# Test endpoints:
curl http://127.0.0.1:8787/health
curl http://127.0.0.1:8787/status
curl http://127.0.0.1:8787/runs
curl http://127.0.0.1:8787/jobs
curl -N http://127.0.0.1:8787/events  # SSE stream
```

##### SQLite Schema

| Table | Purpose |
|-------|---------|
| `runs` | dsynth build runs (profile, timestamps, path) |
| `bundles` | Evidence bundles per failed port |
| `jobs` | Queue jobs with state tracking |
| `artifacts` | Files within bundles (relpath, kind, size) |
| `events` | Event log for SSE replay |

##### REST API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Health check (`{"ok": true}`) |
| `/status` | GET | Aggregate counts (runs, bundles, jobs by state) |
| `/runs` | GET | List all runs |
| `/runs/<run_id>` | GET | Single run with its bundles |
| `/jobs` | GET | List jobs (optional `?state=pending\|inflight\|done\|failed`) |
| `/jobs/<job_id>` | GET | Single job details |
| `/bundles` | GET | List recent bundles (up to 100) |
| `/bundles/<bundle_id>` | GET | Bundle metadata with artifact list |
| `/ports/<origin>` | GET | Timeline of bundles and jobs for a port |
| `/bundles/<bundle_id>/artifacts/<relpath>` | GET | Serve artifact file (with path traversal protection) |

##### SSE Events (`GET /events`)

Supports `Last-Event-ID` header for replay from a specific event.

| Event Type | Data |
|------------|------|
| `run_started` | `{run_id, profile, ts_start}` |
| `run_ended` | `{run_id, ts_end}` |
| `bundle_created` | `{bundle_id, run_id, origin, ts_utc}` |
| `job_enqueued` | `{job_id, state, origin, type}` |
| `job_claimed` | `{job_id, state, origin, type}` |
| `job_done` | `{job_id, state, origin, type}` |
| `job_failed` | `{job_id, state, origin, type}` |
| `triage_written` | `{bundle_id, artifact}` |
| `patch_written` | `{bundle_id, artifact}` |
| `pr_created` | `{bundle_id, pr_url}` |

##### Observer Design

- Reconciler thread polls filesystem every `--poll-interval` seconds
- Detects new/changed runs, bundles, jobs, artifacts
- Emits events on state changes
- All state persisted to SQLite for full history
- Keepalive comments sent every 15s on SSE connections

##### Design Decisions

- **Bind localhost by default**: Security via network topology, not auth
- **No authentication**: Intended for trusted LAN/localhost access
- **No retention/pruning**: History kept forever (SQLite scales well)
- **No CORS headers**: Add if/when cross-origin UI is needed

Validated 2026-01-11 on DragonFlyBSD VM:

```
$ curl http://127.0.0.1:8787/status
{
    "jobs": {"done": 19},
    "bundles": 13,
    "runs": 33,
    "last_event_id": 80
}

$ curl http://127.0.0.1:8787/ports/net/bsdrcmds
{
    "origin": "net/bsdrcmds",
    "bundles": [...],
    "jobs": [...]
}

$ curl -N -H "Last-Event-ID: 75" http://127.0.0.1:8787/events
id: 76
event: job_done
data: {"job_id": "...", "state": "done", "origin": "net/widentd", "type": "patch"}
...
```

#### Phase 7 — Snippet extraction escalation (non-AI, bounded)

Goal: allow agents to request more context without uploading huge trees/logs.

Tasks:

- Implement a size-capped snippet extractor that writes extracted slices into the bundle (e.g. `analysis/snippets/`).
- Add a mechanism to enqueue a follow-up job when snippets are added.

Done when:

- A triage/patch request for additional context can be satisfied automatically in a bounded way.

#### Phase 8 — End-to-end staging test

Goal: validate the full loop on a real failure.

Tasks:

- Trigger one known failure in staging.
- Verify: evidence bundle → queued triage → triage output → patch output → overlay update → sync → rebuild → PR.
- Verify State Server/UI reports the full timeline.

Done when:

- One port can be taken through at least one full iteration with all artifacts captured and visible.

#### Phase 9 — Bootstrap UI Dashboard

Goal: a modern, no-build Bootstrap 5 UI served by `scripts/state-server` at `/` that visualizes the agentic workflow in real time.

##### Design Decisions

- **Deployment:** served by `state-server` at same origin (no CORS)
- **Security:** no auth (trusted LAN / localhost)
- **Assets:** CDN-first (Bootstrap 5, Bootstrap Icons, marked, highlight.js, DOMPurify)
- **Implementation:** vanilla JS with hash router, no build step

##### File Layout

```
scripts/state-server-ui/
├── index.html      # Bootstrap shell, navbar, route container
├── app.js          # Router, state store, API client, view renderers
└── app.css         # Layout polish, code blocks, dark theme tweaks
```

##### Server Changes

- Serve UI: `GET /` → `index.html`, `GET /assets/*` → static files
- SSE improvements for reload continuity:
  - `GET /events?after_id=<n>` — resume from event id
  - `GET /events?tail=<n>` — start with last N events (default for first load)
- Include `ts` (server timestamp) in SSE event payloads

##### Routes

| Route | Purpose | Data Source |
|-------|---------|-------------|
| `#/overview` | Dashboard with counts + recent activity | `GET /status` + events buffer |
| `#/events` | Live event timeline with filters | SSE `/events` stream |
| `#/jobs` | Job queue with state filters | `GET /jobs`, `GET /jobs?state=...` |
| `#/runs` | Build runs list + bundles per run | `GET /runs`, `GET /runs/<id>` |
| `#/ports` | Port search | Local autocomplete |
| `#/ports/<origin>` | Per-port timeline | `GET /ports/<origin>` |
| `#/bundles` | Recent bundles list | `GET /bundles` |
| `#/bundles/<id>` | Bundle detail + artifact viewer | `GET /bundles/<id>`, artifact fetch |

##### Bootstrap Components

**Global layout:**
- Navbar: `navbar sticky-top` with nav pills, SSE status badge, last event id, pause toggle
- Main: `container-fluid` with 2-column layout (list left, detail right); offcanvas on mobile

**Badge color mapping:**
- Job states: `pending=secondary`, `inflight=primary`, `done=success`, `failed=danger`
- Event types: `job_failed=danger`, `pr_created=success`, `triage_written=warning`, `patch_written=warning`, `bundle_created=info`

**Reusable components:**
- Cards: `card shadow-sm`
- Tables: `table table-sm table-hover align-middle` in `table-responsive`
- Filters: `card` toolbar with `form-select`, `form-control`, toggle buttons
- Toasts: `toast-container` for `job_failed` / `pr_created` notifications

**Route-specific:**

| Route | Components |
|-------|------------|
| `#/overview` | Summary cards (queue progress bar, runs, bundles, last PR) + recent activity list-group |
| `#/events` | Sticky filter bar + timeline table + detail accordion/offcanvas |
| `#/jobs` | State filter btn-group + jobs table + detail card with error alert |
| `#/runs` | Runs table + detail card with bundles list-group |
| `#/ports/<origin>` | Side-by-side bundles/jobs cards |
| `#/bundles/<id>` | Header + nav-tabs (Summary/Artifacts/Triage/Patch/Errors) + pinned artifact buttons |

**Artifact rendering:**
- Markdown (`triage.md`, `patch.md`): render with `marked` + sanitize with `DOMPurify`
- Diff/logs (`patch.diff`, `errors.txt`): `pre`+`code` with `highlight.js`
- Download-only: `full.log.gz` as download button

**Smart defaults:**
- Bundle page defaults to **Triage** tab if `analysis/triage.md` exists
- Bundle page defaults to **Patch** tab if `analysis/patch.diff` exists (and no triage)
- Otherwise defaults to **Summary** tab

##### Tasks

- [x] Confirm deploy/security assumptions (served by state-server, no auth, CDN)
- [x] Define UI state model and routes
- [x] Define Bootstrap component spec
- [x] Implement Bootstrap layout and navigation (`index.html`)
- [x] Implement SSE client with replay/reconnect (`app.js`)
- [x] Build Events view with filters/search
- [x] Build Jobs/Runs/Ports/Bundles views
- [x] Implement artifact viewer (markdown/diff/logs)
- [x] Serve UI from state-server (modify Python)
- [x] Add `after_id` and `tail` query params to `/events`
- [x] Include `ts` in SSE event payloads
- [x] Smoke test and document

Done when:

- [x] Visiting `http://<builder>:8787/` loads the Bootstrap UI
- [x] Events view updates live via SSE
- [x] Page reload resumes from last event id
- [x] Drilling into job/bundle/port works
- [x] Artifact rendering (triage.md, patch.diff, errors.txt) works

Validated 2026-01-11 on DragonFlyBSD VM:

- UI served at `http://127.0.0.1:8787/` with all views functional
- SSE stream with `after_id` and `tail` query params working
- Event payloads include `ts` field
- Artifact viewer renders markdown (triage.md), diffs (patch.diff), and logs (errors.txt)
- Bundle view auto-selects appropriate tab based on available artifacts

Access from host via SSH tunnel:
```sh
ssh -i ~/.go-synth/vm/id_ed25519 -p 2222 \
    -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
    -L 8787:127.0.0.1:8787 root@localhost -N
# Then open http://127.0.0.1:8787/ in browser
```

## VM / Builder Access (Phase 1 testing)

This section documents the DragonFlyBSD builder VM used for testing the agentic workflow.

### SSH access

```sh
ssh -i /home/antonioh/.go-synth/vm/id_ed25519 -p 2222 \
    -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
    root@localhost
```

### Key paths on the builder

| Path | Purpose |
|------|---------|
| `/build/synth/DeltaPorts` | DeltaPorts overlay checkout (where changes are made) |
| `/build/synth/DPorts` | Staged DPorts checkout (used by dsynth builds) |
| `/var/log/agentic-dports` | Evidence root (`Directory_logs` for hooks) |
| `/etc/dsynth/` or `/usr/local/etc/dsynth/` | dsynth config base (hooks go here) |

### Required tools on the builder

- `rg` (ripgrep) — used by `hook_pkg_failure` for log distillation
- `gzip` — used to compress full logs

## Non-goals / explicit avoids

- Feeding full build logs into AI contexts.
- Feeding whole upstream codebases into AI contexts.
- Fully autonomous “fix everything” agents.
- Adding heavy infrastructure.

## Known limitations / future extensions

- **Workdir preservation:** dsynth builds in tmpfs and tears down per-port work state. The workflow focuses on preserving enough evidence (distilled errors + full compressed log + port context) to avoid context explosion.
- For deeper debugging, consider using dsynth directives/modes intended for debugging (e.g., `debug`) and then extract only the needed slices.
- Candidate selection automation can be layered on later (reverse-dep impact ranking), but it should stay optional and operator-controlled.
