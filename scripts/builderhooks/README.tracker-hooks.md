# dsynth tracker hooks

These hooks connect dsynth build events to the `dportsv3 tracker` server.

Files:

- `tracker_common.sh`: shared helper logic
- `dportsv3-tracker.conf.example`: config template
- `hook_run_start`, `hook_run_end`: tracker run lifecycle
- `hook_pkg_start` / `hook_pkg_started`: enqueue + mark building
- `hook_pkg_success`, `hook_pkg_failure`, `hook_pkg_ignored`,
  `hook_pkg_skipped`: final result recording

Install pattern:

```bash
install -d /etc/dsynth
install -m 755 scripts/builderhooks/hook_* /etc/dsynth/
install -m 755 scripts/builderhooks/tracker_common.sh /etc/dsynth/
install -m 644 scripts/builderhooks/dportsv3-tracker.conf.example \
  /etc/dsynth/dportsv3-tracker.conf
```

Then edit `/etc/dsynth/dportsv3-tracker.conf` for:

- `DPORTSV3_BIN`
- `DPORTSV3_TRACKER_URL`
- `DPORTSV3_TRACKER_TARGET`
- `DPORTSV3_TRACKER_BUILD_TYPE`

Notes:

- hooks log failures but exit successfully so dsynth builds continue if the
  tracker is unavailable
- `hook_pkg_start` and `hook_pkg_started` are both provided to cover local
  dsynth variants
- per-port start hooks enqueue the port before calling `mark-building`, which
  matches tracker DB expectations
- if `start-build` fails (for example because an active run already exists for
  the same target/build type), tracking is disabled for the rest of that dsynth
  run instead of reusing a stale run id from a previous build
