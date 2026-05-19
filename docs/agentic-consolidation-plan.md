# Phase 4 — Consolidate state-server + tracker onto one DB

> **Phases 1–3 shipped.** Phase 1 (`dportsv3 dev-env exec`), Phase 2
> (`apply-patch` + `process_apply_job` retired), and Phase 3 (opencode
> replaced by `dportsv3.agent`) all landed in the
> `agentic-dsynth-evidence-hooks` branch. Their commits are the
> authoritative record; this document only carries the *current* plan.

## Goal

Today the agentic side runs on `state-server` (stdlib `http.server`,
~1500 LOC) reading/writing `state.db`, while build tracking runs on
the FastAPI `dportsv3 tracker serve` against a separate `tracker.db`.
Two processes, two DBs, two SPAs, overlapping concerns.

Phase 4 collapses this into:

- **One DB**: `state.db` (the existing one — `tracker.db` retires).
  Artifact-store writes failure evidence; tracker writes build runs +
  ports status. SQLite WAL + `busy_timeout=5000` + `foreign_keys=ON`
  on every connection lets both writers share the file safely.
- **One serve process** for the read/UI surface: the FastAPI tracker
  absorbs state-server's read endpoints, then state-server +
  `state-server-ui` retire.
- **One hook set**: `dsynth-hooks/` writes both the artifact-store
  failure bundle *and* the tracker run state on every event.

## Status

| Step | What | State |
|---|---|---|
| 1 | Artifact-store grows tracker tables in `state.db` schema | shipped |
| 1.5 | Artifact-store extracted into `dportsv3.artifact_store` (shim retained) | shipped |
| 2 | `POST /v1/user-context` on artifact-store | shipped |
| 3a | `dsynth-hooks/` + `builderhooks/` merged into one hook set | shipped |
| 4a | `dportsv3 dev-env update` + cache bind-mount inside chroot | shipped |
| 4 | Tracker reads/writes `state.db`; `tracker.db` retires | shipped (26ac0ae1e03) |
| 5 | Tracker agentic-read endpoints + target column on bundles/jobs/runs | shipped (8797550a6ac) |
| 6 | Tracker HTML views (bundle detail, jobs queue, runner) | shipped (fdc7528a24f) |
| 8 | Decommission `state-server` + `state-server-ui` SPA | shipped |

> **Phase 4 complete.** One DB (`state.db`), one serve process
> (FastAPI tracker), one hook set. Agent queue runner now points
> exclusively at the tracker for bundle/artifact lookups via
> `DPORTSV3_TRACKER_URL` (default `http://127.0.0.1:8080`).

(There is no step 7 — slot reserved during planning, folded into 6/8.)

## Step 5 — agentic read endpoints + target column

Two coupled changes ship together because the endpoints exist to
expose target-scoped views; landing the endpoints without the target
column would harden a known gap that step 8 then can't undo without
schema migration on top of decommission.

### Schema additions

`dportsv3.db.schema` gains (idempotent — applied via `MIGRATIONS`,
not `SCHEMA`, since legacy rows exist):

```sql
ALTER TABLE bundles ADD COLUMN target TEXT;
ALTER TABLE jobs    ADD COLUMN target TEXT;
ALTER TABLE runs    ADD COLUMN target TEXT;
CREATE INDEX IF NOT EXISTS idx_bundles_target ON bundles(target);
CREATE INDEX IF NOT EXISTS idx_jobs_target    ON jobs(target);
CREATE INDEX IF NOT EXISTS idx_runs_target    ON runs(target);
```

Target is nullable: pre-step-5 rows stay `NULL` and surface as target
`unknown` in filtered queries. Going forward every new row is
written with a target.

### Hook + writer changes

`scripts/dsynth-hooks/hook_common.sh` already knows the target — it
defaults `DPORTSV3_TRACKER_TARGET` from `$PROFILE`. The bundle upload
to artifact-store carries it in the POST body. Artifact-store writes
it on the `bundles` row (and on the `runs` row when creating one).
`scripts/agent-queue-runner` writes target on the `jobs` row when
enqueueing.

### Endpoints to port from state-server

All read; all live in `dportsv3/tracker/server.py` next to the
existing routes. Per-request SQLite connections (matching the
a14fe9c4dab pattern).

| state-server route | tracker route | target filter? |
|---|---|---|
| `GET /health` | `GET /api/health` | no |
| `GET /status` | `GET /api/agentic-status` | no (global counts) |
| `GET /activity` | `GET /api/activity?limit=N` | `&target=` |
| `GET /runner-status` | `GET /api/runner-status` | no |
| `GET /runs`, `/runs/<id>` | `GET /api/runs[?target=]`, `/api/runs/{id}` | yes |
| `GET /jobs?state=`, `/jobs/<id>` | `GET /api/jobs[?state=&target=]`, `/api/jobs/{id}` | yes |
| `GET /bundles`, `/bundles/<id>` | `GET /api/bundles[?target=&origin=]`, `/api/bundles/{id}` | yes |
| `GET /ports/<origin>` | `GET /api/ports/{origin}[?target=]` | yes |
| `GET /bundles/<id>/artifacts/<relpath>` | `GET /api/bundles/{id}/artifacts/{relpath:path}` | no |
| `GET /events` (SSE) | `GET /api/events[?target=]` (SSE) | yes |

`user-context*` stays on artifact-store (write path, step 2 settled
it there).

### Artifact root discovery

`GET /api/bundles/{id}/artifacts/...` streams from artifact-store's
`blob_root`. The tracker doesn't know that path today. Resolution:
`DPORTSV3_ARTIFACT_ROOT` env var on the tracker process, defaulting
to the artifact-store default (`/build/synth/logs/evidence`).
Symmetric with `DPORTSV3_STATE_DB`.

### SSE on FastAPI

State-server hand-rolls SSE on `http.server`. Tracker uses
`StreamingResponse` + async generator that:
- Polls `events` table on a 1s tick.
- Tracks last-seen `id` per connection.
- Emits each new row as `event: <type>\ndata: <json>\n\n`.
- Filters by target if `?target=` is supplied.

Async polling is fine here — single FastAPI process, low event rate.
No LISTEN/NOTIFY, no external pub/sub.

### Tests

One pytest module per route group under `scripts/generator/tests/`
(`test_tracker_agentic_runs.py`, `test_tracker_agentic_bundles.py`,
`test_tracker_agentic_jobs.py`, `test_tracker_agentic_events.py`).
Each seeds `state.db` with rows including a mix of target values
(some `NULL` to verify legacy-row tolerance) and asserts:
- response shape matches the state-server equivalent (no field-name
  drift),
- `?target=` filtering works,
- `NULL`-target rows surface only when no filter is set.

### LOC estimate

| Area | Added | Notes |
|---|---|---|
| `dportsv3/db/schema.py` migrations | ~10 | three ALTERs + three indexes |
| `dportsv3/tracker/server.py` routes | ~350 | ten read endpoints + SSE |
| `dportsv3/tracker/queries.py` (new) | ~150 | agentic-read SQL helpers |
| `dportsv3/artifact_store.py` target writes | ~20 | bundle/run insert paths |
| `scripts/dsynth-hooks/hook_common.sh` | ~5 | target in POST body |
| `scripts/agent-queue-runner` job-enqueue paths | ~10 | target in jobs insert |
| tests | ~250 | one module per route group |
| **Total** | **~795** | |

No deletions in this step. State-server keeps running in parallel
until step 8.

## Step 6 — HTML views

Add target-scoped views to the tracker:

- `/agentic/bundles[?target=]` — bundle list
- `/agentic/bundles/{id}` — bundle detail with linked artifacts
- `/agentic/jobs[?target=&state=]` — job queue
- `/agentic/runner` — runner heartbeat / status

Templates live next to existing `dportsv3/tracker/templates/`.
Server-rendered, no SPA. Style follows the existing tracker
dashboard.

## Step 8 — decommission state-server + state-server-ui

- Delete `scripts/state-server` (~1500 LOC).
- Delete `scripts/state-server-ui/` (the React SPA).
- Remove the systemd/rc unit references in `docs/dportsv3-user-guide.md`.
- Update any docs/scripts that point at the state-server port.
- One follow-up commit; predicated on step 5 + step 6 being in
  production for at least one build cycle.

## Out of scope (Phase 4)

- Tracker UI redesign — Phase 5.
- Authn/authz on agentic-read endpoints — operator-internal for now.
- Migration of historical `state.db` rows on machines that already
  ran pre-step-5: those rows keep `target = NULL` and surface in
  unfiltered views. No backfill.

## Verification

End-to-end check after step 5 lands:

1. `dportsv3 dev-env exec foo -- /etc/dsynth/05_failed.sh ...` triggers
   the hook, uploading a bundle to artifact-store with
   `target=$PROFILE` in the POST body.
2. `sqlite3 state.db 'select bundle_id, target from bundles order by
   id desc limit 1;'` shows the new row with non-NULL target.
3. `curl tracker:8080/api/bundles?target=@2026Q2` returns it.
4. `curl tracker:8080/api/bundles?target=@main` excludes it.
5. `curl -N tracker:8080/api/events?target=@2026Q2` streams the
   `bundle_upserted` event for the same upload.
6. Same checks against `state-server`'s legacy endpoints still pass
   (parallel operation until step 8).
