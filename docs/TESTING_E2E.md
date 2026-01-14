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
│  3. PATCH GENERATION                                                     │
│     agent-queue-runner → analysis/patch.diff                             │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  4. APPLY & REBUILD                                                      │
│     apply-patch → branch + sync + dsynth just-build                      │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  5. PR CREATION                                                          │
│     gh pr create (only if rebuild succeeds)                              │
└─────────────────────────────────────────────────────────────────────────┘
```

## Prerequisites

### On the Host (Linux)

1. **opencode serve** running:
   ```sh
   opencode serve --hostname 0.0.0.0 --port 4097
   ```

2. **Safe clone** exists at `/home/antonioh/s/DeltaPorts-ai-fix`

3. **SSH access** to VM configured:
   ```sh
   export VM_SSH_HOST=root@localhost
   export VM_SSH_PORT=2222
   export VM_SSH_KEY=/home/antonioh/.go-synth/vm/id_ed25519
   ```

### On the VM (DragonFlyBSD)

1. **DeltaPorts scripts** deployed to `/build/synth/DeltaPorts/scripts/`

2. **dsynth hooks** installed in `/etc/dsynth/` or `/usr/local/etc/dsynth/`

3. **artifact-store daemon** running (required):
   ```sh
   /build/synth/DeltaPorts/scripts/artifact-store --logs-root /build/synth/logs &
   ```

4. **State Server** running in DB-only mode (optional, for UI verification):
   ```sh
   EVIDENCE_DB_ONLY=1 /build/synth/DeltaPorts/scripts/state-server --logs-root /build/synth/logs &
   ```

5. **Generator config** at `/usr/local/etc/dports.conf` with valid paths

## Test Execution

### Stage 1: Trigger Failure and Verify Evidence Capture

**On VM:**
```sh
dsynth test net/hostapd
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
ls -la /build/synth/logs/evidence/full-logs/<bundle_id>.full.log.gz

# Check job file
ls -la /build/synth/logs/evidence/queue/pending/
cat /build/synth/logs/evidence/queue/pending/*.job | head -20
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

### Stage 3: Patch Job Processing

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

**Verify:**
```sh
# Check patch output
curl -s http://127.0.0.1:8787/bundles/<bundle_id>/artifacts/analysis/patch.diff | head -40

# Validate diff format
curl -s http://127.0.0.1:8787/bundles/<bundle_id>/artifacts/analysis/patch.diff | head -20
```

**Expected:**
- `analysis/patch.diff` with valid unified diff format
- Diff modifies only `ports/net/hostapd/*` paths

**Error Case:** If `patch.diff.invalid` exists instead, the patch failed validation. Check contents for error details.

---

### Stage 4: Apply Patch to Safe Clone

Apply runs automatically via the `apply` job in the runner. The runner materializes a temp bundle directory from blobstore artifacts and calls `apply-patch` with it.

**Verify:**
```sh
# Check apply job exists/ran
ls -la /build/synth/logs/evidence/queue/done/ | head -5

# Check branch was created
cd /home/antonioh/s/DeltaPorts-ai-fix
git branch -a | grep ai-fix/net-hostapd

# Check commit
git log -1 --oneline

# Check files changed
git show --stat HEAD
```

**Expected:**
- Apply job ran and moved to `done/` or `failed/`
- Branch `ai-fix/net-hostapd-<bugslug>` created on success
- Commit with descriptive message

---

### Stage 5: Sync and Rebuild on VM

This is automated by `apply-patch`, but to verify manually:

**On VM:**
```sh
# Check branch was fetched
cd /build/synth/DeltaPorts
git fetch origin
git branch -a | grep ai-fix/net-hostapd

# Check sync result (already done by apply-patch)
ls -la /build/synth/DPorts/net/hostapd/

# Check rebuild status
curl -s http://127.0.0.1:8787/bundles/<bundle_id>/artifacts/analysis/rebuild_status.txt
```

**Expected:**
- Branch available on VM
- DPorts updated via `sync1.sh`
- `rebuild_status.txt` shows SUCCESS or FAILURE

---

### Stage 6: PR Creation

**Prerequisite:** Rebuild succeeded.

**Verify:**
```sh
# Check PR URL was recorded
curl -s http://127.0.0.1:8787/bundles/<bundle_id>/artifacts/analysis/pr_url.txt

# View PR on GitHub
# (URL from above)
```

**Expected:**
- PR created with proper format
- PR body includes triage analysis, changes list, rebuild confirmation

---

## Error Handling Paths

These may occur naturally during testing. Document what happens:

### Unpatchable Classification

If triage returns `missing-dep`, `fetch-error`, or `unknown`:
- No patch job will be auto-enqueued
- Check runner logs for: "not auto-enqueueing patch job"
- Human intervention required

### Invalid Patch Diff

If patch agent produces malformed diff:
- `patch.diff.invalid` created instead of `patch.diff`
- Job moves to `failed/`
- Check `<job>.error` file for details

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
- [ ] Patch.diff renders with syntax highlighting
- [ ] PR URL appears after Stage 6

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
| 3. Patch Generation | ☐ Pass / ☐ Fail | |
| 4. Apply Patch | ☐ Pass / ☐ Fail | |
| 5. Rebuild | ☐ Pass / ☐ Fail | |
| 6. PR Creation | ☐ Pass / ☐ Fail | PR: ___ |

## Error Paths Encountered

- [ ] Unpatchable classification
- [ ] Invalid patch diff
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
