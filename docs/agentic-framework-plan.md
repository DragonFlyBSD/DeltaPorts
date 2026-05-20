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

Test delta: +26. 275 total green at phase end.

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
and exits 0/1/2 (ready/broken/degraded).

Commits: `b47918ba311` (module) · `93dd9f5244d` (cutover) ·
`f221a0a154f` (CLI subcommand) · `f11fe8d1c6b` (cache integration
tests + `probe_health_cached` extraction).

Test delta: +33. 310 total green at phase end.

### Phase 3 — Policy engine (shipped 2026-05-21)

Consolidated tier resolution + retry cap + env-broken short-circuit
into one `decide(classification, confidence, history, env_health,
policy) -> Decision` function. The orchestrator routes on
`decision.action` ∈ {`auto_patch`, `escalate_manual`, `skip`}.

New module `dportsv3.agent.decision` with `Decision`, `PortHistory`,
and `decide()`. `PortHistory.load(conn, target, origin,
window_hours)` absorbs the runner's `recent_failure_count` query;
that helper got deleted. The lone remaining `tier_for` call site
is `_process_patch_job_harness`'s legacy fallback for hand-fired
patch jobs missing the `tier` field — explicitly out of scope; Phase
5 absorbs it.

Parity test sweeps every `(classification, confidence)` in the
shipped `config/agentic-policy.json` against legacy `tier_for`.
Side artifact: future-work parking lot in
`agentic-framework-design.md` for operator notification on
env-broken / cap.

Commits: `2f6d4c604d8` (design doc parking-lot entry) ·
`89d66e2d4a3` (module) · `30f7a50bc76` (runner cutover) ·
`69fd1be477c` (parity smoke).

Test delta: +27 (19 unit + 8 parity). 337 total green at phase end.

---

## Current phase: Phase 4 — Context assembly

> **Goal:** replace the two `build_*_payload` walls of
> `parts.append(...)` with composable `ContextSection` classes the
> `ContextAssembler` renders in priority order. Strict parity:
> byte-for-byte identical output on the same inputs. Adding new
> sections, changing prompts, or enforcing a hard global token
> budget is **out of scope** — this phase only refactors structure.

### Decisions captured up front

- **Strict parity.** Phase 4 must not change a single byte of any
  LLM prompt vs. today. The whole point is to land the structure
  *first*; behavior changes ride on top in later commits. Golden
  fixtures lock this in via the parity tests in Steps 2 and 3.
- **No global token budget yet.** Today neither payload enforces an
  aggregate cap; sections that have per-section caps keep them.
  Aggregate budget enforcement is a future enhancement once the
  layer exists.
- **Section priority = render order.** Today the order is hard-
  coded by where `parts.append(...)` lives. Sections get integer
  priorities such that sorting reproduces today's order. The
  Assembler does not reorder beyond that.
- **Sections are reusable across triage + patch where the content
  overlaps** (e.g. `SiblingBundlesSection`, `PriorAttemptsSection`,
  `BuildErrorsSection`, `PortFilesSection`, `ExistingPatchesSection`,
  `KEDBSection`, `UserContextSection`, `SnippetsRoundSection`).
  Triage- or patch-specific sections (`AutomationContextSection`,
  `TriageSummarySection`, `PromptFooterSection`) live next to the
  caller.
- **`ctx` is a dataclass, not a kwargs dict.** Render takes one
  typed `ContextCtx` containing `bundle_dir`, `bundle_id`, `job`,
  `kedb_dir`, and the runner's connection (for sections that need
  to query state.db). Cleaner than mutating dicts everywhere.

### Pre-conditions

- `bundle_dir`, `bundle_id` semantics from Phase 1 still hold —
  bundles are either on-disk (via `_materialize_bundle`) or in the
  artifact-store.
- `read_bundle_text`, `bundle_artifact_list`, and the snippet
  helpers in runner.py still work as today.
- All 337 tests currently green; the parity assertions in Steps 2
  and 3 are net-additive.

### Step 1 — `context.py` module

**Goal:** the `ContextSection` Protocol, the `ContextCtx`
dataclass, the `ContextAssembler.render()` driver, and unit tests.
No concrete section classes yet (those land in Steps 2 and 3 as
they're needed).

**Files:**
- `scripts/generator/dportsv3/agent/context.py` — new
- `scripts/generator/tests/test_context.py` — new

**Interface:**

```python
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable
import sqlite3


@dataclass
class ContextCtx:
    """One render-time context object passed to every Section.render."""
    bundle_dir: Path | None
    bundle_id: str | None
    job: dict
    kedb_dir: Path | None = None
    db_conn: sqlite3.Connection | None = None
    # Anything else a section needs goes here, not in **kwargs.


@runtime_checkable
class ContextSection(Protocol):
    name: str
    priority: int        # lower runs first

    def render(self, ctx: ContextCtx) -> str | None: ...
    # None means "skip this section silently" (the only way to
    # produce a missing optional section in today's output).


def render_payload(sections: list[ContextSection], ctx: ContextCtx) -> str:
    """Sort sections by priority, render each, join non-None with
    "\\n" exactly as the legacy parts.append(...) + "\\n".join(parts)
    produced. Empty + None sections drop out."""
```

**Tests** (`test_context.py`):
- Two sections render in priority order regardless of input order.
- A section returning None is omitted (no extra blank line).
- A section returning "" is omitted (no empty section).
- A section that raises propagates the exception (no swallowing —
  payload assembly should crash loudly on bugs).
- The driver concatenates with `"\n"` as the today's join character.
- `ContextCtx` is hashable-fields-only (so future caching is
  straightforward) — or document if not.

**Done criteria:** module importable, tests green, no consumers.

**Commit:** `feat(agent): context assembly module`

---

### Step 2 — Triage payload cutover

**Goal:** replace `build_triage_payload` with `ContextAssembler.
render(triage_sections, ctx)`. Add a parity test against a synthetic
bundle fixture so the cutover proves byte-equivalence.

**Files:**
- `scripts/generator/dportsv3/agent/context.py` — adds the concrete
  section classes used by the triage payload.
- `scripts/generator/dportsv3/agent/runner.py` — `build_triage_payload`
  becomes a thin wrapper that builds the sections list + ctx and
  calls `render_payload`.
- `scripts/generator/tests/test_triage_payload_parity.py` — new.

**Sections to migrate (in render order):**

| Priority | Section | Today's slot |
|---|---|---|
| 10 | `SnippetsRoundSection` | Top — when `has_snippets=true` + `snippet_round>0` |
| 20 | `KEDBSection` | KEDB content |
| 30 | `UserContextSection` | Run-scoped user context |
| 40 | `MetadataSection` | bundle's `meta.txt` |
| 50 | `BuildErrorsSection` | `logs/errors.txt` |
| 60 | `PortFilesSection` | Makefile + pkg-plist + distinfo |
| 70 | `ExistingPatchesSection` | Existing `port/files/patch-*` |
| 80 | `SiblingBundlesSection` | Phase-1 batch siblings |
| 90 | `PriorTriagesSection` | Last 2 historical bundles' triage.md |
| 100 | `PromptFooterSection` | `---` separator + "Analyze this..." |

**Parity test:** create a synthetic on-disk bundle directory with
hand-crafted `meta.txt`, `logs/errors.txt`, `port/Makefile`,
`port/pkg-plist`, etc. Call `build_triage_payload(bundle_dir,
kedb_dir, job)` *before* the refactor lands; capture the bytes as
a fixture string in the test file. After the refactor, the new
implementation must produce the exact same bytes for the same
input. Multiple fixtures exercise: minimal bundle, full bundle,
bundle with siblings, bundle with snippet round.

**Cutover criteria:**
- New `build_triage_payload` is ≤ 30 LOC (composes sections,
  delegates everything else).
- Old inline `parts.append(...)` blocks for the triage path are
  deleted.
- Parity test green for at least 3 fixture variants.
- All 337 existing tests still green.

**Commit:** `refactor(agent): triage payload via ContextAssembler`

---

### Step 3 — Patch payload cutover

**Goal:** same treatment for `build_patch_payload`. Reuses the
sections that landed in Step 2 + adds patch-specific ones.

**Files:**
- `scripts/generator/dportsv3/agent/context.py` — adds patch-only
  sections.
- `scripts/generator/dportsv3/agent/runner.py` — `build_patch_payload`
  becomes a thin wrapper.
- `scripts/generator/tests/test_patch_payload_parity.py` — new.

**Patch-specific sections (in render order):**

| Priority | Section | Today's slot |
|---|---|---|
| 10 | `SnippetsRoundSection` (reused) | Top — same as triage |
| 20 | `TriageSummarySection` | Triage summary from bundle |
| 30 | `AutomationContextSection` | Iteration count, prior failures |
| 40 | `SiblingBundlesSection` (reused) | Batch siblings |
| 50 | `PriorAttemptsSection` | Last 3 bundles' patch_plan + log + status |
| 60 | `UserContextSection` (reused) | Run-scoped user context |
| 70 | `KEDBSection` (reused) | KEDB content |
| 80 | `MetadataSection` (reused) | meta.txt |
| 90 | `BuildErrorsSection` (reused) | logs/errors.txt |
| 100 | `PortFilesSection` (reused) | Makefile + plist + distinfo |
| 110 | `ExistingPatchesSection` (reused) | port/files/patch-* |
| 120 | `PatchPromptFooterSection` | `---` + "Use the dports tools..." footer |

**Parity test:** mirror the triage parity test — synthetic bundle
fixtures, capture pre-refactor output, assert byte-equivalence after
the cutover. Includes fixtures with prior-attempt history and
sibling bundles.

**Cutover criteria:**
- New `build_patch_payload` is ≤ 30 LOC.
- Old inline blocks deleted.
- Parity test green for at least 3 fixture variants (minimal,
  with-history, with-siblings).
- All existing tests still green.

**Commit:** `refactor(agent): patch payload via ContextAssembler`

---

### Phase 4 cutover criteria (overall)

Phase 4 is "done" when:

1. All three steps committed.
2. `pytest scripts/generator/tests/` green.
3. `grep -nE "parts\\.append" scripts/generator/dportsv3/agent/runner.py` returns nothing — both legacy walls deleted.
4. Both parity test files green (triage + patch), across all
   fixture variants.
5. Manual smoke: run one real triage and one real patch on dfly,
   eyeball the activity-log's `decision` entry and the bundle's
   `analysis/{triage,patch}.md` — they should look indistinguishable
   from pre-refactor output.
6. This plan file gets updated: Phase 4 ledger entry written,
   Phase 5 (step contract) detail replaces this body.

### Risk + rollback

| Step | Risk | Mitigation |
|---|---|---|
| 1 | `ContextSection` Protocol too restrictive — a real section needs something the interface doesn't allow | Land the Protocol with no consumers; Step 2 immediately exercises it for 10 sections. If the API doesn't fit, redesign in the same Step 2 commit. |
| 2 | Byte-for-byte parity fails because of subtle whitespace differences | Parity test runs on multiple fixtures; first failure is concrete (golden diff). Iterate until match. |
| 2 | A section uses external state (DB query, HTTP call) that's hard to stub | Mock at the `ContextCtx.db_conn` boundary — that's why ctx carries the conn explicitly. |
| 3 | Patch payload has more sections than triage; some need fixture data triage didn't | Each fixture variant has all the bundle artifacts needed for the sections in scope; new variants add new files. |
| 3 | Section reuse breaks a triage parity test by accident | The triage parity test pins exact bytes; if a "reusable" section changes its render output, triage immediately catches it. |

---

## Phases overview (remaining)

| # | Phase | Layer(s) | Status |
|---|---|---|---|
| 1 | Lifecycle | Layer 1 | ✅ shipped |
| 2 | Health / readiness | Layer 3 | ✅ shipped |
| 3 | Policy engine | Layer 5 | ✅ shipped |
| **4** | **Context assembly** | **Layer 4** | **active (this doc)** |
| 5 | Step contract | Layer 2 | pending |

Estimated remaining sizing:

| Phase | LOC delta | Commits | Risk |
|---|---|---|---|
| 4 | +700, −500 | 3 | Medium |
| 5 | +500, −800 | 5–6 | Highest |

Each phase has a **parity test** against today's behavior. No
regression on something that works.
