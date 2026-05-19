# dsynth hooks for DeltaPorts

A single hook set that does **both** things you want for an agentic
DeltaPorts build:

1. **Failure evidence**: every `hook_pkg_failure` writes a full bundle
   (errors, log tail, port snapshot, gzipped full log) to
   `artifact-store` and enqueues a triage job for the agent harness.
2. **Build tracking**: every per-port event (`pkg_start`, `pkg_success`,
   `pkg_failure`, `pkg_skipped`, `pkg_ignored`) plus `run_start` /
   `run_end` reports to `dportsv3 tracker` so the cross-build dashboard
   reflects the build's progress in real time.

Tracker integration is opt-in via the config file
(`dportsv3-hooks.conf`). Without it the hooks still do the
artifact-store work; tracker calls are silent no-ops.

## Files

| File | Purpose |
|---|---|
| `hook_common.sh` | shared helpers (env defaults, artifact-store client wrappers, tracker integration, log distilling) |
| `dportsv3-hooks.conf.example` | config template — copy to `/etc/dsynth/dportsv3-hooks.conf` and edit |
| `hook_run_start` | initializes evidence root + starts a tracker build_run |
| `hook_run_end` | clears evidence pointer + finishes the tracker build_run |
| `hook_pkg_start` / `hook_pkg_started` | tracker mark-building (both names provided for dsynth-variant compat) |
| `hook_pkg_failure` | writes a full evidence bundle, enqueues a triage job, records `fail` in tracker |
| `hook_pkg_success` | records `pass` in tracker |
| `hook_pkg_skipped` | records `skipped` in tracker |
| `hook_pkg_ignored` | records `ignored` in tracker |

## Install

```sh
install -d /etc/dsynth
install -m 755 scripts/dsynth-hooks/hook_* /etc/dsynth/
install -m 755 scripts/dsynth-hooks/hook_common.sh /etc/dsynth/
install -m 644 scripts/dsynth-hooks/dportsv3-hooks.conf.example \
    /etc/dsynth/dportsv3-hooks.conf
```

Then edit `/etc/dsynth/dportsv3-hooks.conf` for at least:

- `DPORTSV3_TRACKER_URL` (or leave commented to disable tracker integration)
- `DPORTSV3_BIN` (absolute path to your `dportsv3` wrapper)

Defaults you usually don't need to override:

- `DPORTSV3_TRACKER_TARGET` derives from `${PROFILE}` (one profile per target)
- `DPORTSV3_TRACKER_BUILD_TYPE` defaults to `test` — set to `release` for builds you intend to publish
- `ARTIFACT_STORE_URL` defaults to `http://127.0.0.1:8788`

Make sure dsynth's `Hooks_Directory` points to `/etc/dsynth` (or
wherever you installed). dsynth picks up hooks by name; only one
executable per event name can exist there at a time.

## Operational notes

- Tracker outages don't fail dsynth. The hook logs the error to
  `DPORTSV3_TRACKER_HOOK_LOG` (default: `${DIR_LOGS}/dportsv3-hooks.log`)
  and exits 0.
- If `start-build` fails (typically because a prior build_run for the
  same `(target, build_type)` is still active — usually because dsynth
  was killed mid-run last time), tracker integration is disabled for
  *this* dsynth run rather than reusing a stale run id. The state file
  records `TRACKING_DISABLED=1`. Resolve by either:
  1. Manually finishing the stale run: `dportsv3 tracker finish-build --run N`
  2. Deleting the state file: `rm /path/to/state-dir/${PROFILE}.env`
- Per-profile active run state lives in
  `${DPORTSV3_TRACKER_STATE_DIR}/${PROFILE}.env`. Default location is
  under the evidence tree so it travels with the rest of the run data.
- Artifact-store, by contrast, is treated as required for
  `hook_pkg_failure` — if its health check fails, the hook exits
  non-zero (dsynth logs the failure, build continues but the failure
  bundle is lost).

## What landed where

This is the single canonical hook set. The earlier `scripts/builderhooks/`
directory (tracker-only hooks) has been folded in and removed. The
patterns from there — config-driven, per-profile state files,
soft-fail logging, disable-on-collision — are all preserved in
`hook_common.sh`.
