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

Test delta: +26 tests. 275 total green at phase end.

### Phase 2 — Health / readiness (shipped 2026-05-21)

Replaced the inferred-from-stderr `env_broken` path with a direct
structured probe. New `dportsv3.agent.health` module with
`EnvHealth` + `HealthCheck` dataclasses and three named checks:
`python_runtime` (py311-* deps via `pkg info -e`), `writable_overlay`
(sentinel-file touch), and `dports_compose` (`dportsv3 --version`
inside the env). Aggregate status is `ready`/`degraded`/`broken`.

Runner cutover deleted `_env_broken_reason`, `_classify_env_error`,
and `_ENV_BROKEN_SENTINELS`. The gate now consults
`probe_health_cached(env, ttl_seconds)` (default 60s TTL,
`DP_HARNESS_HEALTH_CACHE_SECONDS`). Tool errors matching a known
suspicious pattern force a cache re-probe but never set state — the
probe is authoritative. **Operator no longer has to restart the
runner after fixing the chroot**; the next gate cycle picks it up.

New operator-facing CLI: `dportsv3 dev-env health NAME` emits JSON
and exits 0/1/2 (ready/broken/degraded). Useful for shell-script
gating and ops debugging.

Commits: `b47918ba311` (module) · `93dd9f5244d` (cutover) ·
`f221a0a154f` (CLI subcommand) · `f11fe8d1c6b` (cache integration
tests + `probe_health_cached` extraction).

Test delta: +33 tests (19 health unit + 5 CLI + 9 cache). 310 total
green at phase end.

---

## Current phase: Phase 3 — Policy engine

> **Goal:** consolidate the scattered tier-resolution + retry-cap +
> escalation logic into a single, testable `decide()` function that
> takes a triage outcome + per-port history + current env health
> and returns one `Decision` for the orchestrator to act on.

### Decisions captured up front

- `decide()` is the only function the runner consults at the
  "what next?" boundary. Today that decision is fragmented across
  `policy.tier_for(classification, confidence)`, a hand-coded retry
  cap inline in `_process_triage_job_harness`, and the implicit
  "MANUAL → no patch enqueue" branch. Phase 3 absorbs all three.
- `decide()` reads `EnvHealth` too. If the env is broken at decision
  time, we skip the patch enqueue (the runner gate would block it
  anyway, but the decision should be explicit, not implicit).
- `policy.tier_for` and the JSON config (`config/agentic-policy.json`)
  stay; they're load + lookup logic that `decide()` composes on top
  of. The Tier dataclass and `load_policy()` are kept verbatim.
- The runner's `recent_failure_count` query moves into
  `decision.py` as the implementation of `PortHistory.load()`.
  No new schema; we keep reading the existing `bundles` table.
- Yolo cutover: when Phase 3 lands, every call site that
  previously called `policy.tier_for` directly or computed
  retry-cap inline is replaced by `decide()`. No parallel paths.

### What `decide()` returns

```python
@dataclass
class Decision:
    action: Literal["auto_patch", "escalate_manual", "skip"]
    tier: Tier                  # carries max_iterations + max_tokens
    reason: str                 # human-readable, surfaced in activity_log
    next_event: JobEvent        # what lifecycle event the runner fires next

@dataclass
class PortHistory:
    target: str
    origin: str
    recent_failures: int        # within the configured window
    last_success_at: str | None # ISO; None if never succeeded
    last_attempt_at: str | None # ISO; None if first attempt

    @classmethod
    def load(cls, conn, target, origin, window_hours) -> "PortHistory": ...

def decide(
    triage: TriageOutcome,     # classification + confidence
    history: PortHistory,
    env_health: EnvHealth,
    config: Policy,
) -> Decision: ...
```

Mapping today's behavior:

| Today | Decision output |
|---|---|
| `tier_for(...).name == "MANUAL"` | `action="escalate_manual"`, `next_event=ESCALATE_MANUAL` |
| AUTO/ASSIST, recent_failures ≥ cap | `action="escalate_manual"`, reason includes the cap math |
| AUTO/ASSIST, env_health broken | `action="skip"`, reason explains why (runner gate will also pause) |
| AUTO/ASSIST, env_health ready | `action="auto_patch"`, `next_event=PATCH_START`, tier carries budget |

### Pre-conditions

- Phase 2's `dportsv3.agent.health.EnvHealth` is importable.
- `bundles` table still has the `target`, `origin`, `result`,
  `last_seen_at` columns used by `recent_failure_count`. (Schema
  hasn't changed since `f1272152971`.)
- `policy.Tier` and `load_policy` work — verified by the existing
  18 tests that touch them.

### Step 1 — `decision.py` module

**Goal:** the module, dataclasses, and `decide()` function. Unit
tests cover every branch + every (classification, confidence)
combination from the policy JSON. No consumers wired yet.

**Files:**
- `scripts/generator/dportsv3/agent/decision.py` — new
- `scripts/generator/tests/test_decision.py` — new

**Interface:** as drafted above. Single `decide()` function;
`PortHistory.load(conn, target, origin, window_hours)` reads the
bundles table; `Decision` is the orchestrator's input.

**Tests:**
- Every (classification, confidence) combination from the shipped
  `config/agentic-policy.json` produces a decision matching today's
  `tier_for(...)` output.
- Cap triggers `escalate_manual` when recent_failures ≥ threshold
  (default 3, env-overridable).
- Env-broken health overrides classification → `skip`.
- MANUAL tier without env trouble → `escalate_manual`.
- AUTO with high confidence and clean history → `auto_patch`.
- `PortHistory.load()` against a synthetic in-memory DB returns the
  right count.

**Done criteria:** module importable, tests green, **no call sites
in the runner yet**.

**Commit:** `feat(agent): policy decision engine`

---

### Step 2 — Runner cutover

**Goal:** every place that called `policy.tier_for` or computed a
retry cap inline now calls `decide()`. The hard cutover.

**Files:**
- `scripts/generator/dportsv3/agent/runner.py` — modified
  - `_process_triage_job_harness`: build `TriageOutcome` from result,
    build `PortHistory` via `PortHistory.load(...)`, call
    `decide(triage, history, env_health, policy)`, then route on
    `decision.action`.
  - Inline `recent_failure_count` logic deleted (it lived in
    runner.py; moves into decision.py as PortHistory.load).
  - `_process_patch_job_harness`: keep `tier_for` fallback path
    only if `tier` field missing from job (legacy hand-fired
    patches); use the resolved tier from the parent triage when
    available.
  - `_completion_events_for` keeps mapping by string status —
    untouched. Decision logic is the *what next* boundary;
    completion mapping is the *what happened* boundary.

**Mapping (existing → new):**

| Old call | Replacement |
|---|---|
| `pol = harness_policy.load_policy(policy_path)`<br>`tier = harness_policy.tier_for(pol, classification, confidence)` | `dec = decision.decide(triage, history, env_health, pol)` |
| `if tier.name == "MANUAL": …` | `if dec.action == "escalate_manual": …` |
| `recent_failures = recent_failure_count(...)`<br>`if recent_failures >= max_attempts: tier = MANUAL` | (folded into `decide`) |
| `enqueue_patch_job(...)` | `if dec.action == "auto_patch": enqueue_patch_job(...)` |

**Cutover criteria:**
- `grep -nE "tier_for|recent_failure_count" scripts/generator/dportsv3/agent/runner.py` returns nothing live (the helper moves wholesale into decision.py).
- All 310 existing tests still pass + the new test_decision suite.
- Activity-log entries for tier-capped jobs still surface the
  `recent_failures` + threshold (same fields, now sourced via
  `Decision.reason` + extra dict).

**Commit:** `refactor(runner): route policy + cap through decide()`

---

### Step 3 — Parity smoke

**Goal:** a small integration test that loads the real
`config/agentic-policy.json` and walks `decide()` through every
classification × confidence × history combination, asserting parity
with what the runner *would* have produced before the cutover. This
is the "no behavior regression" gate.

**Files:**
- `scripts/generator/tests/test_decision_parity.py` — new

**Test shape:**

For each `(classification, confidence)` in the live policy JSON:
- Compute the legacy result: `tier_for(pol, classification,
  confidence).name`.
- Build a `Decision` via `decide()` with `env_health=ready` and
  `history=PortHistory(recent_failures=0)`.
- Assert: legacy MANUAL ↔ `decision.action == "escalate_manual"`,
  legacy AUTO/ASSIST ↔ `decision.action == "auto_patch"` and
  `decision.tier.name` matches.

Plus a few hand-crafted cases:
- AUTO + clean history → auto_patch.
- AUTO + 3 recent failures → escalate_manual (cap-driven).
- AUTO + env_health broken → skip.

**Done criteria:** parity test green; all 310+ existing tests still
green; manual run of `dportsv3 dev-env health 2026Q2` still works.

**Commit:** `test(agent): decision-engine parity against legacy tier_for`

---

### Phase 3 cutover criteria (overall)

Phase 3 is "done" when:

1. All three steps committed.
2. `pytest scripts/generator/tests/` green.
3. `grep -nE "tier_for|recent_failure_count" scripts/generator/dportsv3/agent/runner.py` returns nothing live.
4. The parity test confirms decision output matches legacy
   `tier_for` for every (classification, confidence) in the JSON.
5. Manual smoke: trigger a triage on a known failing port,
   confirm activity-log shows the decision reason (auto_patch vs
   escalate_manual vs skip) with the cap math when relevant.
6. This plan file gets updated: Phase 3 ledger entry written,
   Phase 4 (context assembly) detail replaces this body.

### Risk + rollback

| Step | Risk | Mitigation |
|---|---|---|
| 1 | `PortHistory.load` perf regresses if joins get expensive | Same query as today's `recent_failure_count`; bench against a 10k-bundle DB before merge. |
| 2 | Mid-flight job that was capped under legacy logic isn't capped under new logic (or vice versa) | Parity test in Step 3 catches this exhaustively for the (classification, confidence) matrix; manual smoke on a known-looping port (gnome_subr if available) confirms cap fires. |
| 2 | `_process_patch_job_harness` legacy fallback (when `tier` job field is missing) accidentally diverges | Keep using `tier_for` for that fallback path; the patch flow doesn't need full `decide()` because triage already produced one. |
| 3 | Parity matrix omits a combination | Iterate over `pol.classification_to_tier.keys()` × `pol.confidence_floor.values()` ∪ {"low","medium","high"} — the test is generated from the live JSON. |

---

## Phases overview (remaining)

| # | Phase | Layer(s) | Status |
|---|---|---|---|
| 1 | Lifecycle | Layer 1 | ✅ shipped |
| 2 | Health / readiness | Layer 3 | ✅ shipped |
| **3** | **Policy engine** | **Layer 5** | **active (this doc)** |
| 4 | Context assembly | Layer 4 | pending |
| 5 | Step contract | Layer 2 | pending |

Estimated remaining sizing:

| Phase | LOC delta | Commits | Risk |
|---|---|---|---|
| 3 | +250, −180 | 3 | Low–medium |
| 4 | +700, −500 | 4–5 | Medium |
| 5 | +500, −800 | 5–6 | Highest |

Each phase has a **parity test** against today's behavior. No
regression on something that works.
