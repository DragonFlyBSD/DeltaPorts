# Agentic framework — plan (rolling ledger)

Rolling-ledger format: shipped phases summarized at top, current phase
detail below. Full arc and rationale in
`agentic-framework-design.md`; overview of remaining phases at the
bottom of this file.

---

## Shipped phases

### Phase 1 — Lifecycle (shipped 2026-05-20)

Typed `JobState` + `JobEvent` enums and transition table backed by a
new `job_events` table. `lifecycle.apply()` is the single entry point
for state changes; `current()`, `history()`, and `reap_orphans()`
round out the module. Runner cutover deleted `_post_job_upsert` and
the artifact-store's `upsert_job`; the `/v1/jobs/upsert` endpoint
renamed to `/v1/jobs/transition`. UI templates + count queries
updated for typed values. As a bonus refactor, the 2330-LOC
`scripts/agent-queue-runner` script became a 23-line shim over the
new `dportsv3.agent.runner` module, dropping the `execv` workaround
in `dportsv3 agent-queue-runner` and making the runner internals
importable for tests.

Commits: `42df53620f6` (schema) · `71ed4a38945` (lifecycle module) ·
`407795d1793` (cutover) · `365344cc329` (runner module move) ·
`164df2bb8b2` (e2e tests + policy-path fix).

Test delta: +26 tests (18 lifecycle unit + 4 import smoke + 4 e2e
integration). 275 total green.

---

## Current phase: Phase 2 — Health / readiness

> **Goal:** replace the `env_broken` stderr regex with a structured
> `EnvHealth.check()` probe that all entry points (runner gate, dev-
> env CLI, and — when Phase 5 lands — every Step's precheck) consult.
> Health becomes a first-class, named-aspect concern instead of
> "guess from a tool stderr."

### Decisions captured up front

- Health is **probed directly**, not inferred from tool errors. The
  current "scan stderr for sentinel" path is a band-aid; once we
  probe, we refuse to start jobs against an unhealthy env in the
  first place.
- Three named checks land in this phase, picked because they've each
  already burned us: `python_runtime` (the gnome_subr disaster),
  `writable_overlay`, and `dports_compose`. Adding more checks later
  is one function per check.
- Probe results are **cached briefly** in the runner (default 60s)
  to avoid hammering the chroot on every poll. CLI invocations
  always probe fresh.
- Yolo cutover continues: when health lands, the `_env_broken_reason`
  module global, `_classify_env_error`, the `_ENV_BROKEN_SENTINELS`
  tuple, and the `error_category=env_broken` field on tool results
  all go away in the same step that wires the new probe in.

### Pre-conditions

- Phase 1 manual smoke against a real dsynth run on dfly. Yes,
  optional in theory; in practice "the spine just changed" warrants
  one end-to-end verification before piling more on top.
- `dportsv3 dev-env exec NAME -- pkg query %n` works on the operator's
  env (we'll lean on `pkg` for the `python_runtime` check).

### The three checks

```
python_runtime   pkg-query that py311-{sqlite3,pydantic2,pydantic-core,
                 fastapi,uvicorn,watchfiles,uvloop,httptools,websockets,
                 python-dotenv} are installed in the chroot.
                 Broken if any is missing; operator_action gives the
                 exact `pkg install ...` command.

writable_overlay The env's writable overlay path
                 (dportsv3 dev-env path NAME --writable) is mounted
                 and writable. Touch-test a sentinel file under
                 work/.health/.
                 Broken if the dir isn't there or the touch fails.

dports_compose   dportsv3 compose --check (or equivalent dry-run)
                 succeeds inside the env. This is the canary that
                 catches the gnome_subr-style failure (compose was
                 the one thing that died there).
                 Broken if the subprocess returns non-zero with the
                 known py311-deps stderr.
```

Aggregate `EnvHealth.status`:
- `broken`  if any check is broken
- `degraded` if any warn and none broken  *(no `warn` check in Phase 2;
  reserved for later additions)*
- `ready`    otherwise

### Step 1 — `health.py` module

**Goal:** the module + dataclasses + three concrete checks. Pure
logic + subprocess calls to dev-env. No runner integration yet.

**Files:**
- `scripts/generator/dportsv3/agent/health.py` — new
- `scripts/generator/tests/test_health.py` — new

**Interface:**

```python
from dataclasses import dataclass, field
from typing import Literal

@dataclass
class HealthCheck:
    name: str
    status: Literal["ok", "warn", "broken"]
    detail: str = ""
    operator_action: str | None = None

@dataclass
class EnvHealth:
    env: str
    status: Literal["ready", "degraded", "broken"]
    checks: list[HealthCheck] = field(default_factory=list)
    operator_action: str | None = None
    probed_at: str = ""  # ISO timestamp

    def is_ready(self) -> bool: ...
    def to_dict(self) -> dict: ...

def check(env: str, *, only: list[str] | None = None) -> EnvHealth:
    """Run all named checks (or a subset). Aggregate into EnvHealth."""

# Individual check functions, all (env) -> HealthCheck:
def _check_python_runtime(env: str) -> HealthCheck: ...
def _check_writable_overlay(env: str) -> HealthCheck: ...
def _check_dports_compose(env: str) -> HealthCheck: ...
```

**Tests** (`test_health.py`):
- Each check function returns the right `HealthCheck` shape under
  mocked subprocess outcomes (success, missing-deps stderr,
  permission error).
- `check()` aggregation: all-ok → ready, one-broken → broken,
  one-warn → degraded.
- `check(only=["python_runtime"])` runs only the named check.
- `EnvHealth.to_dict()` is JSON-serializable + round-trips through
  json.dumps.

**Done criteria:** module importable, full test coverage,
**no consumers wired**.

**Commit:** `feat(agent): EnvHealth probe module`

---

### Step 2 — Worker + runner cutover

**Goal:** Replace `_classify_env_error` and `_env_broken_reason`
with `health.check()`. The hard cutover for this phase.

**Files:**
- `scripts/generator/dportsv3/agent/worker.py` — remove
  `_ENV_BROKEN_SENTINELS` + `_classify_env_error`; `materialize_dports`
  no longer tags results with `error_category`.
- `scripts/generator/dportsv3/agent/runner.py` — remove
  `_env_broken_reason` module global. `_gate_blocked()` calls
  `health.check(runner_env)` and gates on `status != "ready"`,
  with a 60s result cache. The completion-event mapping
  (`_completion_events_for`) keeps `ENV_BROKEN` as one of its
  routes (now triggered by a probe-failure mid-job, not by a
  stderr sentinel).
- `scripts/generator/tests/test_runner_e2e_lifecycle.py` — the
  `_env_broken_reason` setattr path is replaced with a stubbed
  `health.check` that returns broken health.

**Cache semantics:** the runner caches `(env, EnvHealth)` for
`DP_HARNESS_HEALTH_CACHE_SECONDS` (default 60). On cache miss or
expiry, re-probe. Operator can force re-probe via SIGUSR1 (cheap,
future work; not in Phase 2).

**Cutover criteria:**
- `grep -nE "_classify_env_error|_ENV_BROKEN_SENTINELS|_env_broken_reason" scripts/` returns nothing live.
- Existing `test_runner_e2e_lifecycle.py` passes with the new probe
  stub.
- Manual: enter a chroot, `pkg delete -y py311-sqlite3`, start
  the runner. It should pause on health-broken with a message
  containing the exact `pkg install` command from
  `operator_action`. Re-install the package, runner picks back up
  within the cache window.

**Commit:** `refactor(agent): cutover env_broken to EnvHealth.check()`

---

### Step 3 — `dportsv3 dev-env health` CLI subcommand

**Goal:** operator-facing diagnostic. `dportsv3 dev-env health NAME`
runs `health.check(NAME)` and prints JSON. Used directly for
debugging + by anything that wants to know "is this env usable
right now" without spinning up the runner.

**Files:**
- `scripts/tools/dev-env/dports_dev_env/cli.py` — new subcommand
- `scripts/tools/dev-env/dports_dev_env/health_cmd.py` — handler
  (thin wrapper that imports `dportsv3.agent.health.check` and
  pretty-prints)
- `scripts/generator/tests/test_dev_env_health_cli.py` — new

**Interface:**

```
$ dportsv3 dev-env health 2026Q2
{
  "env": "2026Q2",
  "status": "broken",
  "probed_at": "2026-05-21T10:00:00Z",
  "checks": [
    {"name": "python_runtime", "status": "broken",
     "detail": "missing: py311-sqlite3, py311-pydantic2",
     "operator_action": "pkg install py311-sqlite3 py311-pydantic2 ..."},
    {"name": "writable_overlay", "status": "ok", "detail": "..."},
    {"name": "dports_compose", "status": "broken",
     "detail": "compose dry-run failed: ...",
     "operator_action": "..."}
  ],
  "operator_action": "..."
}
$ echo $?
1
```

Exit code: 0 for ready, 1 for broken, 2 for degraded. Lets shell
scripts gate on `if dportsv3 dev-env health ENV; then ...`.

**Tests:** stub `health.check` to return canned `EnvHealth`,
assert CLI exits with the right code + emits valid JSON.

**Commit:** `feat(dev-env): add 'dportsv3 dev-env health NAME' command`

---

### Step 4 — Integration test for the runner gate

**Goal:** end-to-end test that drives the runner's main loop (or
`_gate_blocked` directly) with a stubbed `health.check` and asserts:
- `status="broken"` → loop pauses, `update_runner_status("paused",
  stage="env_broken: ...")` is called, no claims happen.
- `status="ready"` → loop proceeds.
- Cache: two probes within the window only call `health.check` once.

**Files:**
- `scripts/generator/tests/test_runner_health_gate.py` — new

**Done criteria:** tests green; runtime under 1s.

**Commit:** `test(agent): runner health gate integration`

---

### Phase 2 cutover criteria (overall)

Phase 2 is "done" when:

1. All four steps committed.
2. `pytest scripts/generator/tests/` green.
3. `grep -nE "_classify_env_error|_ENV_BROKEN_SENTINELS|_env_broken_reason|error_category" scripts/` returns nothing live in code.
4. Manual smoke: deliberately break the chroot (rm a py311-* package), confirm:
   - `dportsv3 dev-env health 2026Q2` exits non-zero with the right operator_action.
   - Runner gates on health-broken, surfaces the message in `/agentic/runner`.
   - Re-install the package; runner resumes within the cache window.
5. This plan file gets updated: Phase 2 ledger entry written, Phase 3 detail replaces the current body.

### Risk + rollback

| Step | Risk | Mitigation |
|---|---|---|
| 1 | subprocess calls to dev-env are slow → checks are expensive | aggregate timeout (5s per check), parallelize check execution if needed; cache results in step 2. |
| 2 | The cache hides a freshly-installed package | 60s default is short enough that operators don't wait long; expose `DP_HARNESS_HEALTH_CACHE_SECONDS` for impatience. |
| 2 | Removing `error_category` breaks the agent's tool-result inspection | The agent never read it — it was only the runner's flag. Verified via `grep error_category dportsv3/agent/prompts.py` (no hits in the system prompt). |
| 3 | CLI subcommand exit-code semantics conflict with existing tooling | New subcommand; 0/1/2 codes are conventional. |
| 4 | Test flakiness on subprocess mocking | Tests stub `health.check` itself, not the subprocess layer. |

---

## Phases overview (remaining)

| # | Phase | Layer(s) | Status |
|---|---|---|---|
| 1 | Lifecycle | Layer 1 | ✅ shipped |
| **2** | **Health / readiness** | **Layer 3** | **active (this doc)** |
| 3 | Policy engine | Layer 5 | pending |
| 4 | Context assembly | Layer 4 | pending |
| 5 | Step contract | Layer 2 | pending |

Estimated remaining sizing (refined after Phase 1):

| Phase | LOC delta | Commits | Risk |
|---|---|---|---|
| 2 | +250, −100 | 4 | Low |
| 3 | +200, −180 | 3 | Low–medium |
| 4 | +700, −500 | 4–5 | Medium |
| 5 | +500, −800 | 5–6 | Highest |

Each phase has a **parity test** against today's behavior on at
least one real frozen bundle. No regression on something that works.
