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
   - `analysis/patch_plan.json`
   - `analysis/patch.log`
   - `analysis/rebuild_status.txt`
   - `analysis/rebuild_proof.json`
   - `analysis/review.md`
   - `analysis/snippet_requests.json`



7. **Snippet extraction escalation (non-AI, size-capped)**
   - Only if triage requests more context, extract the smallest relevant source/config snippets and append them to a follow-up request.
   - Keep strict caps so the patch-author agent stays accurate.

8. **Dry-run the full loop on one known failure**
   - dsynth failure → evidence bundle → triage output → (optional snippet extraction) → workspace patch outputs → rebuild.

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

#### Phase 4 — Agentic workspace (single shared tree, custom tools) (PLANNED)

Goal: generate DeltaPorts artifacts by operating on a shared DragonFly workspace (FPORTS → DeltaPorts → DPorts), using `dupe` + `genpatch` and per-file diffs. The patch agent edits files through custom tools and commits changes on a per-origin fix branch. Unified diff generation is no longer used.

##### Workspace layout (DragonFly)

- `/build/synth/agentic-workspace/`
  - `FPORTS/` (full FreeBSD ports checkout, pinned to a quarterly branch)
  - `DeltaPorts/` (working repo; branches used per origin)
  - `DPorts/` (staged/generated tree used by dsynth)
  - `workspace.json` (pinning + paths)

`workspace.json` (example):
```json
{
  "fports_path": "/build/synth/agentic-workspace/FPORTS",
  "fports_ref": "2026Q1",
  "deltaports_path": "/build/synth/agentic-workspace/DeltaPorts",
  "dports_path": "/build/synth/agentic-workspace/DPorts",
  "dsynth_profile": "agentic"
}
```

##### FPORTS pin policy (verify-only)

- The worker verifies that `FPORTS` is clean and `HEAD == fports_ref`.
- It will not `git pull` or move the ref automatically (quarterly updates remain manual).

##### dsynth profile integration

- dsynth configuration lives in `/etc/dsynth/dsynth.ini`.
- Create a profile (e.g. `agentic`) that points `DPORTS` at `/build/synth/agentic-workspace/DPorts`.
- Use that profile for `dsynth just-build`.

##### Tool deployment

- Repo source of truth: `config/opencode/tool/`
- Deployed to the opencode runtime user at: `~/.config/opencode/tool/`
- Restart `opencode serve` after copying tools.

##### Remote configuration (VM now, production later)

Tools read SSH + path configuration from env:

- `DP_SSH_HOST`, `DP_SSH_PORT`, `DP_SSH_KEY`
- `DP_WORKSPACE_BASE=/build/synth/agentic-workspace`

##### Branch lifecycle (one origin at a time)

- Fix branch name: `ai-work/<origin_sanitized>`
- At start of a patch job:
  - checkout `ai-work/<origin_sanitized>` (create from `master` if missing)
- Each attempt makes a **commit** to that branch.
- At the end of the job (success or failure): checkout `master` and keep the branch intact.
- The next attempt continues on the same branch (incremental fixes are preserved).

##### Agentic workflow (per origin)

1. **Verify workspace + checkout branch**
   - Verify `workspace.json` and FPORTS pin.
   - Checkout `ai-work/<origin_sanitized>`.

2. **Materialize closure**
   - Regenerate `DPorts/<origin>` from `FPORTS` + DeltaPorts overlay.
   - Resolve `MASTERDIR` closure by querying `make -V MASTERDIR` and regenerating required master ports.

3. **Extract**
   - `make extract` inside `DPorts/<origin>`.
   - Record `WRKSRC`/`WRKDIR`.

4. **Source patches (WRKSRC → dragonfly/patch-*)**
   - `dupe <file>` → edits performed by full-file `get`/`put`.
   - `genpatch <file>` writes `patch-*` in a controlled output dir.
   - Install into `DeltaPorts/ports/<origin>/dragonfly/`.

5. **Skeleton diffs (FPORTS base → diffs/*.diff)**
   - Edit port skeleton files (Makefile/distinfo/pkg-plist/files/*) in staged `DPorts/<origin>`.
   - Emit one diff per file vs `FPORTS/<origin>/<relpath>` into `DeltaPorts/ports/<origin>/diffs/*.diff`.
   - Update `diffs/REMOVE` for deletions.

6. **Overlay-only changes**
   - Edit `Makefile.DragonFly` or other overlay files directly in `DeltaPorts/ports/<origin>`.

7. **Commit, re-materialize, rebuild**
   - Commit changes on `ai-work/<origin_sanitized>`.
   - Re-materialize closure to ensure diffs apply cleanly.
   - Run `dsynth -p <profile> just-build <origin>`.

8. **Record outputs**
   - Write `analysis/patch_plan.json`, `analysis/patch.log`, `analysis/rebuild_status.txt`, `analysis/rebuild_proof.json` into the bundle.
   - Mark patch job done/failed accordingly.

9. **Clean slate**
   - Checkout `master` and ensure working tree is clean.
   - Ensure the staged `DPorts` tree is reset for the next origin (no leftover workdirs or patch artifacts).

##### Incremental knowledge

- The patch job payload includes the **last 3 attempts** for the same origin:
  - `analysis/patch_plan.json`
  - `analysis/patch.log`
  - `analysis/rebuild_status.txt`

##### Custom tool surface (minimum viable)

- `dports_workspace_verify()`
- `dports_checkout_branch(origin)`
- `dports_commit(origin, message)`
- `dports_materialize_closure(origin)`
- `dports_extract(origin)`
- `dports_get_file(path)`
- `dports_put_file(path, content, expected_sha256?)`
- `dports_dupe(path)`
- `dports_genpatch(path)`
- `dports_install_patches(origin, patch_files)`
- `dports_emit_diff(origin, relpath)`
- `dports_dsynth_build(origin, profile)`

##### Job types and auto-enqueue rules

Job types include `triage`, `patch`, and `pr`.

- Patch jobs operate through custom tools instead of generating unified diffs.
- PR jobs are enqueued only after a successful rebuild proof.

Auto-enqueue rules remain:

- Classification: `compile-error`, `configure-error`, `patch-error`, `plist-error`
- Confidence: `high` or `medium`

Classifications that do NOT auto-enqueue: `missing-dep`, `fetch-error`, `unknown`.

##### Notes

- This phase replaces the earlier `analysis/patch.diff` flow and supersedes `apply-patch` with workspace-driven rebuilds.
- No jail initially; enforce safety via tool allowlists and path validation.

#### Phase 5 — Workspace rebuild + PR jobs (PLANNED)

Goal: rebuild using the shared workspace artifacts and optionally open PRs via a queue-driven workflow.

##### Workflow

1. Use the shared DeltaPorts workspace (`/build/synth/agentic-workspace/DeltaPorts`) and staged DPorts (`/build/synth/agentic-workspace/DPorts`).
2. Re-materialize `DPorts/<origin>` + MASTERDIR closure.
3. Run `dsynth -p <profile> just-build <origin>`.
4. Write proof artifacts into the bundle:
   - `analysis/rebuild_status.txt`
   - `analysis/rebuild_log.txt`
   - `analysis/rebuild_proof.json`
5. UI shows: **Build succeeded — proof available**.
6. UI button enqueues a PR job if desired.

##### PR job enqueue endpoint (state-server)

- `POST /enqueue/pr`
- Body: `{ "bundle_id": "...", "origin": "..." }`
- The server verifies that `analysis/rebuild_proof.json` exists and reports success.
- The server writes a `type=pr` job into `${Directory_logs}/evidence/queue/pending/`.

**WARNING:** This endpoint is unauthenticated. Only expose it on trusted localhost/LAN and do not bind it to public interfaces.

##### PR job processing (runner)

- Validate `rebuild_ok=true` from `analysis/rebuild_proof.json`.
- Checkout `ai-work/<origin_sanitized>` at the proven commit hash.
- Push branch to `origin`.
- `gh pr create --base master --head <branch>`
- Write `analysis/pr_url.txt` and `analysis/pr.json` to the bundle.
- Checkout `master` afterward.

##### Notes

- PR creation assumes `gh` authentication is already configured on the builder.
- The PR base branch is `master`.




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

#### Phase 7 — DB-only artifact store (daemon + blobstore) (PLANNED)

Goal: eliminate per-bundle filesystem artifacts and store evidence in a local blobstore with a single-writer daemon.

Design summary:

- Bundles/runs are DB-only (no bundle directories).
- Small artifacts are stored as content-addressed blobs under `${DIR_LOGS}/evidence/blobstore/`.
- `logs/full.log.gz` stays on disk under `${DIR_LOGS}/evidence/full-logs/<bundle_id>.full.log.gz` and is referenced as an fs-backed artifact.
- Queue remains file-based (`${DIR_LOGS}/evidence/queue/`).

Key components:

- `scripts/artifact-store` (daemon, localhost:8788)
  - Single writer to `state.db` and blobstore.
  - Endpoints:
    - `POST /v1/bundles/upsert`
    - `POST /v1/artifacts/put` (blob)
    - `POST /v1/artifacts/put-fs` (fs ref)
- `scripts/artifact-store-client` (tiny CLI for hooks)
  - Commands: `health`, `bundle-upsert`, `put-blob`, `put-fs`
- `scripts/state-server`
  - DB/blobstore consumer only (no filesystem scan).
  - Artifacts served from `artifact_refs` + blobstore/fs paths.
- `scripts/dsynth-hooks/hook_pkg_failure`
  - Posts metadata, `errors.txt`, and port snapshots to daemon.
  - Writes `full.log.gz` to spool path and registers fs ref.
- `scripts/agent-queue-runner`
  - Reads artifacts from blobstore, writes outputs via daemon.
  - Jobs reference `bundle_id` (not `bundle_dir`).

DB schema additions:

- `blob_objects(sha256 PRIMARY KEY, size, created_at)`
- `artifact_refs(bundle_id, relpath, backend, sha256, fs_path, kind, size, created_at)`

Opencode TODO (artifact-store cutover):

- [ ] Implement `scripts/artifact-store` daemon (8788) with WAL + busy_timeout and auto-created dirs.
- [ ] Implement `scripts/artifact-store-client` CLI and use it in dsynth hooks.
- [ ] Update `hook_pkg_failure` to post bundle + artifacts and register `full.log.gz` fs path.
- [ ] Update runner to use `bundle_id` + blobstore IO for triage/patch outputs.
- [ ] Update state-server to serve artifacts from DB/blobstore (no scans).
- [ ] Update job format (`bundle_id=...`) and document it here.
- [ ] Smoke test end-to-end on VM (failure → triage → patch → apply).

Notes:

- No opencode agent prompt changes required for this phase.
- Daemon failure should fail hooks/runner (no fallback).

#### Phase 8 — Snippet extraction escalation (non-AI, bounded) (DONE)

Goal: allow agents to request more context (source files, build system files, log ranges) without uploading huge trees/logs. The snippet extractor runs on the VM (DragonFlyBSD builder) and extracts bounded content from preserved workdirs, distfiles, or logs.

##### Design Overview

- **Snippet extractor runs on VM** where distfiles and workdirs live
- **Up to 5 snippet rounds** (configurable via `OPENCODE_MAX_SNIPPET_ROUNDS`)
- **Incremental feedback**: agent sees what was extracted, failures, and budget remaining
- **Agent configurations** saved in repo as `config/opencode-agents.json`
- **Dual execution mode**: Runs locally on DragonFlyBSD, or via SSH on Linux dev hosts

##### Snippet Request Format

Agents request snippets by including a `## Snippet Requests` section in their output:

```markdown
## Snippet Requests

- `source:src/foo.c:142:±20` — 20 lines around line 142 in src/foo.c
- `source:include/bar.h:all` — entire header file (capped at 50KB)
- `buildsystem:CMakeLists.txt` — root build file
- `buildsystem:src/CMakeLists.txt` — subdirectory build file
- `configure:configure.ac` — autoconf input
- `log:1200:1400` — lines 1200-1400 from full build log
```

**Request Grammar:**

| Type | Format | Description |
|------|--------|-------------|
| `source` | `source:<path>:<line>:<context>` | Source file from workdir/distfiles |
| `buildsystem` | `buildsystem:<path>` | CMakeLists.txt, meson.build, Makefile.am, etc. |
| `configure` | `configure:<path>` | configure.ac, configure.in, etc. |
| `log` | `log:<start_line>:<end_line>` | Lines from full.log.gz |

**Context specifiers for source:**
- `±N` — N lines before and after target line (default: `±30`)
- `all` — Entire file (subject to 50KB per-file cap)

**Size caps:**
- Per-snippet: 50KB max
- Total per round: 200KB max
- If requests exceed cap: extract in order, note truncation in manifest

##### Source Extraction Strategy

The snippet extractor tries multiple sources in priority order:

| Priority | Source | When Available | How to Access |
|----------|--------|----------------|---------------|
| 1 | **Preserved workdir** | After `dsynth debug` | Scan `${DIR_BUILDBASE}/SL*/construction/${cat}/${port}/work/` |
| 2 | **Distfile extraction** | Always (if distfiles exist) | Parse `port/distinfo`, extract from `${DIR_DISTFILES}` |
| 3 | **Full log mining** | Always | Extract source lines from compiler output in `logs/full.log.gz` |

**Workdir detection:**
- dsynth doesn't expose `WORKER_SLOT` in hook env, so extractor scans all `SL*` dirs
- Look for `SL*/construction/<cat>/<port>/work/` matching the origin
- If found, use directly; else fall back to distfiles

##### Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `SNIPPET_DISTFILES_DIR` | From `meta.txt:dir_distfiles` | Distfiles cache location |
| `SNIPPET_BUILDBASE_DIR` | From `meta.txt:dir_buildbase` | dsynth build base (for workdir scan) |
| `SNIPPET_MAX_PER_FILE` | `51200` | Max bytes per snippet (50KB) |
| `SNIPPET_MAX_TOTAL` | `204800` | Max bytes per round (200KB) |
| `OPENCODE_MAX_SNIPPET_ROUNDS` | `5` | Max snippet request rounds |

##### Execution Mode

The runner automatically selects execution mode based on environment:

| Mode | Condition | Behavior |
|------|-----------|----------|
| **Local** | `VM_SSH_HOST` not set | Runs `snippet-extractor` directly (DragonFlyBSD production) |
| **SSH** | `VM_SSH_HOST` is set | SSHs to VM to run `snippet-extractor` (Linux development) |

Environment variables for SSH mode:
```sh
VM_SSH_HOST=root@localhost    # Enables SSH mode when set
VM_SSH_PORT=2222              # Optional, default 2222
VM_SSH_KEY=/path/to/key       # Optional, default ~/.go-synth/vm/id_ed25519
```

##### Snippet Extractor Script

**Location:** `scripts/snippet-extractor`

**Interface:**
```sh
scripts/snippet-extractor \
  --bundle /path/to/evidence/bundle \
  [--round 1] \
  [--distfiles-dir /build/synth/distfiles] \
  [--buildbase-dir /build/synth] \
  [--max-per-snippet 51200] \
  [--max-total 204800] \
  [--prefer-workdir] \
  [--dry-run]

# Reads requests from bundle/analysis/triage.md or bundle/analysis/patch.md
# Writes to bundle/analysis/snippets/round_N/
```

**Exit codes:**
- `0` - Success, at least some snippets extracted
- `1` - No requests found
- `2` - All requests failed (nothing extracted)
- `3` - Configuration/usage error

##### Output Structure

```
analysis/snippets/
├── manifest.json           # Cumulative manifest across all rounds
├── round_1/
│   ├── manifest.json       # This round's results
│   ├── source/
│   │   └── src_foo.c.txt
│   ├── buildsystem/
│   │   └── CMakeLists.txt.txt
│   └── log/
│       └── lines_1200-1400.txt
├── round_2/
│   └── ...
└── .workdir/               # Cached extracted distfiles (ephemeral)
```

##### Manifest Format

```json
{
  "rounds": [
    {
      "round": 1,
      "source": "distfiles",
      "distfile": "example-1.2.3.tar.gz",
      "requests": [
        {
          "raw": "source:src/foo.c:142:±20",
          "type": "source",
          "path": "src/foo.c",
          "line": 142,
          "context": 20,
          "status": "ok",
          "output": "round_1/source/src_foo.c.txt",
          "bytes": 1234,
          "actual_lines": [122, 162],
          "note": null
        }
      ],
      "total_bytes": 5678,
      "budget_remaining": 198522,
      "truncated": false
    }
  ],
  "total_rounds": 1,
  "total_bytes_all_rounds": 5678
}
```

##### Follow-up Job Mechanism

**Job format extensions:**
```
type=triage|patch
snippet_round=0
has_snippets=false
parent_job=
```

After snippet extraction:
```
type=triage|patch
snippet_round=1
has_snippets=true
parent_job=20260111-020000Z-LiveSystem-devel_foo-12345.job
```

**Flow:**

1. Triage/patch job runs, agent produces output
2. Runner parses output for `## Snippet Requests` section
3. If requests found AND `snippet_round < max`:
   - Run `snippet-extractor` (locally or via SSH depending on mode)
   - Enqueue follow-up job with `snippet_round+1`, `has_snippets=true`
4. If no requests or max rounds reached:
   - Proceed with normal flow (enqueue patch job or mark done)

##### Agent Payload with Snippets

When `has_snippets=true`, payload includes feedback and content:

```markdown
## Snippet Extraction Results

**Round 1** | Source: `distfiles` | Budget remaining: 198522 bytes

| Request | Status | Output | Bytes |
|---------|--------|--------|-------|
| `source:src/foo.c:142:±20` | ok | source/src_foo.c.txt | 1234 |
| `source:missing.h:all` | not_found | - | 0 |
| `log:1200:1400` | ok | log/lines_1200-1400.txt | 4444 |

**Snippet rounds used:** 1/5 (remaining: 4)

## Extracted Snippets

*Source: distfile `example-1.2.3.tar.gz`*

### source/src/foo.c
```c
// Lines 122-162 of src/foo.c
...content...
```

### log/lines_1200-1400
```
...log content...
```
```

##### Tasks

- [x] Document Phase 7 plan
- [x] Create `config/opencode-agents.json` with agent definitions
- [x] Update agent prompts with snippet request documentation
- [x] Create `scripts/snippet-extractor` (Python, runs on VM)
- [x] Implement distfile extraction (tar/zip parsing)
- [x] Implement workdir detection (scan SL* dirs)
- [x] Implement log line extraction from full.log.gz
- [x] Update `scripts/agent-queue-runner`:
  - [x] Add `parse_snippet_requests()`
  - [x] Add `load_snippets_content()` (renamed from `add_snippet_content_to_payload`)
  - [x] Add `build_snippet_feedback()`
  - [x] Add `run_snippet_extractor()` (local or SSH based on VM_SSH_HOST)
  - [x] Add `enqueue_followup_job()`
  - [x] Add `check_and_handle_snippet_requests()`
  - [x] Update `build_triage_payload()` to include snippets
  - [x] Update `build_patch_payload()` to include snippets
  - [x] Update `process_triage_job()` to handle snippet flow
  - [x] Update `process_patch_job()` to handle snippet flow
- [x] Update `scripts/dsynth-hooks/hook_common.sh` with `snippet_round=0` in initial jobs
- [ ] Test end-to-end with real failure on VM

Done when:

- [x] A triage/patch request for additional context can be satisfied automatically in a bounded way, with up to 5 rounds of refinement.

Validated 2026-01-11:

**Snippet extractor (`scripts/snippet-extractor`):**
- Parses `## Snippet Requests` from triage.md/patch.md
- Extracts from distfiles (tar.gz, tar.xz, tar.bz2, zip)
- Scans preserved workdirs in `SL*/construction/`
- Extracts log ranges from full.log.gz
- Writes structured output with per-round and cumulative manifests

**Agent queue runner (`scripts/agent-queue-runner`):**
- Detects snippet requests in agent output
- Runs snippet-extractor locally (DragonFlyBSD) or via SSH (Linux dev)
- Enqueues follow-up jobs with incremented `snippet_round`
- Includes extraction feedback and snippet content in follow-up payloads

Example dry-run with snippets:
```
$ scripts/agent-queue-runner --queue-root /tmp/test-queue --once --dry-run
2026-01-11T22:43:34Z INFO  processing job test-job.job
2026-01-11T22:43:34Z INFO  [dry-run] type=triage, round=1, would send payload (9181 bytes)
============================================================
JOB TYPE: triage (snippet_round=1)
============================================================
## Snippet Extraction Results

**Round 1** | Source: `distfiles` | Budget remaining: 203264 bytes

| Request | Status | Output | Bytes |
|---------|--------|--------|-------|
| `source:src/main.c:142:±20` | ok | source/src_main.c.txt | 1024 |
| `source:include/config.h:all` | not_found | - | 0 |
| `log:500:550` | ok | log/lines_500-550.txt | 512 |

**Snippet rounds used:** 1/5 (remaining: 4)

## Extracted Snippets

*Source: distfile `test-port-1.0.tar.gz`*

### source/src/main.c
```c
// Lines 122-162 of src/main.c
...
```
...
```

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
- Markdown (`triage.md`, `patch.md`, `patch.log`): render with `marked` + sanitize with `DOMPurify`
- Logs (`errors.txt`, `rebuild_status.txt`): `pre`+`code` with `highlight.js`
- JSON (`rebuild_proof.json`, `patch_plan.json`): pretty-print with monospace
- Download-only: `full.log.gz` as download button

**Smart defaults:**
- Bundle page defaults to **Triage** tab if `analysis/triage.md` exists
- Bundle page defaults to **Patch** tab if `analysis/patch.log` or `analysis/patch_plan.json` exists (and no triage)
- Otherwise defaults to **Summary** tab

**PR action:**
- If `analysis/rebuild_proof.json` reports success, show a **Create PR** button.
- Button calls `POST /enqueue/pr` with `bundle_id`.
- **WARNING:** `/enqueue/pr` is unauthenticated. Only expose it on trusted localhost/LAN.

##### Tasks

- [x] Confirm deploy/security assumptions (served by state-server, no auth, CDN)
- [x] Define UI state model and routes
- [x] Define Bootstrap component spec
- [x] Implement Bootstrap layout and navigation (`index.html`)
- [x] Implement SSE client with replay/reconnect (`app.js`)
- [x] Build Events view with filters/search
- [x] Build Jobs/Runs/Ports/Bundles views
- [x] Implement artifact viewer (markdown/logs/json)
- [x] Serve UI from state-server (modify Python)
- [x] Add `after_id` and `tail` query params to `/events`
- [x] Include `ts` in SSE event payloads
- [x] Smoke test and document

Done when:

- [x] Visiting `http://<builder>:8787/` loads the Bootstrap UI
- [x] Events view updates live via SSE
- [x] Page reload resumes from last event id
- [x] Drilling into job/bundle/port works
- [x] Artifact rendering (triage.md, patch.log, errors.txt, rebuild_proof.json) works

Validated 2026-01-11 on DragonFlyBSD VM:

- UI served at `http://127.0.0.1:8787/` with all views functional
- SSE stream with `after_id` and `tail` query params working
- Event payloads include `ts` field
- Artifact viewer renders markdown (triage.md, patch.log), logs (errors.txt), and JSON (rebuild_proof.json)
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
| `/build/synth/agentic-workspace` | Shared agentic workspace (FPORTS/DeltaPorts/DPorts) |
| `/var/log/agentic-dports` | Evidence root (`Directory_logs` for hooks) |
| `/etc/dsynth/` or `/usr/local/etc/dsynth/` | dsynth config base (hooks + profiles go here) |

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
