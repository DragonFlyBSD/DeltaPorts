# End-to-End Testing Guide for Agentic DPorts Workflow

This document describes the manual testing process for validating the complete agentic workflow from build failure through PR creation.

> **Future Work:** This manual process should be automated into a `scripts/test-e2e` regression test script once the workflow is stable.

## Overview

The agentic workflow consists of these stages:

```
┌─────────────────────────────────────────────────────────────────────────┐
│  1. FAILURE CAPTURE                                                      │
│     dsynth test <port> → evidence bundle + queue job                     │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  2. TRIAGE                                                               │
│     agent-queue-runner → analysis/triage.md                              │
│     ├─ If snippet requests → 2a. SNIPPET EXTRACTION (up to 5 rounds)    │
│     └─ If patchable → auto-enqueue patch job                            │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  3. PATCH + REBUILD (workspace)                                           │
│     agent tools → DeltaPorts branch → dsynth just-build                  │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  4. PR JOB (optional)                                                    │
│     UI → enqueue pr job → gh pr create                                   │
└─────────────────────────────────────────────────────────────────────────┘
```

## Prerequisites

### On the Host (Linux)

1. **opencode serve** running (as user `opencode`):
   ```sh
   opencode serve --hostname 0.0.0.0 --port 4097
   ```

2. **OpenCode custom tools** installed for the `opencode` user (manual step).

3. **SSH access** to VM configured (for tools):
   ```sh
   export DP_SSH_HOST=root@localhost
   export DP_SSH_PORT=2222
   export DP_SSH_KEY=/home/antonioh/.go-synth/vm/id_ed25519
   export DP_WORKSPACE_BASE=/build/synth/agentic-workspace
# optional overrides if dports.conf differs
export DP_FPORTS_DIR=/build/synth/agentic-workspace/freebsd-ports
export DP_DELTAPORTS_DIR=/build/synth/agentic-workspace/deltaports
export DP_DPORTS_DIR=/build/synth/agentic-workspace/dports
   ```

### On the VM (DragonFlyBSD)

1. **VM DeltaPorts repo is up to date** (commit + push locally, then pull on VM):
   ```sh
   cd /build/synth/DeltaPorts
   git pull
   ```

2. **DeltaPorts scripts** deployed to `/build/synth/DeltaPorts/scripts/` (includes `agentic-worker`)

  3. **Shared workspace root** exists:
   ```sh
   mkdir -p /build/synth/agentic-workspace
   ```

  3. **workspace.json** created (pin FPORTS ref):
   ```sh
   cat > /build/synth/agentic-workspace/workspace.json <<'EOF'
   {
     "fports_path": "/build/synth/agentic-workspace/freebsd-ports",
     "fports_ref": "2025Q2",
     "deltaports_path": "/build/synth/agentic-workspace/deltaports",
     "dports_path": "/build/synth/agentic-workspace/dports",
     "dsynth_profile": "agentic"
   }
   EOF
   ```

   Note: this workspace setup is a good candidate for automation later.

  4. **FPORTS checkout** at `/build/synth/agentic-workspace/freebsd-ports` and checked out to the pinned ref:
   ```sh
   git -C /build/synth/agentic-workspace/freebsd-ports checkout 2025Q2
   ```

  5. **DeltaPorts checkout** at `/build/synth/agentic-workspace/deltaports`

  6. **DPorts directory** exists:
   ```sh
   mkdir -p /build/synth/agentic-workspace/dports
   ```


8. **dsynth hooks** installed in `/etc/dsynth/` or `/usr/local/etc/dsynth/`

9. **dsynth profile** added in `/etc/dsynth/dsynth.ini` (copy `LiveSystem` values and override ports dir):
   ```ini
   [agentic]
   Operating_system= DragonFly
    Directory_packages= /build/synth/agentic-workspace/packages
    Directory_repository= /build/synth/agentic-workspace/packages/All
    Directory_portsdir= /build/synth/agentic-workspace/dports
    Directory_options= /build/synth/agentic-workspace/options
    Directory_distfiles= /build/synth/distfiles
    Directory_buildbase= /build/synth/agentic-workspace
    Directory_logs= /build/synth/agentic-workspace/logs

   Directory_ccache= disabled
   Directory_system= /
   Package_suffix= .txz
   Number_of_builders= 2
   Max_jobs_per_builder= 3
   Tmpfs_workdir= true
   Tmpfs_localbase= true
   Display_with_ncurses= false
   leverage_prebuilt= false
   ```

 10. **artifact-store daemon** running (required):
   ```sh
   /build/synth/DeltaPorts/scripts/artifact-store --logs-root /build/synth/agentic-workspace/logs &
   ```

 11. **State Server** running (optional, for UI verification):
   ```sh
   /build/synth/DeltaPorts/scripts/state-server --logs-root /build/synth/agentic-workspace/logs &
   ```


12. **Generator config** at `/usr/local/etc/dports.conf` with valid paths

## Test Execution

### Stage 1: Trigger Failure and Verify Evidence Capture

**On VM:**
```sh
dsynth -p agentic test net/hostapd
```

**Verify:**
```sh
# Find latest bundle via API
curl -s http://127.0.0.1:8787/bundles | head -80

# Inspect bundle detail (replace <bundle_id>)
curl -s http://127.0.0.1:8787/bundles/<bundle_id> | head -80

# Fetch meta.txt + errors.txt
curl -s http://127.0.0.1:8787/bundles/<bundle_id>/artifacts/meta.txt
curl -s http://127.0.0.1:8787/bundles/<bundle_id>/artifacts/logs/errors.txt | head -50

# Check full log location (fs-backed)
ls -la /build/synth/agentic-workspace/logs/evidence/full-logs/<bundle_id>.full.log.gz

# Check job file
ls -la /build/synth/agentic-workspace/logs/evidence/queue/pending/
cat /build/synth/agentic-workspace/logs/evidence/queue/pending/*.job | head -20
```

**Expected:**
- Bundle exists in `/bundles` output with artifacts list
- Artifacts include `meta.txt`, `logs/errors.txt`, `logs/full.log.gz`, `port/*`
- Full log is stored at `/build/synth/logs/evidence/full-logs/<bundle_id>.full.log.gz`
- Job file contains `type=triage`, `snippet_round=0`, `has_snippets=false`, `bundle_id=<id>`

---

### Stage 2: Triage Job Processing

**On VM:**
```sh
OPENCODE_URL=http://10.0.2.2:4097 \
  /build/synth/DeltaPorts/scripts/agent-queue-runner \
  --queue-root /build/synth/logs/evidence/queue --once
```

**Verify:**
```sh
# Check job moved to done
ls -la /build/synth/logs/evidence/queue/done/

# Check triage output
curl -s http://127.0.0.1:8787/bundles/<bundle_id>/artifacts/analysis/triage.md | head -80

# Check for snippet requests (determines next step)
curl -s http://127.0.0.1:8787/bundles/<bundle_id>/artifacts/analysis/triage.md | grep -A 20 "## Snippet Requests" || echo "No snippet requests"
```

**Expected:**
- Job in `done/` directory
- `analysis/triage.md` with Classification, Platform, Root Cause, Evidence, Suggested Fix, Confidence

**Branch Point:**
- If `## Snippet Requests` section exists → Continue to Stage 2a
- If no snippet requests → Skip to Stage 3

---

### Stage 2a: Snippet Extraction Flow

**Note:** In DB-only artifact mode, snippet extraction is disabled unless a filesystem bundle directory exists.
If you need snippets, run in legacy bundle mode or add an explicit snippet store.

---

### Stage 3: Patch + Rebuild Job Processing

**Prerequisite:** Triage completed with patchable classification and sufficient confidence.

**Verify patch job exists:**
```sh
ls -la /build/synth/logs/evidence/queue/pending/
grep "type=patch" /build/synth/logs/evidence/queue/pending/*.job
```

**Run:**
```sh
OPENCODE_URL=http://10.0.2.2:4097 \
  /build/synth/DeltaPorts/scripts/agent-queue-runner \
  --queue-root /build/synth/logs/evidence/queue --once
```

**Verify outputs:**
```sh
curl -s http://127.0.0.1:8787/bundles/<bundle_id>/artifacts/analysis/patch_plan.json | head -40
curl -s http://127.0.0.1:8787/bundles/<bundle_id>/artifacts/analysis/patch.log | head -40
curl -s http://127.0.0.1:8787/bundles/<bundle_id>/artifacts/analysis/rebuild_status.txt | head -40
curl -s http://127.0.0.1:8787/bundles/<bundle_id>/artifacts/analysis/rebuild_proof.json | head -40
```

**Expected:**
- `analysis/patch_plan.json` describes tool actions and files changed
- `analysis/patch.log` summarizes edits and rationale
- `analysis/rebuild_status.txt` reports SUCCESS/FAILURE
- `analysis/rebuild_proof.json` includes `fports_ref`, commit hashes, and profile

---

### Stage 4: PR Job (optional)

**Prerequisite:** Rebuild succeeded.

**Enqueue PR job (unauthenticated endpoint):**
```sh
curl -X POST http://127.0.0.1:8787/enqueue/pr \
  -H 'Content-Type: application/json' \
  -d '{"bundle_id":"<bundle_id>","origin":"net/hostapd"}'
```

**WARNING:** The PR enqueue endpoint is unauthenticated. Only expose it on trusted localhost/LAN.

**Verify:**
```sh
ls -la /build/synth/logs/evidence/queue/pending/ | grep pr
curl -s http://127.0.0.1:8787/bundles/<bundle_id>/artifacts/analysis/pr_url.txt
```

**Expected:**
- A `type=pr` job is created in the queue
- `analysis/pr_url.txt` appears after the PR job completes

---

## Error Handling Paths

These may occur naturally during testing. Document what happens:

### Unpatchable Classification

If triage returns `missing-dep`, `fetch-error`, or `unknown`:
- No patch job will be auto-enqueued
- Check runner logs for: "not auto-enqueueing patch job"
- Human intervention required

### Patch Workspace Failure

If the patch job fails during workspace operations:
- Job moves to `failed/`
- Check `<job>.error` for the tool error (workspace verify, extract, genpatch, rebuild)

### Rebuild Failure

If `dsynth just-build` fails after patch applied:
- `rebuild_status.txt` contains failure output
- No PR created
- Branch remains for manual investigation

### Snippet Extraction Failure

If snippets cannot be extracted:
- `manifest.json` shows requests with `status: "not_found"` or `"error"`
- Follow-up job still runs (agent decides how to proceed)

### Max Snippet Rounds

If agent keeps requesting snippets beyond round 5:
- Runner stops creating follow-up snippet jobs
- Proceeds to patch phase or completion
- Check logs for: "max snippet rounds (5) reached"

---

## State Server / UI Verification

Throughout testing, verify the UI reflects accurate state:

**Access UI (via SSH tunnel from host):**
```sh
ssh -i ~/.go-synth/vm/id_ed25519 -p 2222 \
    -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
    -L 8787:127.0.0.1:8787 root@localhost -N &

# Open http://127.0.0.1:8787/ in browser
```

**Check:**
- [ ] Events timeline shows all stage transitions
- [ ] Bundle detail page shows all artifacts
- [ ] Triage.md renders with markdown formatting
- [ ] Patch.log renders with markdown formatting
- [ ] Rebuild proof renders (rebuild_proof.json)
- [ ] PR URL appears after Stage 4

**SSE Continuity:**
1. Note last event ID in UI
2. Refresh page
3. Verify events resume correctly (no duplicates, no gaps)

---

## Test Results Template

Copy and fill in after testing:

```
Test Date: YYYY-MM-DD
Test Port: net/hostapd
Tester: <name>

## Stage Results

| Stage | Status | Notes |
|-------|--------|-------|
| 1. Failure Capture | ☐ Pass / ☐ Fail | |
| 2. Triage | ☐ Pass / ☐ Fail | |
| 2a. Snippets | ☐ Pass / ☐ Fail / ☐ N/A | Rounds: ___ |
| 3. Patch + Rebuild | ☐ Pass / ☐ Fail | |
| 4. PR Job | ☐ Pass / ☐ Fail | PR: ___ |

## Error Paths Encountered

- [ ] Unpatchable classification
- [ ] Patch workspace failure
- [ ] Rebuild failure
- [ ] Snippet extraction failure
- [ ] Max snippet rounds

## Issues Found

1. ...

## State Server / UI

- [ ] Events accurate
- [ ] Artifacts viewable
- [ ] SSE continuity works
```

---

## Future Work

- [ ] **Automate this process** into `scripts/test-e2e` for regression testing
- [ ] Add automated assertions for each verification step
- [ ] Support running against multiple test ports
- [ ] Generate test report automatically
