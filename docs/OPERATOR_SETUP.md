# Operator setup — agentic DeltaPorts builds, zero to running

This is the single walkthrough from a fresh DragonFly box to a live
agentic dsynth build with the tracker UI open. For architecture see
`docs/AGENTIC_BUILDS.md`; for testing see `docs/TESTING_E2E.md`.

## 1. System prerequisites

DragonFly packages required by the generator + tracker venv:

```sh
pkg install py311-sqlite3 py311-pydantic2 py311-pydantic-core \
            py311-fastapi py311-uvicorn py311-watchfiles \
            py311-uvloop py311-httptools py311-websockets \
            py311-python-dotenv
```

(These keep us off Rust-built wheels — the venv is set up with
`--system-site-packages` to pick them up.)

You also need `dsynth` itself, of course, and a profile set up
(`/usr/local/etc/dsynth/<profile>-make.conf` etc).

## 2. Clone + bootstrap

```sh
cd /build/synth                 # or wherever you keep build trees
git clone https://github.com/.../DeltaPorts.git
cd DeltaPorts
./dportsv3 --help               # first run builds the venv at scripts/generator/.venv
```

The wrapper script:
- creates `scripts/generator/.venv/` with `--system-site-packages`
- `pip install -e scripts/generator` (editable, source changes pick up live)
- caches a stamp so subsequent calls skip re-install

If you also want pytest + mypy in there:

```sh
scripts/generator/.venv/bin/pip install -e 'scripts/generator[dev]'
```

## 3. Create a dev-env for your target

The dev-env is a chroot with a writable copy-on-write overlay where
the agent edits files. One per target/origin you want to iterate on.

```sh
./dportsv3 dev-env create myenv --target @2026Q2 --origin devel/foo
./dportsv3 dev-env status myenv     # expect: status=ready, backend=chroot, root_mounted=true
```

`dev-env path myenv --writable` will print the overlay path
(`/var/cache/dports-dev/myenv/writable`) — that's where the agent's
dirty edits land.

For details (mounts, FPORTS pinning, materialization), see
`docs/dev-chroot-environment.md`.

## 4. Install dsynth hooks

```sh
./dportsv3 hooks install
```

This copies the hook scripts + `dportsv3-hooks.conf.example` →
`dportsv3-hooks.conf` into `/etc/dsynth/`. Existing
`dportsv3-hooks.conf` is preserved (pass `--force` to overwrite).

Edit `/etc/dsynth/dportsv3-hooks.conf` and set:

```sh
ARTIFACT_STORE_URL=http://127.0.0.1:8788
DPORTSV3_TRACKER_URL=http://127.0.0.1:8080
DPORTSV3_TRACKER_TARGET=@2026Q2     # defaults from $PROFILE if unset
DPORTSV3_BIN=/build/synth/DeltaPorts/dportsv3
```

Verify with:

```sh
./dportsv3 hooks status
```

Shows which hooks are present, whether they're executable, and
whether any are stale vs. the in-repo source.

## 5. Configure env for the services

Pick a logs root that artifact-store + tracker share:

```sh
LOGS_ROOT=/build/synth/logs
STATE_DB=$LOGS_ROOT/evidence/state.db
ARTIFACT_ROOT=$LOGS_ROOT/evidence
```

LLM credentials — pick a provider for each phase:

```sh
export DP_HARNESS_TRIAGE_MODEL=deepseek/deepseek-v4-flash
export DP_HARNESS_TRIAGE_API_KEY=...
export DP_HARNESS_PATCH_MODEL=anthropic/claude-sonnet-4
export DP_HARNESS_PATCH_API_KEY=...
```

For DeepSeek thinking-mode the harness keeps `reasoning_content` on
all turns — works out of the box, no extra config.

## 6. Start the three services

In three separate shells or under your service manager of choice:

```sh
# Shell A — artifact-store (receives bundles, writes state.db + blobs)
./dportsv3 artifact-store --logs-root $LOGS_ROOT

# Shell B — tracker (UI + read API + SSE)
DPORTSV3_STATE_DB=$STATE_DB \
DPORTSV3_ARTIFACT_ROOT=$ARTIFACT_ROOT \
  ./dportsv3 tracker serve --port 8080

# Shell C — queue runner (claims jobs, runs triage/patch)
DPORTSV3_STATE_DB=$STATE_DB \
DPORTSV3_TRACKER_URL=http://127.0.0.1:8080 \
ARTIFACT_STORE_URL=http://127.0.0.1:8788 \
  scripts/agent-queue-runner
```

Order doesn't matter; each is idempotent on schema init. Open
`http://localhost:8080/` in a browser and confirm the dashboard
loads (it'll be empty until a build runs).

## 7. Run a build

```sh
dsynth -p 2026Q2 -S -y build devel/known-failing-port
```

The dsynth profile must source `/etc/dsynth/dportsv3-hooks.conf` for
the hooks to fire. With hooks live, watch:

- `http://localhost:8080/target/@2026Q2` — the dsynth-progress view
  updates as builders move through phases. Failed ports appear with a
  red pill.
- `http://localhost:8080/agentic/bundles?target=@2026Q2` — failure
  bundles as they upload.
- `http://localhost:8080/agentic/jobs?target=@2026Q2&state=pending`
  — triage jobs as the runner picks them up.

## 8. Inspect a result

For a job that landed `rebuild_ok=true`:

```sh
curl -s http://localhost:8080/api/bundles/<bundle_id> | python3 -m json.tool
```

The `artifacts` array points at `analysis/triage.md`,
`analysis/patch_audit.json`, `analysis/rebuild_proof.json`, and
`analysis/changes.diff`. Each is streamable from
`/api/bundles/<id>/artifacts/<relpath>`.

The actual edits live in the dev-env's writable overlay:

```sh
ENV_DIR=$(./dportsv3 dev-env path myenv --writable)
git -C $ENV_DIR/work/DeltaPorts diff
```

If you accept the change, apply that diff in your own DeltaPorts
clone, review, sign, and commit there. The agentic loop never
touches your authoritative working tree.

## Common stumbles

| Symptom | Fix |
|---|---|
| `dportsv3` says "missing DragonFly packages" | install the `pkg install` list from §1 |
| Hooks don't fire on failure | check `/etc/dsynth/dportsv3-hooks.conf` is sourced by the profile; `./dportsv3 hooks status` for stale/missing |
| Tracker 500s on artifact stream | `DPORTSV3_ARTIFACT_ROOT` doesn't match `--logs-root`/evidence on the artifact-store |
| Triage 401s | provider key wrong, or `DP_HARNESS_TRIAGE_API_BASE` needs to be set for non-default endpoints |
| Patch loop stops with `budget-exhausted` | check trust tier classification in `analysis/triage.md`; consider bumping the tier in `config/agentic-policy.json` |
| Runner sees no jobs after a failure | check `bundles` row exists in `state.db` (hook side); check classification didn't resolve to MANUAL |

## Upgrading

When you pull new code:

```sh
git pull
./dportsv3 hooks status     # are any hooks stale vs. the new source?
./dportsv3 hooks install    # re-copy (config preserved)
```

Then restart the tracker (uvicorn doesn't auto-reload templates or
static files). Artifact-store + runner read state.db schema on
startup; migrations are idempotent.
