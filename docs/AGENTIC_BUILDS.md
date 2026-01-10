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

#### Phase 1 — Evidence capture (already implemented)

Goal: dsynth failures reliably produce bounded evidence bundles.

Tasks:

- Install the dsynth hooks from `scripts/dsynth-hooks/` into dsynth’s config base.
- Verify that failures generate bundles with `meta.txt`, `logs/errors.txt` (capped), `logs/full.log.gz`, and `port/*` context.

Done when:

- A known failing port produces an evidence bundle under `${Directory_logs}/evidence/runs/.../ports/.../` with the expected files.

#### Phase 2 — Central queue enqueue (hook-side)

Goal: every failure instance becomes a queue job; hooks remain non-blocking.

Tasks:

- Update `scripts/dsynth-hooks/hook_common.sh` to add:
  - `queue_root()` → `${Directory_logs}/evidence/queue/`
  - `ensure_queue_dirs()` → create `pending/`, `inflight/`, `done/`, `failed/`
  - `enqueue_job(...)` → write a `key=value` job file and atomically enqueue it (temp + `mv`)
- Update `scripts/dsynth-hooks/hook_pkg_failure` to enqueue one job per failure instance after writing the evidence bundle.

Done when:

- Each new failure bundle produces exactly one `.job` in `${Directory_logs}/evidence/queue/pending/`.

#### Phase 3 — Worker: triage jobs (runner-side)

Goal: asynchronously triage failures via `opencode serve` and write outputs back into the bundle.

Tasks:

- Add `scripts/agent-queue-runner` (single worker).
- Runner interface:
  - `scripts/agent-queue-runner --queue-root <path>`
  - Optional modes for testing: `--once` and `--dry-run`
- Implement job lifecycle:
  - Claim by atomic rename: `pending/` → `inflight/`
  - On success: `inflight/` → `done/`
  - On permanent failure: `inflight/` → `failed/` (+ error note)
- Implement synchronous opencode calls (session-per-job):
  - `POST /session`
  - `POST /session/<id>/message`
  - Config via env vars like `OPENCODE_URL`, `OPENCODE_AGENT`, and `OPENCODE_TIMEOUT`.
- Write outputs into `bundle_dir/analysis/`:
  - `analysis/triage.json` (raw response)
  - `analysis/triage.md` (human-readable extraction)
  - `analysis/session_id.txt`

Done when:

- A pending job becomes `done/` and the referenced bundle contains `analysis/triage.md` + `analysis/triage.json`.

#### Phase 4 — Patch generation (agent output)

Goal: generate DeltaPorts overlay changes as a patch suitable for review and application.

Tasks:

- Extend the job format to support `type=patch` (initially manual enqueue is fine).
- Add a patch agent (`dports-patch`) that consumes bounded evidence + triage output.
- Store outputs into `bundle_dir/analysis/`:
  - `analysis/patch.diff` (DeltaPorts-style diffs that apply to the DeltaPorts overlay checkout)
  - optional `analysis/patch.md` (short rationale)

Done when:

- For a known failure, a patch job produces a usable `analysis/patch.diff`.

#### Phase 5 — Apply patch to DeltaPorts overlay, sync to DPorts, rebuild, open PR

Goal: integrate with the existing staged build workflow while ensuring the system never pushes to master.

Assumptions:

- There is a local **DeltaPorts overlay** checkout where changes are made.
- There is a local **DPorts** checkout used by the staged build.
- An existing sync script applies overlay changes into the DPorts checkout.

Tasks:

- Add an “apply + PR” helper (separate from the runner) that:
  - Applies `analysis/patch.diff` to the DeltaPorts overlay checkout on a new branch.
  - Runs the existing sync script to update the DPorts checkout.
  - Triggers/resumes the dsynth build for the target port.
  - Uses `gh` to open a PR in the DeltaPorts overlay repo.
- Hard rule:
  - Never push to `master` for DeltaPorts or DPorts.
  - Always open a PR against the DeltaPorts overlay.

Done when:

- A patch can be applied to the overlay, synced into the staged DPorts checkout, rebuilt, and results are tracked back into the evidence bundle.

#### Phase 6 — Observability/UI (State Server, observe-only)

Goal: provide a single API for a remote UI with live progress and full historical detail.

Tasks:

- Implement a builder-side State Server that:
  - Observes `${Directory_logs}/evidence/` and `${Directory_logs}/evidence/queue/`.
  - Persists state/history to SQLite.
  - Provides a REST API (status/jobs/runs/ports) and SSE (`GET /events`).
  - Serves artifacts including `logs/full.log.gz`.
- Keep it observe-only:
  - It does not drain jobs.
  - It does not call `opencode serve`.
  - It does not run builds.

Done when:

- A remote UI can subscribe to `/events` and see queue/job progression live while also querying full history and artifacts.

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

## Non-goals / explicit avoids

- Feeding full build logs into AI contexts.
- Feeding whole upstream codebases into AI contexts.
- Fully autonomous “fix everything” agents.
- Adding heavy infrastructure.

## Known limitations / future extensions

- **Workdir preservation:** dsynth builds in tmpfs and tears down per-port work state. The workflow focuses on preserving enough evidence (distilled errors + full compressed log + port context) to avoid context explosion.
- For deeper debugging, consider using dsynth directives/modes intended for debugging (e.g., `debug`) and then extract only the needed slices.
- Candidate selection automation can be layered on later (reverse-dep impact ranking), but it should stay optional and operator-controlled.
