# Plan: DeltaPorts Build Tracker

## Overview

A live tracking server for DeltaPorts build results across multiple compose
targets (`@main`, `@2026Q1`, etc.). Supports parallel builds from multiple
machines reporting to a single server via HTTP API. Includes a read-only web
dashboard with auto-refresh for active builds.

The tracker is a recording/reporting layer only. Orchestration (PR triggers,
test builds, production builds, commits) happens externally. External scripts
call the CLI/API to record results.

## Architecture

```
Build Machine(s)                    Tracker Server
┌──────────────┐                   ┌─────────────────┐
│ build scripts │   HTTP/JSON      │  FastAPI         │
│ CLI client    │ ─────────────>   │  SQLite (WAL)    │
│               │ <─────────────   │  Jinja2 dashboard│
└──────────────┘                   └─────────────────┘
```

- **Server**: FastAPI + uvicorn, SQLite with WAL mode, Jinja2 templates, Pico CSS
- **CLI client**: `dportsv3 tracker` subcommands, HTTP via `urllib.request`
- **Dashboard**: server-rendered HTML pages, auto-refresh on active builds
- **No auth**: user handles externally

## Build Workflow Context

The tracker supports the following workflow (orchestrated externally):

1. **PR submitted** — contributor proposes port fixes
2. **Test build** — build scripts run a test build (`--type test`) to validate
3. **Production build** — if test passes, a real build (`--type release`) runs
4. **Commit** — on success, results are committed to the repo on the
   appropriate branch; commit info is recorded back to the tracker

Only one active build is allowed per `(target, build_type)` at a time.

## Data Model (4 tables)

```sql
CREATE TABLE build_types (
    name TEXT PRIMARY KEY   -- e.g. 'test', 'release'
);
INSERT INTO build_types(name) VALUES ('test'), ('release');

CREATE TABLE build_runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    target          TEXT NOT NULL,
    build_type      TEXT NOT NULL REFERENCES build_types(name),
    started_at      TEXT NOT NULL,
    finished_at     TEXT,
    commit_sha      TEXT,
    commit_branch   TEXT,
    commit_pushed_at TEXT
);

CREATE TABLE build_results (
    build_run_id  INTEGER NOT NULL REFERENCES build_runs(id),
    origin        TEXT NOT NULL,
    version       TEXT NOT NULL,
    result        TEXT NOT NULL,  -- 'success'|'failure'|'skipped'|'ignored'
    log_url       TEXT,
    recorded_at   TEXT NOT NULL,
    PRIMARY KEY (build_run_id, origin)
);

CREATE TABLE port_status (
    target                TEXT NOT NULL,
    origin                TEXT NOT NULL,
    last_attempt_version  TEXT,
    last_attempt_result   TEXT,
    last_attempt_at       TEXT,
    last_attempt_run_id   INTEGER REFERENCES build_runs(id),
    last_success_version  TEXT,
    last_success_at       TEXT,
    last_success_run_id   INTEGER REFERENCES build_runs(id),
    PRIMARY KEY (target, origin)
);

CREATE INDEX idx_build_runs_target ON build_runs(target);
CREATE INDEX idx_build_runs_active ON build_runs(target, build_type, finished_at);
CREATE INDEX idx_build_results_origin ON build_results(origin);
CREATE INDEX idx_port_status_target ON port_status(target);
CREATE INDEX idx_port_status_failures ON port_status(target, last_attempt_result);
```

### Concurrency constraint

Only one active (un-finished) build run is allowed per `(target, build_type)`.
`POST /api/builds` checks for an active run and returns **409 Conflict** if
one exists, including the active run's ID and started_at in the response.

### port_status upsert logic

When a result is recorded for `(target, origin)`:

- Always update: `last_attempt_version`, `last_attempt_result`,
  `last_attempt_at`, `last_attempt_run_id`
- On success only: also update `last_success_version`, `last_success_at`,
  `last_success_run_id`

The `*_run_id` columns link directly back to the specific build run that
produced each state.

## API Endpoints

### Write (called by CLI / build scripts)

| Method | Path                       | Body                              | Returns           |
|--------|----------------------------|-----------------------------------|-------------------|
| POST   | /api/builds                | {target, build_type, started_at?} | {id}              |
| PATCH  | /api/builds/{id}           | {finished_at?, commit_sha?, commit_branch?, commit_pushed_at?} | {ok} |
| POST   | /api/builds/{id}/results   | {results: [{origin, version, result, log_url?}]} | {recorded} |

POST to `/api/builds` returns 409 if an active run exists for the same
`(target, build_type)`.

POST to `/api/builds/{id}/results` upserts `port_status` server-side for
each result.

### Read (called by CLI / dashboard)

| Method | Path                       | Params                            | Returns           |
|--------|----------------------------|-----------------------------------|-------------------|
| GET    | /api/builds                | ?target=&build_type=&limit=       | [{build_run}]     |
| GET    | /api/builds/{id}           |                                   | {build_run, results[]} |
| GET    | /api/builds/compare        | ?a=&b=                            | {summary, buckets} |
| GET    | /api/status                | ?target=&origin=                  | [{port_status}]   |
| GET    | /api/failures              | ?target=                          | [{port_status}]   |
| GET    | /api/diff                  | ?a=&b=                            | {only_a[], only_b[], differ[]} |

### Build comparison (`GET /api/builds/compare?a=3&b=5`)

Compares two build runs (same or different targets). Returns:

```json
{
  "run_a": {"id": 3, "target": "@2026Q1", "build_type": "release", "started_at": "..."},
  "run_b": {"id": 5, "target": "@2026Q1", "build_type": "release", "started_at": "..."},
  "summary": {
    "new_successes": 12,
    "new_failures": 3,
    "still_failing": 45,
    "still_succeeding": 31200,
    "added": 8,
    "removed": 2,
    "version_changes": 340
  },
  "new_successes": [{"origin": "...", "version_a": "...", "result_a": "failure", "version_b": "...", "result_b": "success"}],
  "new_failures": [...],
  "still_failing": [...],
  "added": [...],
  "removed": [...],
  "version_changes": [{"origin": "...", "version_a": "...", "version_b": "...", "result_b": "success"}]
}
```

Buckets:
- **new_successes**: failed in run A, succeeded in run B (fixes)
- **new_failures**: succeeded in run A, failed in run B (regressions)
- **still_failing**: failed in both runs
- **still_succeeding**: succeeded in both (count only in summary, omitted
  from detail to keep response small)
- **added**: in run B but not in run A (new ports attempted)
- **removed**: in run A but not in run B (ports no longer attempted)
- **version_changes**: same port in both runs but different version
  (regardless of result)

## Dashboard

### Styling

**CSS**: Pico CSS (~10KB, vendored in `tracker/static/pico.min.css`) plus a
small `tracker/static/custom.css` for DeltaPorts-specific tweaks (failure
highlighting in red, success in green, running status in amber).

**Interactivity**: No JavaScript framework. `<details>/<summary>` for
expandable sections, `<form>` for filters/search, server-side pagination.

### Pages

| Path                            | Page                                  |
|---------------------------------|---------------------------------------|
| /                               | Target overview (totals, last build)  |
| /target/{target}                | Port list with filters, pagination    |
| /target/{target}/{cat}/{port}   | Single port detail + recent history   |
| /builds                         | Recent build runs across all targets  |
| /builds/{id}                    | Single build run detail               |
| /builds/compare?a=...&b=...    | Build-to-build comparison             |
| /diff?a=...&b=...              | Cross-target comparison               |

### Page Designs

**`/` — Target Overview**
```
Target    | Total  | Pass   | Fail  | Last Build
──────────┼────────┼────────┼───────┼───────────
@main     | 32,450 | 31,200 | 1,250 | 2026-03-15
@2026Q1   | 32,410 | 31,100 | 1,310 | 2026-03-14
```
Each target row links to `/target/{t}`. Fail count red-tinted.

**`/target/{target}` — Port List**
Filter dropdown (All/Failures/Successes), search box, paginated table:
`Origin | Version | Result | Last Success`. Failure rows highlighted.
Each origin links to port detail. Page size: 100 ports per page.

**`/target/{target}/{cat}/{port}` — Port Detail**
Header: current status, last attempt version/run, last success version/run.
Build history table: `Run | Type | Version | Result | Log | Date`.
Run IDs link to `/builds/{id}`. Log column shows "View log" link where
`log_url` is set.

**`/builds` — Build Run List**
Table: `Run | Target | Type | Started | Finished | Pass/Fail | Compare`.
Active (un-finished) runs show "running..." with elapsed time in amber.
Compare link per row auto-resolves to previous run of same
`(target, build_type)`.

**`/builds/{id}` — Single Build Detail**
Header: target, build_type, started/finished timestamps, duration.
Commit info block (sha, branch, pushed_at) shown when set.
Running count while active: "742 success, 505 failure — 1,247 recorded".
Auto-refresh via `<meta http-equiv="refresh" content="10">` while build is
active (tag omitted once finished).
Result table with filter dropdown (All/Failures/Successes).
Log links per result where `log_url` is set.

**`/builds/compare?a=&b=` — Build Comparison**
Header: "Comparing run #A vs #B (target, type)".
Summary counts table + expandable `<details>` sections for each bucket.
New failures expanded by default (most urgent). Still_succeeding shows
count only.

**`/diff?a=&b=` — Cross-Target Diff**
Form to pick two targets from dropdown. Table showing ports that differ
in status or version between targets: `Origin | Target A | Target B`.

## CLI Commands

```
dportsv3 tracker serve [--port 8080] [--db PATH]
dportsv3 tracker start-build --target T --type TYPE [--server URL]
dportsv3 tracker finish-build --run ID [--commit-sha SHA --commit-branch BRANCH --commit-pushed-at TS] [--server URL]
dportsv3 tracker record-result --run ID --origin O --version V --result R [--log-url URL] [--server URL]
dportsv3 tracker status [--target T] [--origin O] [--server URL]
dportsv3 tracker failures --target T [--server URL]
dportsv3 tracker diff TARGET_A TARGET_B [--server URL]
dportsv3 tracker show-build --run ID [--server URL]
dportsv3 tracker compare-builds RUN_A RUN_B [--server URL]
```

All query commands support `--json`.
`--server` defaults to env `DPORTSV3_TRACKER_URL`.

### compare-builds human output

```
Comparing @2026Q1 release run 3 (2026-03-10) vs run 5 (2026-03-14)
New successes (fixes):       12
New failures (regressions):   3
  devel/bar 2.0, www/baz 1.5, lang/qux 3.1
Still failing:               45
Still succeeding:         31200
Added:                        8
Removed:                      2
Version changes:            340
```

New failures are listed inline (most urgent information).

## Project Structure

New files under `scripts/generator/dportsv3/`:

```
tracker/
  __init__.py
  db.py                 -- schema init, migrations, all DB query functions
  models.py             -- pydantic models for API request/response
  server.py             -- FastAPI app: API routes + dashboard routes
  client.py             -- HTTP client functions (used by CLI commands)
  static/
    pico.min.css        -- vendored Pico CSS
    custom.css          -- DeltaPorts-specific tweaks
  templates/
    base.html           -- layout with nav, conditional auto-refresh
    index.html          -- target overview
    target.html         -- port list for one target
    port_detail.html    -- single port history
    builds.html         -- build run list
    build_detail.html   -- single build run (auto-refresh while active)
    build_compare.html  -- build-to-build comparison
    diff.html           -- cross-target diff
commands/
  tracker.py            -- CLI handler: cmd_tracker(args)
cli.py                  -- register tracker subparser + subcommands
```

## Dependencies (new)

Add to `pyproject.toml` as optional dependency group:

```toml
[project.optional-dependencies]
tracker = ["fastapi", "uvicorn[standard]", "jinja2"]
dev = ["pytest", "mypy", "httpx"]  # httpx for FastAPI TestClient
```

The tracker server deps are optional so the base `dportsv3` tool (compose,
DSL, migrate) stays dependency-free. `pip install -e ".[tracker]"` on
machines that need the server.

CLI client uses `urllib.request` only (no extra dependencies).

## Implementation Steps

### Step 1: DB layer + models (`tracker/db.py` + `tracker/models.py`)

DB functions (all take `sqlite3.Connection`, return plain dicts, no ORM):

- `init_db(db_path) -> Connection` — create tables if not exist, enable WAL,
  seed `build_types` with ('test', 'release')
- `get_active_run(conn, target, build_type) -> dict | None`
- `create_build_run(conn, target, build_type, started_at) -> int`
- `finish_build_run(conn, run_id, finished_at, commit_sha=None, commit_branch=None, commit_pushed_at=None)`
- `record_results(conn, run_id, target, results: list[dict])` — inserts into
  `build_results` (including `log_url`) + upserts `port_status` (including `*_run_id`)
- `get_build_run(conn, run_id) -> dict`
- `list_build_runs(conn, target=None, build_type=None, limit=20) -> list[dict]`
- `get_build_results(conn, run_id) -> list[dict]`
- `get_port_status(conn, target=None, origin=None) -> list[dict]`
- `get_failures(conn, target) -> list[dict]`
- `get_diff(conn, target_a, target_b) -> dict`
- `get_target_summary(conn) -> list[dict]`
- `compare_builds(conn, run_id_a, run_id_b) -> dict`

Pydantic models:

- `StartBuildRequest(target, build_type, started_at?)`
- `StartBuildResponse(id)`
- `FinishBuildRequest(finished_at?, commit_sha?, commit_branch?, commit_pushed_at?)`
- `ResultItem(origin, version, result, log_url?)`
- `RecordResultsRequest(results: list[ResultItem])`
- `RecordResultsResponse(recorded: int)`
- `BuildRunOut(id, target, build_type, started_at, finished_at, commit_sha, commit_branch, commit_pushed_at, result_count?)`
- `PortStatusOut(target, origin, last_attempt_version, ..., last_attempt_run_id, ..., last_success_run_id)`
- `DiffOut(only_a, only_b, differ)`
- `BuildCompareOut(run_a, run_b, summary, new_successes, new_failures, still_failing, added, removed, version_changes)`

### Step 2: Tests for DB layer (`tests/test_tracker_db.py`)

Unit tests for all DB functions with in-memory SQLite:

- Basic CRUD: create/finish build run, record results, get status
- Active run enforcement: second create for same (target, type) fails
- port_status upsert: success updates both attempt+success fields, failure
  updates only attempt fields
- compare_builds: overlapping ports, fixes, regressions, added, removed,
  version changes, empty runs, cross-target
- get_target_summary: counts, last build time
- get_diff: cross-target comparison
- get_failures: filter by target

### Step 3: FastAPI server (`tracker/server.py`)

- `create_app(db_path) -> FastAPI`
- Startup: `init_db()`, store connection in app state
- Mount static files
- Mount Jinja2 templates
- API routes (all endpoints from tables above, including 409 enforcement)
- Dashboard routes (all pages from table above)
- Shutdown: close connection

### Step 4: Tests for API (`tests/test_tracker_api.py`)

Integration tests using FastAPI TestClient against in-memory DB:

- Start build, record results, finish build round trip
- 409 conflict on duplicate active build
- Compare endpoint
- List/filter endpoints with build_type parameter
- Finish build with commit info

### Step 5: HTTP client (`tracker/client.py`)

`urllib.request` wrappers for all API endpoints:

- `start_build(server_url, target, build_type) -> int`
- `finish_build(server_url, run_id, commit_sha=None, commit_branch=None, commit_pushed_at=None)`
- `record_result(server_url, run_id, origin, version, result, log_url=None)`
- `record_results_batch(server_url, run_id, results: list[dict])`
- `get_status(server_url, target=None, origin=None) -> list[dict]`
- `get_failures(server_url, target) -> list[dict]`
- `get_diff(server_url, target_a, target_b) -> dict`
- `get_build(server_url, run_id) -> dict`
- `list_builds(server_url, target=None, build_type=None, limit=20) -> list[dict]`
- `compare_builds(server_url, run_id_a, run_id_b) -> dict`

All raise on HTTP errors with clear messages.

### Step 6: CLI commands (`commands/tracker.py` + `cli.py`)

- `cmd_tracker(args)` dispatcher
- One handler per subcommand
- Server URL: `args.server` or `DPORTSV3_TRACKER_URL` env or error
- `--json` flag for query commands
- `--type` required on start-build
- `--commit-sha`, `--commit-branch`, `--commit-pushed-at` optional on finish-build
- `--log-url` optional on record-result
- `--build-type` optional filter on list commands
- Human formatting for compare-builds shows summary + inline new failures

### Step 7: Dashboard templates + static assets

Vendored Pico CSS + custom.css. 8 Jinja2 templates:

- `base.html` — nav bar, conditional auto-refresh meta tag
- `index.html` — target overview table
- `target.html` — paginated port list with filter/search
- `port_detail.html` — port status + build history
- `builds.html` — build run list with compare links
- `build_detail.html` — single build, auto-refresh while active
- `build_compare.html` — comparison with expandable sections
- `diff.html` — cross-target diff with target picker form

### Step 8: pyproject.toml

```toml
[project.optional-dependencies]
tracker = ["fastapi", "uvicorn[standard]", "jinja2"]
dev = ["pytest", "mypy", "httpx"]
```

### Step 9: Integration test

CLI -> server -> DB round trip test. Full lifecycle: start build, record
results, finish build with commit info, query status, compare builds.

### Step 10: Documentation

Tracker section in user guide and architecture docs.

## Implementation Order

1. db.py + models.py (foundation, testable standalone)
2. tests/test_tracker_db.py (validate DB layer including compare_builds)
3. server.py API routes (no dashboard yet)
4. tests/test_tracker_api.py (validate API including /api/builds/compare)
5. client.py (HTTP client)
6. commands/tracker.py + cli.py registration
7. Dashboard templates + static assets (can be iterative)
8. pyproject.toml + dependency management
9. Integration test: CLI -> server -> DB round trip
10. Documentation

## Notes

- SQLite WAL mode handles concurrent readers + single writer. Server
  serializes writes. Multiple build machines POST results but server
  processes them sequentially — fine at this scale.
- `port_status` upsert in a single transaction. `*_run_id` columns updated
  atomically with version/result fields.
- `compare_builds` uses LEFT JOIN + UNION (SQLite lacks FULL OUTER JOIN).
- Dashboard is deliberately simple. No SPA, no websockets. Auto-refresh via
  meta tag on active builds only. Can add htmx later.
- The tracker is a separate concern from compose. Compose does not know about
  the tracker. External build scripts are the integration point.
- Build comparison works across targets and build types.
- Log serving/decompression is deferred. `log_url` column is present for
  future use — links rendered as plain hrefs.
