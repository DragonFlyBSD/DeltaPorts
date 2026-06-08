# Agentic architecture backlog — shipped steps

> Steps from `agentic-architecture-backlog.md` that have landed.
> Kept as a historical record of the design rationale that drove the
> shipped work. See the live backlog for pending/in-progress steps.

### Step 19 — detection-driven triage playbooks — shipped (via Step 27)

> **Fully shipped in Step 27.** Both 19a (mechanical toolchain
> detection — `detect_toolchains()` in
> `dportsv3.agent.playbooks`) and 19b (the 10 hand-authored
> toolchain markdown files) landed in `c7e1c865298` as part of
> Step 27f. The catalog actually shipped 11 entries
> (`toolchain-autoconf.md`, `cmake.md`, `meson.md`, `perl5.md`,
> `python.md`, `go.md`, `cargo.md`, `gmake.md`, `pkg-config.md`,
> `libtool.md`, plus a `c.md` catch-all) under
> `docs/agent-playbooks/`. 19c's loader sketch is superseded by
> Step 27's `load_playbooks`. The section below preserves the
> original design rationale.

Today the triage LLM gets a generic system prompt and the build log,
and is expected to figure out from scratch what kind of port it's
looking at, what the toolchain typically does wrong, and how to
phrase the classification. This works, but it spends model
intelligence on a problem humans have already solved a hundred
times: GNU autoconf C programs fail in a well-known set of ways;
CMake projects fail in a different well-known set of ways; Perl XS
modules in yet another.

A *playbook* encodes that human knowledge as a short markdown
document attached to a build-system tag. Mechanical detection of
the port's toolchain selects the relevant playbook(s); they get
prepended to the triage/patch system prompt. The model arrives at
the failure already knowing the local laws of physics, not
inferring them turn-by-turn from log fragments.

This is distinct from — and probably more useful than — the KEDB
metadata in Step 14. KEDB is reactive ("we've fixed this exact
signature before"); playbooks are proactive ("this is the shape of
port we're looking at, here are the things to check first"). The
two are complementary, but playbooks win on first-failure ports
and on the 80% common case; KEDB earns its keep on the long-tail
recurring weirdness only after it's been collected.

#### Goal

When the agent sees a failure on a port whose toolchain we
recognize, the system prompt already contains a curated list of
that toolchain's usual suspects. The agent's first inference is
"check item 3 from the autoconf playbook" not "what does this
linker error mean in general."

#### Sub-steps

**19a — port toolchain detection.**

Mechanical, no LLM. Implement
`dportsv3.agent.playbooks.detect(port_dir) -> set[str]` that walks
the port's source tree + Makefile and returns a tag set such as
`{"c", "autoconf", "pkg-config", "libtool"}` or `{"perl", "xs"}`
or `{"cmake", "cpp"}`.

Detection signals:
- `Makefile` `USES=` line (`autoreconf`, `cmake`, `meson`,
  `perl5`, `python`, `go`, `cargo`, etc.) — already a curated
  taxonomy in the FreeBSD ports framework.
- `Makefile` `GNU_CONFIGURE=yes` → `autoconf`.
- Presence of `configure.ac` / `configure.in` → `autoconf`.
- Presence of `CMakeLists.txt` → `cmake`.
- Presence of `Cargo.toml` → `cargo`.
- File extensions in source tree (`.c` vs `.cpp` vs `.rs`) for
  language tagging.

Detection runs once per triage job, results cached on the bundle.

**19b — playbook authoring.**

Each playbook is a markdown file under
`scripts/generator/dportsv3/agent/playbooks/`. Naming is
`<tag>.md`. Contents: a short paragraph on what the toolchain is
and how it typically fails, then a numbered list of "usual
suspects" — concrete failure patterns the agent should check, in
roughly likelihood order. Each suspect names a symptom (what the
log will say) and a typical fix (what the agent should consider).

Initial coverage target (one playbook each):
- `autoconf.md`
- `cmake.md`
- `meson.md`
- `perl5.md`
- `python.md`
- `go.md`
- `cargo.md`
- `gnu-make.md` (the "raw Makefile" catch-all)
- `pkg-config.md`
- `libtool.md`

That's ten playbooks covering the bulk of DPorts. Each is
roughly one page (300–500 words / 400–700 tokens).

These are *hand-curated knowledge products*, not auto-generated.
Operator + maintainer expertise distilled to text. Version-
controlled, reviewed in PR, evolved over time. No LLM in the
maintenance loop — the maintenance loop is humans writing
markdown.

**19c — playbook loader + system prompt assembly.**

`dportsv3.agent.playbooks.load(tags) -> str` reads the matching
playbook files, concatenates them under a heading like:

```
## Toolchain playbooks — usual suspects for this port

### autoconf
...

### pkg-config
...
```

The triage and patch system prompts each get a new section that
pulls in the relevant playbooks for the bundle being processed.
Section is omitted (and the section header skipped) when no
playbook matches.

Order matters: detection gives an unordered set, but the loader
emits in a canonical order (build system first, then language,
then helpers). Stable ordering helps prompt cache hits.

**19d — token-budget guardrail.**

Three matched playbooks at ~700 tokens each = ~2.1K tokens
prepended to every call. Tolerable, but it can grow. Two guards:

- Hard cap on total playbook tokens (e.g. 3000); if matched
  playbooks exceed, drop the lowest-priority ones (helpers
  before languages before build systems).
- Telemetry event `playbook_loaded` emitted per call with
  matched tags + final token count, so the cost is visible in
  the tracker (next to the existing token-usage card from Step
  9a).

**19e — tracker UI surfacing.**

The bundle detail page and the job detail page get a small
"Toolchain" line showing the matched tags. Mostly for
debuggability: when an operator looks at a failure, they can
immediately see which playbooks the agent had loaded — and
whether detection missed something obvious.

When detection is empty (the catch-all case), surface that
explicitly ("no toolchain playbooks matched") so the gap is
visible and feeds into 19f.

**19f — feedback loop for new playbooks.**

Operators reviewing escalated failures will spot patterns the
playbooks should cover. Provide a lightweight workflow:
- Tracker has a "missing playbook" button on the bundle page;
  clicking opens an issue or appends to a markdown TODO file in
  the repo with the bundle ID + a suggested playbook name.
- The KEDB conversation from Step 14 morphs into "long-tail
  notes that *might* graduate into playbooks or stay as KEDB
  catch-net entries."

#### LOC estimate

- 19a detection: ~120
- 19b playbook authoring: 10 files × ~400 words each — content,
  not code, but several days of human writing
- 19c loader + prompt assembly: ~80
- 19d guards + telemetry: ~50
- 19e UI: ~60
- 19f feedback loop: ~50

~360 LOC, plus ~4000 words of playbook content.

#### Order

19a → 19c → 19b → 19d → 19e → 19f. Detection and the loader land
first (with empty playbooks) so the wiring is tested without
gating on content; then write the playbooks (the longest-pole
substep, since it's expertise capture not coding); then the
guards and UI; then the feedback loop.

#### Why not earlier

Pre-Step-19 the triage and patch flows are working — the agent
is fixing real ports without playbook help. Playbooks are a
cost/quality optimization, not a missing feature. Doing them
before there's smoke-test data on which toolchains actually fail
and how means writing playbooks by guess. Doing them after some
real failure corpus exists means each playbook is grounded in
observed evidence. Step 10 + the first month of operating Step 17
should produce that corpus.

#### Relationship to Step 14

Step 14's KEDB is not killed by this. After Step 19, the natural
division becomes:

- **Playbooks**: common-case, build-system-level expertise.
  Hand-curated, broad, proactive.
- **KEDB**: the long tail — that one port that fails the same
  weird way every quarter. Auto-collected from prior fixes,
  narrow, reactive.

Step 14 should be re-scoped at that point to focus on the
catch-net role rather than the universal-lookup role originally
envisioned.

### Step 20 — direct ops conversion as a first-class job type — shipped

`overlay.dops` is the highest-leverage feature for LLM-driven port
maintenance. The mental model that matters:

- **Framework-level adjustments** (Makefile tweaks for DragonFly,
  `USES`/`CONFIGURE_ARGS` swaps, OSVERSION guards, dep
  substitutions, build-system glue) → always belong in dops.
  Pattern-based, intent-driven, exactly the shape dops was
  designed for.
- **Software-level changes that are simple substitutions** (e.g.
  hardcoded `/usr/local` → `${PREFIX}`, bounded-scope sed
  replacements) → expressible as dops `REPLACE_*` commands at
  build time, also belong in dops.
- **Software-level complex surgery** (multi-line restructuring,
  conditional ifdef logic, intertwined-with-context changes) →
  stays as real static patches under `dragonfly/`. This is what
  `patch -p1` is for and dops doesn't pretend to solve it.

The win is *not* "no more static patches ever." It's that the
*framework* layer's tax on patching shrinks dramatically, and
simple source changes follow it into dops. The irreducible
complex-source-patch tail remains, but it's a minority of the
volume and that's a feature, not a defect.

#### Existing infrastructure to build on

`scripts/generator/dportsv3/migration/` is an eight-module package
that already covers most of the deterministic side of conversion:

- `inventory.py` — scans the tree, detects targets, complexity
  signals.
- `classify.py` — auto-safe vs needs-judgment classification.
- `convert.py` — MVP mechanical translator
  (`Makefile.dragonfly` → dops ops + a list of
  ``unsupported_reasons`` for what it couldn't handle).
- `batch.py`, `waves.py`, `policy.py`, `progress.py`,
  `dashboard.py` — deterministic batch infrastructure +
  observability.

Step 20 does **not** rebuild any of this. It adds an LLM layer
that handles the long tail the deterministic converter flags
as unsupported, and wires that layer into the job lifecycle so
results land in the same tracker surfaces as triage and patch.

#### Goal

A port that lacks `overlay.dops` is converted exactly once,
lazily on first triage. Conversion runs the existing
deterministic converter first; only the unsupported items reach
an LLM. Success means the port builds end-to-end with dops
(plus any complex-source patches the agent correctly judged
should stay). The patch flow downstream then operates on the
converted port and never has to think about framework-level
patch fuzz again.

The deterministic batch CLI in `migration/batch.py` continues
to exist for operator-driven mass migration; Step 20 does not
add a parallel agent-driven batch surface (see "20g — removed"
below).

#### Sub-steps

**20a — wire detection through the existing classifier.**

No bespoke `needs_conversion()`. Step 20's contribution is the
*entry point* into the existing pipeline:

- A thin `dportsv3.agent.dops.classify(origin)` that calls
  `migration.classify` + `migration.inventory` and returns the
  port's current state: `converted` / `auto_safe_pending` /
  `needs_judgment` / `complex_only_keep_patches`.
- Cache results on the port row in `state.db`; invalidate when
  the port's overlay tree is touched.

Detection emits a tag set similar to Step 19's playbook tags
(e.g. `{"has-framework-patches", "has-complex-source-patches"}`),
which the convert prompt later uses to scope the work.

**20b — convert system prompt + payload builder, scoped to the
unsupported tail.**

A new `CONVERT_SYSTEM` prompt in `dportsv3.agent.prompts`. The
payload built by `build_convert_payload(origin, target)`
deliberately *narrows* the agent's view to what the mechanical
converter could not handle:

- The deterministic converter's auto-generated dops ops (already
  applied — the agent does not re-do this work).
- The list of ``unsupported_reasons`` from `migration/convert.py`.
- For each unsupported item: the relevant source/Makefile excerpt
  + the existing static patch (if any) that addresses it.
- The dops reference doc, focused on `REPLACE_*` semantics.

The prompt teaches three judgment calls, in order:

1. **Framework vs source.** Is this an adjustment to the ports
   framework (Makefile, USES, etc.) or a real source-file edit?
2. **Source-simple vs source-complex.** If source: is the change
   a bounded substitution expressible as `REPLACE_*` (yes → dops
   it), or genuine surgery (no → keep the static patch under
   `dragonfly/`)?
3. **Audit-worthy reason.** Whatever the call, record a short
   reason — feeds the proof JSON and the tracker UI so reviewers
   see *why* each item ended up where it did.

The prompt is fundamentally different from the patch prompt:
input is a known-good port (the existing patches already work),
no diagnosis pressure, no failure log. Just judgment over a
bounded list of items.

**20c — `process_convert_job` handler + enqueue.**

New handler `dportsv3.agent.convert.run(payload, env)`. Flow:

1. Run `migration.convert.convert_record(...)` (deterministic).
2. If `unsupported_reasons` is empty: write the generated dops,
   verify with `dsynth_build`, parse Conversion Proof, mark
   `done` without any LLM call.
3. Otherwise: enter the attempt_loop with `CONVERT_SYSTEM` + the
   payload built in 20b, restricted to the unsupported items.
4. Parse final `## Conversion Proof (JSON)`:

```json
{
  "origin": "...",
  "mechanical_ops_written": <count>,
  "framework_migrated_to_dops": ["...", "..."],
  "source_migrated_to_replace_ops": ["...", "..."],
  "source_patches_retained": [
    {"file": "patch-foo.c", "reason": "multi-line restructuring"}
  ],
  "rebuild_ok": true
}
```

Three buckets, with reason strings on retained patches so the
audit is reviewable.

New `enqueue_convert_job(...)` mirrors `enqueue_triage_job` /
`enqueue_patch_job`. New `JobState.CONVERTING` parallels
`TRIAGING` / `PATCHING`. Runner dispatcher gains a new
`elif job_type == "convert"` arm in `runner.py:2105`.

**20d — triage hook for lazy conversion.**

In `process_triage_job`, before invoking `triage.run`:

```
state = classify(origin)
if state in ("auto_safe_pending", "needs_judgment") and not has_active_convert_job(...):
    enqueue_convert_job(origin, target, requested_by="triage")
    return "deferred: awaiting dops conversion"
```

`complex_only_keep_patches` does *not* trigger conversion — the
port already lives in its correct end state. `converted` is the
no-op success case.

The deferred triage's stop reason references the spawned convert
job by id so the tracker chain is navigable. Once the convert
job hits `done`, the original failure either auto-retriages via
the Step 5 retry path or sits in manual queue with a "ready to
retriage" affordance.

**20e — verification.**

Loose first, strict later:

- *Loose (ships with 20):* the convert handler runs `dsynth_build`
  on the converted port and asserts `rebuild_ok=true`. If the
  port builds end-to-end with dops + any retained complex
  patches, the conversion is good enough by the only criterion
  that matters end-to-end.
- *Strict (deferred to Step 11b):* build the port twice — once
  pre-convert, once post-convert — and byte-compare the resulting
  `pkg create` manifests. Mismatch → escalate.

20 ships with loose verification; the verification harness
arriving in 11b naturally extends to convert jobs as a bonus.

**20f — tracker UI surfacing, integrated with `migration/dashboard.py`.**

Read the existing `migration/dashboard.py` first; it likely
already presents the deterministic side. Step 20's UI work
*extends* that surface rather than building a parallel one:

- New retire reasons: `convert_succeeded`, `convert_failed`,
  `convert_escalated`. Activity-log entries for each.
- Job-list filter `type=convert` on `/agentic/jobs`.
- Dashboard card showing open convert jobs (separate from the
  deterministic batch progress that `migration/dashboard.py`
  already tracks).
- Bundle/job detail pages link the convert → triage → patch
  chain when one exists for the port.
- A per-port "dops status" line (sibling to Step 19e's
  "Toolchain" line) showing the classifier's state.

**20g — operator batch CLI.**

**Removed.** Two reasons:

1. `migration/batch.py` already provides deterministic batch
   conversion. Mass migration is a solved deterministic problem;
   it does not need an agent-driven parallel surface.
2. The lazy path in 20d catches every port that *actually fails*,
   which is the only set with a payoff. Converting a port that
   never fails wastes tokens and introduces regression risk for
   no observable benefit — and contradicts the case-by-case
   judgment model the convert prompt is built around.

If proactive agent-driven sweeps ever become a real policy
need ("we want to be off framework-patches by Q3"), they can be
added then as a small follow-up. They are not part of Step 20.

#### LOC estimate

- 20a thin classifier wrapper + caching: ~50
- 20b prompt + payload (focused on unsupported tail): ~100 +
  ~500 words of prompt
- 20c handler + enqueue + dispatcher arm: ~150
- 20d triage hook: ~40
- 20e loose verification: ~30
- 20f UI integration with `migration/dashboard.py`: ~80

~350 LOC + prompt content. Substantially smaller than the
original draft because the deterministic infrastructure is
already in place; the agent layer is the long-tail handler,
not a from-scratch system.

#### Order

20a → 20c → 20b → 20e → 20d → 20f. Classifier wrapper first
(read-only, easy to verify against real ports); dispatcher arm
+ handler skeleton next (with a stub LLM call) so the lifecycle
is exercised end-to-end; convert prompt then so the handler has
real work to do; loose verification right after as the success
criterion; lazy triage hook so real failures start exercising
the path; UI integration last.

#### Dependencies

- **Hard:** existing job-type dispatcher, lifecycle state
  machine, worker tool surface — all shipped. Also: the
  `migration/` package, also shipped.
- **Soft:** Step 11b verification harness (lets 20e graduate from
  loose to strict); Step 13 guardrail middleware (could enforce
  "no static-patch writes on a port with an open convert job",
  but the lazy dispatch already provides ordering implicitly).
- **No blocker.** Step 20 can land immediately after Step 10.

#### Implementation prerequisite

Before writing any code: read the dops framework end-to-end —
`engine/api.py` (parse_dsl, check_dsl, build_plan), `migration/`
(convert.py, classify.py, inventory.py, batch.py at minimum), and
any prose docs under `docs/` covering dops syntax and
`REPLACE_*` semantics. The plan above is built from inference
about the framework's shape; the implementation has to be built
from the framework's actual API.

#### Why early, not later

dops is the single highest-leverage feature for LLM-driven
maintenance — patch-fuzz failures on framework-level changes
evaporate, simple source changes follow them into dops, drift
survival goes up, audit trails become readable. Every patch
attempt on a non-converted port pays the framework-patch tax
in tokens and correctness risk. Pulling conversion forward in
the order means future patch attempts on those ports run
cheaper and more reliably. The deferral cost is real and
ongoing; the implementation cost is one step.

#### Suggested updated order

10 → 20 → 11 → 16 → 19 → 12/13 → 17/18 → 14/15.

#### Post-shipment fixes (2026-05-24)

Three substantive corrections to the Step 20 dispatch landed during
smoke testing. None expand the design; they harden it against bugs
that surfaced on real ports.

- **`5369db9fd4e` — break infinite triage/convert loop on auto-safe
  ports.** `convert_record` wrote `overlay.dops` but never removed
  `Makefile.DragonFly`. `classify_dops` requires `has_dops AND NOT
  has_unmigrated` to return `converted`; with both files present it
  returned `auto_safe_pending` forever, so the
  triage→defer→convert→resume cycle never terminated.
  `devel/libunistring` spun up 100+ paired jobs in ~13 minutes.
  Two fixes: `convert_record` now `mk_path.unlink()` after a
  successful write, and `_maybe_defer_to_convert` got a wall-clock
  circuit breaker (`_recent_successful_convert`) that refuses to
  re-defer if a convert reached DONE for this `(origin, target)`
  within the last 10 minutes.
- **`ccab8ebad88` — unify overlay assessment across host and
  chroot.** Pulled the "is this port converted / auto-safe /
  needs-judgment / not-in-scope" logic into a new
  `dportsv3.agent.overlay_state` module shared by host-side tooling
  (`dops.classify`) and the in-chroot probe (`worker.classify_dops`).
  Two collectors (`facts_from_repo`, `worker.probe_overlay_facts`)
  build identical `OverlayFacts`; one `assess_overlay` rule set
  decides the verdict. `OverlayAssessment.action` drives the runner
  dispatch: `surface_invariant` (e.g. `overlay.dops` +
  `Makefile.DragonFly` coexist) refuses to defer and logs
  `triage_defer_invariant_break` so the broken half-migration is
  visible instead of spinning another convert loop. The
  substrate-drift bug that let host and chroot disagree on the same
  port is structurally gone.
- **`300b7b1e96a` — jobs inherit target from their bundle.** The
  tracker's `token_usage_for_port` JOIN was filtering by
  `j.target = bundle.target` while triage and patch jobs landed
  with `target=NULL` (the hook runs with a possibly-empty
  `DPORTSV3_TRACKER_TARGET` env var, and the client strips empty
  strings out of the detail dict). The "Lifetime token cost"
  card was silently suppressed even when the artifacts had the
  numbers. Fixed in three places — `runner._lookup_bundle_target`
  + `_register_new_job` backfill, server-side
  `apply_transition` fallback with `--bundle-id` plumbed through
  artifact-store-client + hook_common.sh, and
  `enqueue_patch_job` now writes `target=` into the .job file
  content so `proposed_fix.md` stops rendering `Target: (none)`.
  Also folded into `proposed_fix.md`: triage tokens now appear
  separately + a combined total.

### Step 24 — prompts + quickref consolidation — shipped (absorbed by Step 27)

> **Closed-out 2026-06-05 — shipped by absorption, not as a
> standalone step.** Step 27 (unified playbook library) did the
> structural half; the residual quickref-vs-engine audit was run
> and comes up clean. Findings:
>
> - **24c (strip `CONVERT_SYSTEM`) — done via 27e.** The prompt's
>   `## dops syntax reference` no longer embeds op syntax; it points
>   to the attached quickref (`prompts.py` `CONVERT_SYSTEM`,
>   "search for the heading `# dops Quick Reference`"). The
>   classification decision trees point at the
>   `convert-classify-patch-domain` / `convert-target-directive`
>   playbooks instead of inlining them.
> - **Quickref↔engine audit (the "what remains" sliver) — clean.**
>   Diffed every directive verb the engine parser
>   (`dportsv3/engine/parser.py`) accepts against
>   `dops_quickref.md`: `mk set/unset/add/remove`,
>   `mk disable-if/replace-if`, `mk block set`,
>   `mk target set/append/remove/rename`,
>   `file copy/materialize/remove`,
>   `text line-remove/line-insert-after/replace-once`,
>   `patch apply`, and the `on-missing error|warn|noop` modifier —
>   all documented, none stale. No missing or orphaned ops.
> - **One residual (judgment call, not a task):** the "two kinds of
>   patches / never `patch apply dragonfly/*`" rule is triplicated
>   across `dops_quickref.md`, the `convert-classify-patch-domain`
>   playbook, and `CONVERT_SYSTEM` procedure step 5. Defensible —
>   each copy serves a distinct role (reference / classification
>   recipe / in-procedure reminder). File a one-line dedup only if
>   the three copies ever drift; not worth pre-emptive collapse.
>
> The original plan text is preserved below as the design rationale
> that drove the (now-absorbed) work.

Surfaced during Step 20 smoke testing: ``CONVERT_SYSTEM`` and
``dops_quickref.md`` have grown by accretion as each smoke-test
finding got jammed into whichever doc was open at the moment.
Result: the same op-specific clarification ("``file.copy`` is
within-port_root, ``file.materialize`` is overlay→port_root";
"never ``patch apply dragonfly/*``") now lives in three different
places, drifts independently when corrections happen, and bloats
the agent's payload on every call.

The two docs should have different jobs and stop overlapping.

#### Goal

- **``dops_quickref.md``** is the *complete reference* for the
  DSL. Every op has shape + semantics + common-pitfalls note +
  example. Self-contained — it's what the agent reads when it
  calls ``dops_reference``.
- **``CONVERT_SYSTEM``** is the *job description*. Goal,
  classification framing, tool surface (one line each, deferring
  to quickref for syntax detail), procedure, response contract.
  Strip the embedded syntax-reference duplication.
- Each fact about an op lives in exactly one place. Bright-line
  rules ("never ``patch apply dragonfly/*``") live with the
  most relevant op in the quickref, with a one-line reminder
  in the procedure if needed.

No behavior change. The agent's outputs should be identical
across the cleanup; the prompt just gets shorter.

#### Sub-steps

**24a — inventory current duplications + drift.**

Grep both files for each op name + each bright-line rule, find
the duplicated paragraphs, pick the canonical home for each.
Output is a small audit table; gives the cleanup a flight plan
so 24b can be mechanical.

LOC: zero code; one audit table in the commit message.

**24b — consolidate the quickref.**

For every op, ensure exactly one canonical entry in the quickref
with:
- Shape (one-line syntax).
- Semantics (1–2 sentences).
- Common pitfall / easy-confusion note (where relevant).
- One example.

Move framework-knowledge sections ("Two kinds of patches",
"When to use which" table) into the quickref if they aren't
already there exclusively.

LOC: ~50 net deletions (deduped paragraphs).

**24c — strip ``CONVERT_SYSTEM``.**

Remove the embedded "dops syntax reference" subsection that
duplicates the quickref. Replace with one line: "See the
``dops_reference`` tool for the full op syntax; this prompt only
covers what's specific to convert (classification + procedure +
response contract)."

Strip bright-line rules from the procedure where they're already
in the quickref; keep at most one-line reminders ("classification
rules — see quickref's 'Two kinds of patches'").

LOC: ~80 net deletions from the prompt.

**24d — token-cost measurement.**

After 24b+24c, re-measure ``CONVERT_SYSTEM`` token count vs
pre-cleanup baseline. Convert payload size measured against a
real port (devel/libuv has been the canonical reproducer). Goal:
prompt + quickref combined is smaller than today, agent behavior
unchanged on smoke tests. If unchanged behavior + lower tokens,
the cleanup paid off.

LOC: a tiny measurement script under ``scripts/`` if it doesn't
already exist; otherwise just `wc -w` + manual diff.

#### LOC estimate

Net ~-130 lines across the two files. No code changes; no test
changes. Behavior preservation verified by re-running a known
convert (libuv) before/after and asserting identical
``put_file`` writes from the agent.

#### Order

24a → 24b → 24c → 24d. Audit first (so 24b/c know what to move
where); quickref before prompt because the prompt will start
referencing the quickref by section; measurement last as the
acceptance check.

#### Dependencies

- **Hard:** none.
- **Soft:** none. Can run anytime after Step 20 stabilizes —
  this is purely the documentation half of the same accretion
  problem 21/22/23 address for code.

#### Why not earlier

Same answer as 21/22/23: the docs grew opportunistically because
we were chasing real bugs. Each correction was best landed
quickly; the cleanup pass is what comes after the bug-fix
cadence quiets down.

#### Suggested updated order

10 → 20 → 11 → 16 → 21 → 23 → 22 → 24 → 19 → 12/13 → 17/18 → 14/15.

24 slots right after 22 — both touch the agent layer, both are
no-behavior-change consolidation, and 22's phase-helper
extraction may surface more opportunities for prompt
simplification (e.g. if `assemble_payload` becomes the natural
home for some structured tool-surface description, that's a
cue to drop one more duplication from the prompt).

### Step 25 — edit-intent DSL for the agent edit surface — shipped (dops-only)

> **Shipped — and the "Step C" narrowing (2026-06-05).** The
> edit-intent layer shipped, but **not as the dual-mode design
> below describes.** A consolidation step ("Step C") collapsed it
> to **dops-only**: compat-mode rendering and the `convert_to_dops`
> intent were removed entirely. In the code today
> (`dportsv3/agent/edit_intent/`): `Mode = Literal["dops"]`, the
> `Translator` constructor raises on any non-dops mode, there is no
> `_compat.py`, and every intent routes to a `_dops` renderer.
>
> The mechanism that made compat-mode unnecessary is shape **#3**
> (convert-first) from the design discussion below: **convert is a
> hard prerequisite.** The patch agent only ever sees
> dops-converted substrate, so the translator never needs to render
> compat. This is enforced at the worker boundary —
> `worker.apply_intent` calls `assess_dops` and a half-migrated
> port returns `action='surface_invariant'`, holding the patch
> agent off until convert finishes. So the shipped design is a
> *blend*: the edit-intent DSL (#5) for the agent surface, made
> single-mode by the convert-first guarantee (#3). The "lossless
> dops→compat lowering" that #4/#5's dual-mode rendering would have
> needed is moot — it was never built.
>
> The intent catalog also grew. v0 (below) specced six edit intents
> plus the convert-only `convert_to_dops`. The shipped catalog is
> **ten dops-only intents**: the original six (`replace_in_patch`,
> `drop_patch`, `add_patch`, `add_file`, `change_makefile`,
> `bump_portrevision`), minus `convert_to_dops`, plus four added
> later — `replace_in_dops_block` and the Step 39 deletes
> (`drop_mk_directive`, `drop_file`, `drop_target_block`).
>
> **Canonical sources** (this design record is now history): the
> per-intent JSON schemas under
> `dportsv3/agent/edit_intent/schemas/`, the reconciled (dops-only)
> `docs/edit-intent-design.md`, and the coverage matrix in
> `docs/intent-surface-gaps.md`. The surface work continues as
> Steps 38–41 (target scoping, Family A deletes shipped, Family B
> pending, Family C deferred). The dual-mode narrative below is
> retained for the design rationale and the bandages-retired table;
> read it as *why we built an intent layer*, not *what shipped*.

Surfaced during the devel/gperf analysis run (bundle
`devel_gperf-20260523-094119Z`). The patch agent and the convert
agent today operate on two different on-disk shapes — compat ports
edit `dragonfly/*` and call `install_patches`; dops ports edit the
`patch.apply` / `file.materialize` / `file.copy` statements inside
`overlay.dops`. The agent has to *know which shape it's on* and pick
the right tool calls. Today's classifier (`classify_dops`) returns
`compat | dops | needs_judgment`, and the patch agent happens to have
enough dops-aware tools (`validate_dops`, `put_file` against
`overlay.dops`) that the `needs_judgment` path mostly works — but
it's silently wrong on the boundary cases (e.g. `put_file` to
`dragonfly/patch-*` on a dops port: the edit gets clobbered on next
reapply). The architectural fix is to stop forcing the agent to know
the substrate at all.

Five shapes were considered during the design discussion:

1. **Mode-aware patch agent** (cheap, narrow) — branch the system
   prompt on `classify_dops` result, give each mode its own tool
   subset.
2. **Sibling agents** (incremental) — `patch_compat` and `patch_dops`
   as two distinct agents; dispatcher picks.
3. **Convert-first pipeline** — force every port to dops before
   patch sees it. Simple but bets the farm on convert success rate.
4. **Dops-as-universal-grammar with compat as a render target** —
   patch agent only ever speaks dops; engine lowers dops to compat
   for compat-mode ports. Requires a lossless dops→compat pass that
   doesn't exist today.
5. **Edit-intent DSL** (chosen). The agent emits intent statements
   (`replace_in_patch`, `add_file`, `change_makefile_var`) instead
   of file writes. A translator turns intent → compat ops or dops
   ops depending on port mode. The agent stops knowing the
   substrate.

Edit-intent wins because the agent layer becomes *substrate-agnostic*
without requiring convert success on every port (#3) or a lossless
dops→compat lowering pass (#4). The translator is the only piece
that knows about modes; the agent's prompt collapses to "what change
do you want to make?" without "where on disk does that change live?"

#### Goal

After Step 25:

- The patch agent emits a sequence of *intent statements* describing
  the change it wants to apply, not file writes.
- A new translator module (`dportsv3.agent.edit_intent`) reads the
  port's mode from `classify_dops` and renders each intent statement
  into either a compat-style file edit or a dops statement edit.
- Adding a new edit primitive is one new intent type + one
  translator branch per mode, not a prompt rewrite.
- The patch agent's prompt no longer carries the dops/compat
  distinction (it disappears below the intent layer).
- Empty-diff bugs from the gperf class are impossible: every intent
  statement produces a diff with a deterministic shape, captured by
  the translator, not by post-hoc `git diff`.
- **Job execution is transactional.** An agent run is "begin →
  emit intents → apply (deterministic) → record → reset
  workspace." Failure mid-apply rolls back; success records the
  intent log as the canonical artifact and resets the env to
  baseline. Verify-fix replays the intent log against a known
  baseline (no drift possible).
- **Workspace state is bounded.** The env's writable overlay is no
  longer a state-accumulator across runs. After every patch/verify
  job, `ports/<origin>/` returns to the baseline (typically
  `git HEAD` of the DeltaPorts checkout). Convert is the one
  exception — its output is meant to persist (and would itself be
  expressed as a single "lift to dops" intent set committed to the
  env's local branch). See "Workspace lifecycle" below.

#### Bandages this step retires

The chain of week-of-2026-05-24 incidents (gperf empty diff,
libunistring loop, python312 wasted budget, liblz4 missing token
card, v4l_compat verify drift, the staged-`new file` leak from
`git apply --3way`) is one symptom set: the agent has no framework
for "what change am I making, in what transaction, against what
baseline." Each fix shipped this week is a localized patch around a
hole the framework would fill structurally.

| Commit | Bandage | What Step 25 makes structurally impossible |
|---|---|---|
| `2d9de6c4edc` | `_git_diff_with_untracked` (intent-to-add dance to make new files visible to `git diff`) | Intent log IS the record; no post-hoc `git diff` capture, no untracked-file blind spot. |
| `5369db9fd4e` | `convert_record` manually `mk_path.unlink()`s `Makefile.DragonFly` after writing `overlay.dops`; runner adds a wall-clock circuit breaker (`_recent_successful_convert`) to detect re-defer loops | Convert is a single transactional intent set ("migrate to dops"). The "remove legacy" half is intrinsic to the intent, not a separate cleanup that can be forgotten. No loop possible. |
| `ccab8ebad88` | New `overlay_state` module to unify host/chroot classification because the two paths had drifted | A single substrate. Classification is a property of git HEAD + intent log replay, not of accumulated workspace state. |
| `surface_invariant` action in `overlay_state.assess_overlay` | Runtime check at *next* triage time for "overlay.dops + Makefile.DragonFly together" | Intent validator rejects contradictory intents at write time. The half-migration we saw on `multimedia/v4l_compat` today (agent emitted both `Makefile.DragonFly` AND `overlay.dops` in one run) is rejected before any file is written. |
| `300b7b1e96a` | `_lookup_bundle_target` fallback because jobs landed with `target=NULL` while the bundle had it | Tangential to 25 but related — same shape of "implicit state propagation across processes" that intents formalize. |
| `b376a58f47b`, `1776bc894ab`, `a77e2500a60`, `bfd0d68473b`, `ed8e97b6007` | Five-commit zigzag to make verify-fix call the dev-env primitive without killing the runner, finding `dportsv3`, or breaking PATH resolution | If verify replays the intent log (25e), there's no diff-apply path at all; no `git apply --3way`, no subprocess gymnastics, no PATH dependency on the verify side. |
| Today's `git apply --3way` staging leak (verify failure leaves `new file` entries in the index) | `--3way` implies `--index`; partial apply on new-file diff stages files before erroring | No `git apply` in the verify path. Intents replay deterministically; no partial apply, no staging side effect. |
| Today's accumulating env state across jobs (verify drift on gperf + v4l_compat — diff says "create new file" but env already has it from the agent's prior run) | The env's `ports/<origin>/` carries forward every agent edit forever | "Workspace lifecycle" below: each job resets to baseline on completion. Drift is structurally impossible. |
| Today's `Makefile.DragonFly + overlay.dops` half-migration emitted by the patch agent | Patch agent has no schema saying "you write a dops or a compat overlay, not both" | Intent grammar enforces it — there is no intent that writes a `Makefile.DragonFly` if `overlay.dops` is in scope (or vice versa). |
| `process_verify_requests` reconciler (runner polls a DB table because the tracker can't enqueue) | DB-mediated request channel because tracker can't reach the runner's queue | Tangential — same pattern works fine for intent submission. Step 25 doesn't change this. |

In aggregate: **eight of the past ten bugfix commits would not have been written** if the agent had been operating on intents the whole time. The recurring pattern is "the agent did X, we observed it via Y, the observation has a blind spot Z, ship a patch for Z." Intents short-circuit the observation — there's nothing to observe because the intent log already says what happened.

#### Workspace lifecycle (new — added 2026-05-25)

The verify-drift incidents on `archivers/liblz4`, `devel/gperf`,
and `multimedia/v4l_compat` revealed that the env's writable
overlay is an unbounded accumulator. Each agent run mutates
`ports/<origin>/` and leaves the edits in place; the next run (or
verify) sees the previous edits as "the baseline."

Step 25 introduces a clean two-tier state model:

- **Baseline** = git HEAD of the env's DeltaPorts checkout.
  Operator-controlled. Doesn't change without explicit operator
  action (or convert; see exception below).
- **Ephemeral** = intent log for the current job. Applied on top
  of baseline at job-start, captured at job-end (whether success
  or failure), then **discarded** with `git checkout HEAD --
  ports/<origin>/ && git clean -fd ports/<origin>/`.

The bundle's `analysis/intent_log.json` (or equivalent) is the
canonical record. Verify replays it against any env at the same
baseline. The "did the env happen to have leftover edits"
question disappears.

**The convert exception.** Convert's output is meant to persist
(triage immediately depends on the converted state). Two options
once Step 25 is live:

- (a) Convert is expressed as a single intent set committed to a
  local branch (`agent/convert/<origin>`) in the env's checkout.
  Reset preserves it. Operator promotes by merging the local
  branch to main.
- (b) Convert is special-cased: its intent log applies and is NOT
  reset. The intent log is still the canonical record; only the
  cleanup step skips. Operator promotes by reading the intent log
  and applying it to their own clone.

**Resolved (shipped):** convert is outside the intent layer
entirely — it authors `overlay.dops` directly via substrate-level
tools (`put_file`/`validate_dops`/`emit_diff`) and its output
persists; only patch/verify jobs reset to baseline. See
`docs/edit-intent-design.md` §6.

#### Sub-step changes from this scope expansion

- **25a** also has to cover: transaction semantics (begin/apply/
  rollback), workspace lifecycle policy, and the convert exception.
- **25b** the translator becomes the apply engine for the
  transaction. Intent emission, validation, application, and
  rollback are all in this module.
- **25c** `apply_intent` is the tool surface. A separate `commit`
  step (implicit in PATCH_OK) writes the intent log to the bundle
  and triggers workspace reset.
- **25e** is now the load-bearing slice for verify-drift. Renames
  from "diff capture via translator" to **"intent log as canonical
  record + verify replays log"**. Verify-fix's `apply_and_build`
  primitive grows an `intent_log_path` parameter as the
  replacement for `diff_path`.
- New **25g — workspace reset policy. Shipped.** Apply the
  baseline-vs-ephemeral split. Patch/verify jobs reset on
  completion. Convert special-cased (convert is outside the intent
  layer and its output persists). Operator gets a
  `dportsv3 dev-env reset-port ENV ORIGIN` manual escape hatch.

#### LOC estimate (revised)

~800 net additions; ~250 net deletions (prompt + retired
emit_diff + retired `--intent-to-add` helper + retired
`surface_invariant` runtime check). Larger than the original
estimate because the scope grew to include the transaction model.

#### Sub-steps

**25a — intent grammar design. Shipped.**

The grammar was designed end-to-end in `docs/edit-intent-design.md`
(now reconciled to dops-only). The canonical, machine-readable spec
is the per-intent JSON schemas under
`dportsv3/agent/edit_intent/schemas/`; the coverage matrix in
`docs/intent-surface-gaps.md` tracks which substrate shapes each
intent can create/modify/delete. The v0 grammar enumeration that
lived here (six edit intents + `convert_to_dops`, each with a
compat-mode and dops-mode translation) is superseded — the shipped
catalog is ten dops-only intents (see the Step-C note above). Refer
to the schemas + gap matrix rather than re-listing field shapes
here.

LOC: zero code; design doc only.

**25b — translator module + intent dispatcher. Shipped (dops-only).**

`dportsv3/agent/edit_intent/` as shipped:

```
__init__.py
grammar.py       # @dataclass per intent type
translator.py    # Translator(mode="dops").apply(intent) -> EditResult
_dops.py         # dops renderers (one per intent type)
schemas/         # per-intent JSON schema (auto-loaded by filename)
```

`Translator.apply(intent)` returns an `EditResult` carrying the
changed paths + the diff produced by *this specific intent*. This
is the substitute for the broken `emit_diff` flow — every intent
self-describes its change.

The v0 plan had a `_compat.py` sibling and resolved mode from
`classify_dops` at construction. Step C removed both: `Mode` is
`Literal["dops"]`, the constructor raises on any other mode, and
there is no compat renderer.

LOC: ~250 (grammar + translator + dops renderers).

**25c — new tool: `apply_intent`. Shipped.**

Replace today's mixed-surface edit tools (`put_file` against patch
files, `install_patches`, `validate_dops`, direct `put_file`
against `overlay.dops`) with a single tool the LLM calls:

```python
apply_intent(env, intent_json) -> {ok, kind, paths_changed, diff}
```

`put_file`, `install_patches`, and `validate_dops` stay in the tool
registry but are no longer exposed to the patch agent's prompt —
only the convert agent (whose job *is* to edit the overlay
directly) keeps them. The patch agent's tool surface shrinks to
`env_verify`, `materialize_dports`, `extract`, `get_file`, `grep`,
`apply_intent`, `dsynth_build`.

LOC: ~80 (tool wrapper + registry update).

**25d — patch prompt rewrite. Shipped.**

`PATCH_SYSTEM` loses the "Two kinds of patches" framing and the
dops vs compat decision tree. Replaces them with a short
description of the intent grammar (one line per intent type) and
points the agent at a new `intent_reference` tool for full
syntax. The "Mandatory opening procedure" reduces — `classify_dops`
is no longer something the agent has to think about.

Behavior parity check: re-run devel/gperf, devel/libuv,
archivers/liblz4 against the new prompt; assert the agent reaches
`rebuild_ok=true` on each. (gperf in particular: the agent should
emit `drop_patch{target: "patch-lib_getopt.c", reason: "obsolete:
upstream gperf-3.3 unconditionally includes <string.h>"}` instead
of a `put_file overlay.dops`.)

LOC: ~150 net deletion from the prompt (the mode-handling sections
were ~30% of `PATCH_SYSTEM`).

**25e — diff capture via translator, not git. Shipped.**

The empty-diff bug from `devel_gperf-20260523-094119Z` was caused
by `emit_diff` returning empty after `put_file` to `overlay.dops`
(hypothesis: the diff baseline is snapshotted at job-start, not
re-read at emit time). The translator-based path side-steps the
bug entirely: each `apply_intent` call returns its own diff, the
runner accumulates them, and `analysis/changes.diff` is the
ordered concatenation of intent diffs. `emit_diff` retires as a
patch-agent tool (kept for convert).

Tests: a port with two intents applied produces a single
`changes.diff` containing both diffs in order; the empty-diff
regression case (dops `put_file` equivalent → `drop_patch` intent)
produces a non-empty diff.

LOC: ~80 (runner-side accumulator + retirement of the
patch-agent emit_diff call).

**25f — telemetry + audit trail. Shipped.**

Each intent application emits a `intent_applied` telemetry event
(when Step 12's bus lands) or an `activity_log` row (in the
meantime) carrying the intent type, paths changed, success bool,
and rendered diff size. The tracker UI shows the intent sequence
on the bundle/job page so an operator can read "agent emitted 1
intent: drop_patch(patch-lib_getopt.c, reason=…)" without
grepping `analysis/patch.md`.

LOC: ~60 (logging + template).

#### LOC estimate

~620 net additions; ~150 net deletions (prompt + retired
emit_diff). Behavior-preserving for the cases the patch agent
already handles correctly; the gperf empty-diff class becomes
impossible.

#### Order

25a → 25b → 25c → 25e → 25g → 25d → 25f.

Design first (25a). Then translator/transaction engine (25b) —
testable in isolation against canned intent inputs and assertable
output diffs, no LLM needed. Then expose to the agent via the new
tool (25c) but keep the existing tools in the registry so the
prompt rewrite (25d) can be staged. Intent-log capture (25e) goes
before workspace reset (25g) so the bundle record exists before
state gets wiped. Workspace reset (25g) ships next; this is what
fixes verify-drift in production. Then swap the patch prompt
(25d). Telemetry (25f) last because it's a layer on top of
working behavior.

25e + 25g together are the verify-fix structural fix. Once both
land, the four bandages around verify (`--3way` quirks, env reset
before apply, drift detection, partial-staging cleanup) all retire
together.

#### Dependencies

- **Hard:** Step 20 (the convert agent's edit surface stays as-is;
  Step 25 only rewires the patch agent). Shipped.
- **Soft:** Step 24 (prompts/quickref consolidation) — easier to
  rewrite the patch prompt after the cleanup pass, since the
  duplicated dops material in `PATCH_SYSTEM` would otherwise have
  to be rewritten twice.
- **Soft:** Step 12 (telemetry bus) — 25f's intent telemetry plugs
  cleanly into the bus; without it, 25f writes to `activity_log`
  directly and gets re-plumbed later.

#### Why early in the priority order

The architectural collision the gperf bundle made concrete — patch
agent silently right on `needs_judgment` ports, silently wrong on
the boundary cases — gets worse the more ports we convert to dops.
Today the patch agent works because most ports are still compat.
Each new dops port is a new opportunity for silent wrongness.
Doing Step 25 sooner caps that risk before the dops/compat ratio
inverts.

The empty-diff bug from gperf is also load-bearing here: it's a
symptom of the same "the agent's edit surface is whatever it
guesses" problem. Step 25 makes the empty-diff class structurally
impossible rather than papering over it with a `emit_diff`
plumbing fix that would only survive until the next refactor.

#### Out of scope

- Rewriting the convert agent on top of intent. Convert's job *is*
  to author overlay.dops; it needs the substrate-level tools. The
  intent grammar is for patch (and any future agent whose job is
  "make a change to a port", not "design a port").
- A bidirectional intent ↔ raw-edit translator that lets operators
  hand-edit compat patches and have intents inferred. Not the
  problem we have.

### Step 27 — unified agent playbook library — shipped

> **Shipped end-to-end across 2026-05-26.** All seven sub-steps
> (27a-g) landed; Steps 19a and 19b's deliverables landed here
> too (subsuming Step 19 entirely). Live catalog: 24 markdown
> entries across `error-*`, `intent-*`, `convert-*`, and
> `toolchain-*` categories. 1092 tests pass.
>
> Commit chain (chronological):
> - 27a — `8b2801fdbdd`, `d91fcb2bb04` — `docs/kedb/` → `docs/agent-playbooks/`, `error-` prefix, README/TEMPLATE
> - 27b — `80c0192517a` — `dportsv3.agent.playbooks` module, selector, budget gate, `load_kedb` retired
> - 27c — `33f7f0312ef` — `intent_reference` returns schema + matching playbooks
> - 27d — `97eac8aa655` — seven intent recipes, `PATCH_INTENT_SYSTEM` trimmed
> - review — `488de85162e`, `8583c119cb7` — telemetry fix, wildcard cross-cutting entry
> - 27e — `1a955344014` — two convert recipes, `CONVERT_SYSTEM` trimmed
> - 27g — `46c6ae14ccd` — structural-vs-pattern boundary in module docstring, dops-quickref dup trimmed
> - 27f — `c7e1c865298` — `detect_toolchains()` + 11 toolchain playbooks
>
> The original plan text is preserved below for context — the
> design rationale that drove the work. The "Out of scope" section
> at the end still applies as a forward-looking statement on
> directions the library deliberately doesn't take.

The plan today has three parallel knowledge-attachment mechanisms,
each with its own naming, its own loader, and its own selector (or
lack of one):

- **Step 14 (KEDB metadata).** Reactive error catalog under
  `docs/kedb/`. Today: bulk-loaded into every triage/patch payload
  via `load_kedb`. Planned: frontmatter + classification filter +
  budget gate.
- **Step 19 (toolchain playbooks).** Proactive "local laws of
  physics" catalog under `scripts/generator/dportsv3/agent/playbooks/`,
  selected by mechanical toolchain detection (`autoconf`, `cmake`,
  etc.). Distinct directory, distinct loader, distinct selector.
- **`prompts.py` prose.** Recipe-style content embedded directly in
  Python strings — per-intent usage patterns, convert classification
  decision trees, the `dupe`/`add_patch` flow, "extending an inline
  `mk target` heredoc body" patterns, etc. Edited via code commits,
  accreting per port shape encountered.

A concrete forcing function: the patch agent needs recipes like
"use `replace_in_dops_block` to append a REINPLACE_CMD to an inline
`mk target` body" — procedural knowledge, not an error fix. That
shape has no natural home: too procedural for KEDB, not toolchain-
shaped for Step 19, ends up as another paragraph in
`PATCH_INTENT_SYSTEM`. Each new port shape adds another paragraph.
The structure is asking for unification.

#### Scope

One library, one loader, one tagged selector. All three current
mechanisms collapse into it. Categories are encoded in filename
prefix so the library is self-describing on `ls`:

```
docs/agent-playbooks/
  error-plist-mismatch.md           ← migrated from docs/kedb/
  error-freebsd-only-features.md
  error-dragonfly-source-patches.md
  error-prefer-dops-over-static-patches.md
  intent-replace_in_dops_block.md   ← migrated from prompts.py recipes
  intent-replace_in_patch.md
  intent-add_patch-from-source.md
  intent-drop_patch.md
  convert-target-directive.md       ← migrated from CONVERT_SYSTEM
  convert-classify-patch-domain.md
  toolchain-autoconf.md             ← Step 19 deliverables land here
  toolchain-cmake.md
  toolchain-meson.md
  …
  TEMPLATE.md
  README.md
```

(Decision in 27a: `docs/agent-playbooks/` vs
`scripts/generator/dportsv3/agent/playbooks/`. Co-locating with
agent code aids discoverability for that audience; placing under
`docs/` aids operator editing without a venv. Lean toward `docs/`
based on KEDB's existing location and the "operator-editable"
principle.)

#### Frontmatter convention

Every entry carries YAML frontmatter declaring its triggers + meta.
Triggers are AND'd within a kind (all listed classifications must
include the bundle's) and OR'd across kinds (matches if any trigger
kind fires). Empty list = wildcard for that kind. Empty trigger
block = always loaded (for fundamental references).

```yaml
---
triggers:
  classifications: [patch-error, compile-error]   # from triage
  intents: [replace_in_dops_block]                # from patch-flow tool surface
  toolchains: [autoconf]                          # from Step 19a's detect()
  convert_phases: [picking_target]                # for convert agent
  flows: [patch, convert, triage]                 # which agent role can see this
tags: [heredoc, post-patch-target]
priority: 100                                     # smaller = drop later under budget
est_tokens: 0                                     # computed at load time, 0 = recompute
---
# Known Pattern: …
```

Old KEDB entries without frontmatter default to
`{classifications: [], flows: [triage, patch], priority: 100}` —
wildcard, both agents see them. Migration is purely additive; no
existing entry breaks.

#### Selection

Selection happens **at payload-build time**, not at agent demand.
The runner knows enough at the moment it constructs the
triage/patch/convert payload to pick:

- Bundle's classification (from prior triage, if any).
- Detected toolchain (Step 19a's `detect(port_dir)` cached on the
  bundle).
- Intent surface for the flow (patch-flow exposes the 7 intent
  types; convert exposes none; triage exposes none).
- Convert phase context (which convert step is in progress).

Pseudocode:

```python
def load_playbooks(role: Literal["triage", "patch", "convert"],
                   *, classification: str | None = None,
                   toolchains: set[str] = (),
                   intents: set[str] = (),
                   convert_phase: str | None = None,
                   budget_tokens: int = 8000) -> str:
    candidates = [e for e in _ALL_ENTRIES if e.matches(
        role=role, classification=classification,
        toolchains=toolchains, intents=intents,
        convert_phase=convert_phase,
    )]
    candidates.sort(key=lambda e: e.priority)
    return _assemble_under_budget(candidates, budget_tokens)
```

This preserves prefix caching (deterministic selection on identical
context) and gives observability: the runner can log "selected N of
M playbooks, dropped K under budget."

#### Intent-driven suggestion via `intent_reference`

The cleanest surface for the "suggest playbooks for intent X" idea
we discussed pre-step: extend `intent_reference(intent_type=X)`
to return the JSON schema (from `grammar.py`) **plus** any
`intent-*` playbook entries tagged `intents: [X]`. Pure tag filter,
no LLM reasoning, no RAG, no novel infrastructure. The agent calls
the existing tool with the existing arg and gets back schema +
matching recipes.

This means baseline payload no longer needs the full intent-recipe
catalog inline. The agent pulls it on demand per intent it's about
to emit. Trade-off: agent pays one extra `intent_reference` call
per intent type used. Worth it because (a) it's already best
practice to call `intent_reference` before `apply_intent`, (b)
prefix cache stays warm across attempts on the same port shape.

#### What it subsumes

- **Step 14's KEDB-specific work** (frontmatter, classification
  filter, est_tokens, priority, budget gate) — folds into Step 27.
  Step 14's *system-prompt decomposition* (PATCH_SYSTEM sections,
  per-section telemetry) is separable and stays in Step 14; it's a
  different abstraction concern (prompt structure, not knowledge
  base).
- **Step 19's `playbooks/` directory + `detect()` + `load(tags)`
  loader.** Migrates to Step 27's library. Step 19's hand-authoring
  of 10 toolchain markdown files remains valid work that lands as
  `toolchain-*.md` in the new library.
- **Step 24's prompts/quickref consolidation.** Step 24 trims
  duplicated content from prompts.py against `dops_quickref.md`;
  Step 27 takes the next logical hop and moves recipe-style content
  to the library. 24 stays as the cosmetic pass; 27 is the
  architectural pass that gives the cosmetic work somewhere to land.

#### Sub-steps

In recommended order; each is independently shippable.

**27a — library skeleton + frontmatter convention.**

Create `docs/agent-playbooks/` (or final location per the decision
above). Move the 4 existing KEDB entries unchanged. Update
`TEMPLATE.md` with the full frontmatter shape including all trigger
kinds. Update `README.md` with the new file-naming convention and
selector model. Pure rename + scaffold; no behavior change yet
(`load_kedb` continues to bulk-load from the new location). One
commit, easy to bisect.

**27b — frontmatter parser + selector + `load_playbooks`.**

Implement the entry model, frontmatter parser (handle missing /
malformed gracefully with safe defaults), the selector function,
and a token estimator. Replace `load_kedb` call sites in
`build_triage_payload` / `build_patch_payload` /
`build_convert_payload` with `load_playbooks(role=..., ...)`.
Telemetry: emit a `playbooks_selected` activity row per payload
build with included/dropped counts, total tokens, dropped reasons
("budget" vs "no trigger match"). Keep all current entries with
wildcard triggers so this is behavior-preserving.

**27c — `intent_reference` returns matching playbooks.**

Extend `intent_reference(intent_type=X)` to also return playbook
entries tagged `intents: [X]`. Update the tool result shape to
carry both `schema` and `playbooks` arrays. Patch-agent prompt is
updated to reference this; no recipe prose stays in the prompt for
intents that have a playbook entry.

**27d — migrate intent-related prose from `prompts.py`.**

Extract per-intent recipe content from `PATCH_INTENT_SYSTEM` into
`intent-*.md` files with `intents: [X]` triggers. Includes
`intent-replace_in_dops_block.md` covering the "extend a heredoc
body by replacing the last line of the body" use case (the recipe
shape that motivated this step). Trim the prompt accordingly.

**27e — migrate convert-related prose.**

Extract from `CONVERT_SYSTEM`: target directive picking →
`convert-target-directive.md`; framework vs upstream classification
decision tree → `convert-classify-patch-domain.md`. Trigger by
`flows: [convert]` and `convert_phases: [...]` where appropriate.
Trim the prompt.

**27f — Step 19's toolchain playbook authoring, in the new library.**

The 10 hand-authored toolchain playbooks from Step 19's
deliverables (`toolchain-autoconf.md`, `toolchain-cmake.md`, etc.)
land in the unified library. Step 19a's `detect()` returns the tag
set the selector consumes. The 10 markdown files remain Step 19's
authoring work; their *home* is Step 27.

**27g — drop redundant prose from `prompts.py`, audit pass.**

After 27d/e, sweep `prompts.py` for any remaining prose that's
pattern-matched by a playbook category but wasn't migrated.
Document the boundary explicitly in `prompts.py`'s module
docstring: "this file holds STRUCTURAL prompt content — loop
shape, tool surface, refusal codes, output format. Pattern-shaped
content (intent recipes, port-toolchain patterns, error fixes)
lives in `docs/agent-playbooks/`."

#### Order and dependencies

- **Hard:** Step 25 (intent DSL) — 27c's `intent_reference`
  extension is meaningless without intents existing. Shipped.
- **Soft:** Step 24 (prompts/quickref cleanup) — does some of the
  cosmetic trim 27 makes structural. Land 24 first against the
  current (smaller) prompt, then 27's deeper trim.
- **Subsumes:** Step 14's KEDB metadata work, Step 19's loader +
  directory. Step 19's *authoring* (the 10 toolchain files)
  remains valid as 27f.
- **Order within 27:** 27a (skeleton) → 27b (loader, behavior-
  preserving) → 27c (intent_reference + suggestion) → 27d + 27e
  (prompt migration) → 27f (toolchain authoring, parallel-shippable
  with 27d/e) → 27g (audit pass).

#### Why now

Three concrete forcing functions:

1. **Step 25 just shipped** — intents are the natural unit for
   suggestion, and `intent_reference` is the natural tool surface
   for tag-filtered lookup. Building 27 against intent + tool
   surfaces that already exist is much cheaper than retrofitting.
2. **Two pending entries on the runway** — an
   `intent-replace_in_dops_block.md` recipe covering heredoc-body
   extension, and a `dsynth_log` failed-phase tagging. Both would
   otherwise land as more paragraphs in `PATCH_INTENT_SYSTEM`,
   baking the old shape deeper.
3. **Prompt cruft is a current cost, not a future one.** Recent
   patch-flow runs have burned attempt budget thrashing on cases
   where the recipe the agent needed wasn't anywhere it would look.
   Centralizing the knowledge surface and making `intent_reference`
   the discovery primitive directly addresses the failure mode.

#### Out of scope (and future directions worth keeping in mind)

The three items below were deliberately deferred. They're
interesting in their own right and the architecture leaves room
for each — listed here as forward-looking directions the library
could grow into if the conditions warrant.

- **LLM-driven playbook discovery / RAG / embedding search.**
  Deterministic tag filter only today. The selector's existing
  axes (classifications, intents, toolchains, convert_phases,
  flows) handle the catalog at ~24 entries comfortably. If the
  volume ever exceeds what filename + frontmatter handles
  (hundreds of entries, or if catalog-author intent becomes
  hard to encode in tags), the natural next step is semantic
  retrieval: embed each entry's body, embed the query context
  (failure log + classification + toolchain), select top-K by
  cosine similarity within the tag-filtered candidate set. The
  tag filter stays as a coarse pre-filter; embeddings refine
  within the matched set. Revisit when the catalog grows past
  ~50-100 entries or when operators start wanting "find me
  entries about X" without browsing.

- **Editing playbooks from within the runtime.** The agents read
  the library today; only operators write to it. The
  authoritative ownership is human, which keeps the library
  auditable and stable. A future direction worth considering
  carefully: an "agent learned a new pattern" feedback loop where
  the agent proposes a new entry (or a refinement to an existing
  one) at the end of an attempt, the runner stages it as a
  pull-request-shaped artifact, and the operator approves
  before it lands. This preserves the human-write authority
  while letting the agent contribute knowledge from real
  failures. The risk is drift: agents writing for agents
  produces playbooks that pattern-match against LLM idioms
  rather than build-system reality. Revisit when there's
  enough operator capacity to review agent-proposed entries
  and enough corpus to evaluate whether the proposals are
  actually useful.

- **Versioning / deprecation policy.** Markdown + git history is
  fine until volume forces the question. Two scenarios that
  would force it: (a) an entry's recipe becomes incorrect (e.g.
  upstream tooling changes) and we want to keep the historical
  text reachable while flagging the current-state, (b) entries
  carry per-DragonFly-release scope (`triggers.platform_release:
  [dragonfly-6.x]`) and need a sunset mechanism. Until either
  scenario lands, "edit the file, commit, done" is the policy.

#### Verification

- 27a: ports of existing KEDB entries continue to load identically
  (byte-identical output of `load_kedb` before vs. `load_playbooks`
  with wildcard triggers after, for triage/patch payloads on a
  fixture bundle).
- 27b: telemetry shows playbook selection for known bundles
  matches expected sets; budget gate drops lowest-priority entries
  first when forced under budget.
- 27c: `intent_reference(intent_type="replace_in_dops_block")`
  returns schema + the heredoc-extension recipe entry; same call
  against an intent type with no playbook returns schema + empty
  list.
- 27d-g: integration tests assert per-flow payload size shrinks
  (prompts trim) while behavior is preserved on a corpus of
  fixture bundles.

---

### Step 36 — typed phase results: replace markdown-regex with a `PhaseResult` contract — shipped

Landed across 36-1..36-7 in commits `a046f781530`, `07c3995c29f`,
`fb9df8d5abe`, `aeca24185d5`. Producer + consumer + hard cutover all
in place; `analysis/<phase>_result.json` is now the structural source
of truth; markdown stays as the human-readable render. 1573 tests
pass. Two design deviations from the spec below: (a) token spend is
three flat ints (`tokens_prompt`/`tokens_completion`/`tokens_total`)
rather than a single `tokens_used` because `proposed_fix.py` needs
the breakdown; (b) `load_phase_result` signature is
`(bundle_dir, bundle_id, phase, cls)` mirroring `read_bundle_text`
so filesystem-mode bundles work alongside artifact-store bundles.


Surfaced during the lang/python311 convert-loop analysis (bundles
`lang_python311-20260531-{084200Z, 094114Z, 095900Z}`). The convert
agent ran three times against a port whose underlying failure was
plist-drift (a packaging error unrelated to substrate state). Each
attempt produced a syntactically valid `overlay.dops`, passed
`validate_dops`, declared `rebuild_ok=true`, and was then rejected by
`_verify_conversion` at `reapply` with `reason_code=reapply_failed`.
The deeper finding was *why convert was running at all*: triage had
classified-and-deferred, the convert agent was dispatched **with no
view of triage's classification or root cause**, and produced an
overlay that couldn't address the real problem.

Tracing inter-phase context-passing showed the mechanism is **"each
phase reads known relpaths out of the bundle artifact store and
re-parses prose to fish out fields."** There is no typed contract:

- Triage writes `analysis/triage.md` (LLM prose) AND
  `analysis/triage.json` (audit metadata: classification, confidence,
  usage, model).
- Patch reads `analysis/triage.md` via `read_bundle_text`, then runs
  `parse_triage_output` (a regex) to extract `Classification:` and
  `Confidence:` from the markdown. The `triage.json` next door is
  write-only.
- Convert reads **nothing**. Its payload
  (`convert.build_convert_payload`) only takes `origin`, `repo_root`,
  `classified_record` (substrate-only classifier from
  `classify_inventory`), `deterministic_result`, the dops quickref,
  and convert-flow playbooks. No triage output is plumbed in.
- Cross-bundle history (`prior_patch_bundle_ids`, `port_bundle_history`)
  is a separate DB query plus more `read_bundle_text` calls — no
  unified context object.

Two problems compound:

1. **Brittle implicit contract.** Nothing enforces what triage must
   produce or what patch can consume; the spec is whichever regex
   `parse_triage_output` happens to use. Prompt rewrites silently
   break downstream consumers.
2. **Asymmetric coverage.** Patch reads triage. Convert reads
   nothing. The mechanism only exists where someone hardcoded it.
   Adding a new consumer (convert wanting triage's classification)
   requires plumbing every layer by hand — `enqueue_convert_job`
   doesn't carry triage context, `process_convert_job` doesn't
   thread it, `build_convert_payload` doesn't render it.

#### Goal

Replace prose-with-regex with a typed `PhaseResult` contract: each
phase writes a single canonical `analysis/<phase>_result.json` matching
a versioned schema; downstream phases load the typed object instead of
re-parsing markdown. The markdown artifacts stay as **human-readable
renders**, not the source of truth.

After Step 36:

- The convert agent has access to triage's classification, confidence,
  root cause, and evidence excerpt — and can refuse a job whose root
  cause isn't substrate-related, instead of burning attempts on the
  wrong layer.
- The patch agent's payload builder stops regex-parsing
  `analysis/triage.md`.
- Adding a new shared field is a schema change (one dataclass field +
  one producer line + one consumer line), not a relpath-hunt across
  three files.
- Future tracker UI surfaces (per-bundle "what triage saw / what convert
  decided / what patch fixed") render from the typed JSON without
  inferring structure from prose.

#### Schema layer — `dportsv3/agent/phase_result.py` (new)

Single file, ~80 LOC. Stdlib `dataclasses` + `dataclasses.asdict` to
avoid pulling Pydantic into the agent path. Frozen dataclasses, one per
phase, each with a `schema_version: int` first field.

```python
@dataclass(frozen=True)
class TriageResult:
    schema_version: int                  # = 1
    classification: str                  # "patch-error" etc.
    confidence: str                      # "high" | "medium" | "low"
    root_cause: str                      # extracted from ## Root Cause
    evidence_excerpt: str                # extracted from ## Evidence, ≤2KB
    error_signature: str | None          # sha256[:16] of first error line
    tier: str                            # "AUTO" | "ASSIST" | "MANUAL"
    classifier_version: str              # prompt/model hash for reproducibility
    tokens_used: int
    model: str

@dataclass(frozen=True)
class ConvertResult:
    schema_version: int                  # = 1
    status: str                          # "verified" | "reapply_failed" | ...
    reason_code: str | None              # populated on failure
    reapply_ok: bool
    overlay_sha256: str | None           # what the agent wrote
    files_removed: list[str]
    diag_tail: str | None
    tokens_used: int

@dataclass(frozen=True)
class PatchResult:
    schema_version: int                  # = 1
    rebuild_ok: bool
    attempts: int
    intents_applied: int                 # count of apply_intent ops
    tokens_used: int
    status: str                          # "success" | "needs-help" | "budget-exhausted"
```

#### Storage — same artifact store, new relpath convention

- `analysis/triage_result.json` — `TriageResult`
- `analysis/convert_result.json` — `ConvertResult`
- `analysis/patch_result.json` — `PatchResult`

Existing `analysis/<phase>.md` files stay as human-readable renders
written from the typed result + LLM prose. Existing
`analysis/triage.json` audit file is replaced wholesale by
`triage_result.json` (no migration; the next bundle's triage just
emits the new shape; legacy bundles' missing-result lookups return
None and the consumer degrades gracefully — "no upstream context
available" is a valid PhaseContext shape).

#### I/O helpers in the same module

```python
def write_phase_result(bundle_id: str, phase: str, result) -> None:
    data = json.dumps(asdict(result), indent=2).encode("utf-8")
    artifact_store_put(bundle_id, f"analysis/{phase}_result.json", data, "json")

def load_phase_result(bundle_id: str, phase: str, cls) -> Any | None:
    raw = read_bundle_text(None, bundle_id, f"analysis/{phase}_result.json")
    if not raw:
        return None
    payload = json.loads(raw)
    if payload.get("schema_version") != _expected_version(cls):
        raise PhaseResultVersionMismatch(phase, payload.get("schema_version"))
    return cls(**payload)
```

Mirrors the existing `artifact_store_put` / `read_bundle_text`
primitives; no new storage path, no new HTTP route, no DB change.

#### Producer side — where it slots in

Markdown→typed conversion runs **once, at write time** in the
producer. The existing regex extractors (`parse_triage_output`,
`_md_section`) stay — they just run in the producer instead of every
consumer.

```python
# at end of process_triage_job, after write_triage_outputs
parsed = parse_triage_output(triage_md)
result = TriageResult(
    schema_version=1,
    classification=parsed.get("classification") or "unknown",
    confidence=parsed.get("confidence") or "low",
    root_cause=_md_section(triage_md, "Root Cause"),
    evidence_excerpt=_md_section(triage_md, "Evidence")[:2000],
    error_signature=_compute_error_signature(errors_text),
    tier=tier_for(parsed.get("classification"),
                  parsed.get("confidence")).name,
    classifier_version=_classifier_version(),
    tokens_used=usage.total_tokens,
    model=model,
)
write_phase_result(bundle_id, "triage", result)
```

Same idea in `_verify_conversion` (emit `ConvertResult` whether verify
passed or failed — both shapes carry `status` + optional `reason_code`)
and in `process_patch_job` (emit `PatchResult` at attempt-loop end).

Future prompt rewrites only break the producer's extractor; consumers
read typed fields and don't see the prose.

#### Consumer side — `build_patch_payload` becomes

Today:

```python
triage_md = read_bundle_text(bundle_dir, bundle_id, "analysis/triage.md")
parsed = parse_triage_output(triage_md)
triage_classification = parsed.get("classification")
```

After:

```python
triage = load_phase_result(bundle_id, "triage", TriageResult)
triage_classification = triage.classification if triage else None
```

`build_convert_payload` gains the same load and renders an `## Original
build failure (from triage)` section from `triage.classification`,
`triage.root_cause`, `triage.evidence_excerpt`. That section is what
unblocks the python311 class of port — convert sees plist-drift in the
triage context and can either route to a minimal substrate-only overlay
(don't speculate beyond compat artifacts) or refuse the job with an
escalation hint ("substrate conversion won't help; route to MANUAL").

#### Plumbing for the convert chain specifically

To make triage→convert work end to end, three small additions in
`runner.py`:

1. `enqueue_convert_job` learns a `triage_result_present: bool` flag (or
   simply: always assume the triage result is on the bundle at the
   relpath above — no field needed; convert reads it from the bundle
   artifact store directly via `load_phase_result(bundle_id, "triage",
   TriageResult)`).
2. `process_convert_job` reads the typed `TriageResult` (if any) and
   passes it to `_run_llm_conversion`.
3. `build_convert_payload` gains a `triage_result: TriageResult | None`
   parameter and renders the new section when present.

~30 LOC. The bundle is already the shared store; the typed result is
already addressable from any phase; convert just stops being blind.

#### Out of scope

- **Migrations.** Old bundles' `analysis/triage.json` is not converted.
  `load_phase_result(...)` returns `None` on absence; consumers
  degrade gracefully ("no upstream context available" is a valid
  `PhaseContext` shape — same path consumers take on operator-fired
  convert jobs that have no bundle attached).
- **DB schema changes.** Phase results live in the bundle artifact
  store, addressed by the existing
  `(bundle_id, relpath) → blob_sha256` index.
- **Replacing the artifact store.** Same routing (`artifact_store_put`
  / `read_bundle_text`), same DB tracking. The change is purely about
  typing the *content* of what we write.
- **Tracker UI work.** The typed JSON makes future per-bundle
  "what triage saw / what convert decided / what patch fixed" panels
  trivial, but those panels are Step 16 territory, not this step.

#### Dependencies

- **Independent of Step 21** (DB consolidation) — phase results don't
  touch SQL.
- **Independent of Step 31** (single-service consolidation) — same
  HTTP surfaces are used.
- **Upstream of Step 25** in spirit but not strictly: Step 25's
  edit-intent DSL produces structured agent output, this step produces
  structured *phase* output. Both move the system from "prose with
  regex" to "typed contract." Doing this first makes the patch agent's
  intent_log a candidate to be folded into `PatchResult` (via an
  `intent_log: list[IntentRecord]` field), which is a cleaner
  consumption surface than the current `analysis/intent_log.json`
  separate file.
- **Composes with Step 26** (lifecycle hardening): once phase results
  are typed, the FSM can read them at terminal transitions (e.g.
  `bundle_branch_dropped` includes `reason_code` from
  `ConvertResult.reason_code`) without scraping activity-log JSON.

#### LOC estimate

Small — `phase_result.py` (~80) + producer wiring in three places
(~30) + consumer-side replacement of `parse_triage_output` callers
(~20) + convert-chain plumbing for the python311 class (~30) + tests
covering schema round-trips and the
"convert-sees-triage-classification" path (~120). Total ~280 LOC.
No prompt changes; no UI changes; no DB changes. The leverage comes
from making the contract explicit, not from new code.

#### Sub-steps

Each sub-step is a self-contained commit. 36-1 through 36-4 ship
producers with no consumers — safe to land in isolation, the new
JSON sits next to the old artifacts unused. 36-5 and 36-6 turn
consumers on. 36-7 closes out the old code path. Tests (36-8) ride
alongside each step.

##### 36-1 — schema module + I/O helpers

New file `scripts/generator/dportsv3/agent/phase_result.py` (~100 LOC):

- Frozen `dataclass`es: `TriageResult`, `ConvertResult`, `PatchResult`,
  each with `schema_version: int = 1` first field.
- `PhaseResultVersionMismatch(Exception)`.
- `write_phase_result(bundle_id, phase, result)` —
  `json.dumps(asdict(result))` →
  `artifact_store_put(bundle_id, f"analysis/{phase}_result.json", …)`.
- `load_phase_result(bundle_id, phase, cls)` —
  `read_bundle_text(None, bundle_id, …)` → parse → version-check →
  `cls(**payload)`.

**Circular-import gotcha:** `artifact_store_put` + `read_bundle_text`
live in `agent/runner.py`. To avoid pulling runner into phase_result,
import them **lazily inside the helper functions** (same pattern the
runner already uses for several cross-module loads).

Risk: none. Touches: new file + new test.

##### 36-2 — `TriageResult` producer

File: `scripts/generator/dportsv3/agent/runner.py`,
function: `_write_triage_audit_harness` at line 2195.

Today it writes `analysis/triage.json` with
`{classification, confidence, snippet_rounds, tokens_used, model, via}`.
Replace that body with:

1. Build `TriageResult` from the already-typed `result` (it's
   `dportsv3.agent.triage.TriageResult`, carries classification +
   confidence + usage).
2. Extract `root_cause` and `evidence_excerpt` from the just-written
   `analysis/triage.md` by calling `_md_section(...)`. **`_md_section`
   doesn't exist in runner.py** — it lives in
   `delivery/orchestrator.py:226`. Move `_md_section` + `_md_inline`
   to a new `dportsv3/agent/markdown.py` shared util so both
   `phase_result` producers and the existing delivery code import
   from one place.
3. Compute `error_signature` via `_compute_error_signature`
   (runner.py:602) on `logs/errors.txt`.
4. Compute `tier` via `decide(...)` (`agent/decision.py`) or
   `tier_for(...)` (policy module).
5. Drop the old `triage.json` write; call
   `write_phase_result(bundle_id, "triage", result_obj)`.

Tests touched: anything asserting `triage.json` shape — grep
`tests/` for it.

Risk: low. Touches: one function in runner + shared markdown util.

##### 36-3 — `PatchResult` producer

File: `runner.py`, function: `_write_patch_audit_harness` at line 3107
(writes `patch.md`, `rebuild_proof.json`, `patch_audit.json`).

Add at the end:

```python
result_obj = PatchResult(
    schema_version=1,
    rebuild_ok=bool(proof_payload.get("rebuild_ok")),
    attempts=len(result.attempts),
    intents_applied=…,                 # count from intent_log if available
    tokens_used=result.usage.total_tokens,
    status=result.status,
)
if bundle_id:
    write_phase_result(bundle_id, "patch", result_obj)
```

Keep `rebuild_proof.json` and `patch_audit.json` — they have separate
consumers (verify, UI) outside the phase-result contract. Those can
be retired in a later step.

Risk: low. Touches: one function in runner.

##### 36-4 — `ConvertResult` producer

File: `runner.py`. Two emission sites:

**(a) `_verify_conversion` at line 4109** — both the success tail
(line 4253) and the `_fail()` inner helper (line 4142):

- Success: `ConvertResult(status="verified", reapply_ok=True,
  overlay_sha256=sha256(overlay_bytes), files_removed=[],
  diag_tail=None, …)`.
- `_fail`: `ConvertResult(status=reason_code, reapply_ok=False,
  overlay_sha256=…, diag_tail=extra.get("diag_tail"),
  reason_code=reason_code, …)`.

`bundle_id` is reachable inside `_verify_conversion` via
`job.get("bundle_id")`.

**(b) `_rollback_env_after_convert_failure` at line 3834** — same
shape with `status="llm_convert_failed"`. Add a `bundle_id`
parameter, mirroring the `job_id` parameter added during the
`afe93b96a34` follow-up.

Risk: low. Touches: two functions in runner.

##### 36-5 — patch consumer swap

File: `runner.py`, function: `build_patch_payload` at lines
1652–1659.

Replace:

```python
triage_md = read_bundle_text(bundle_dir, bundle_id, "analysis/triage.md")
if triage_md:
    parsed = parse_triage_output(triage_md)
    cls = parsed.get("classification")
    if cls:
        triage_classification = cls
```

with:

```python
triage = load_phase_result(bundle_id, "triage", TriageResult)
triage_classification = triage.classification if triage else None
```

**Second consumer:** `agent/steps.py:794-810` (hand-fired patch
tier-derivation path). Same swap —
`services.read_bundle_text + services.parse_triage_output` →
`load_phase_result`.

Risk: medium. Touches: hot-path payload builder + `steps.py`.

##### 36-6 — convert chain plumbing (the python311 fix)

Files:

1. **`agent/convert.py:68`** — `build_convert_payload` gains
   `triage_result: TriageResult | None = None`. When non-None, append
   a `## Original build failure (from triage)` section rendering
   `classification`, `confidence`, `root_cause`, `evidence_excerpt`.
2. **`runner.py:3765`** — call site inside `_run_llm_conversion`.
   Load the triage result and pass it:

   ```python
   triage = load_phase_result(
       job.get("bundle_id"), "triage", TriageResult,
   )
   payload = convert_mod.build_convert_payload(
       …,
       triage_result=triage,
   )
   ```

3. **`runner.py:2064`** — `enqueue_convert_job`: no signature change
   needed; convert reads triage from the bundle artifact store
   directly (the `bundle_id` is already plumbed via the `.job` file).
   Document this in the docstring.

Risk: medium. Touches: `convert.py` signature + caller.

##### 36-7 — hard cutover: delete the regex parser

Files:

1. **`runner.py:1365`** — delete `parse_triage_output`.
2. **`runner.py:3489`** — remove
   `parse_triage_output=parse_triage_output` from
   `PatchAgentServices` construction.
3. **`agent/steps.py:676`** — remove the `parse_triage_output` field
   from the `PatchAgentServices` dataclass.

The `analysis/triage.md` artifact stays (rendered for humans,
embedded in patch payload via `context.py:496` as a human-context
prose-include; that's a context surface, not a parsing surface).

Risk: low (after 36-5 + 36-6 land). Touches: three deletions.

##### 36-8 — tests

1. **New** `tests/test_phase_result.py` — schema round-trips,
   missing-file returns `None`, version mismatch raises.
2. **New** `tests/test_convert_payload_includes_triage.py` — given a
   bundle with a written `TriageResult`,
   `build_convert_payload(…, triage_result=…)` renders the new
   section; without it the section is absent.
3. **Updated** existing tests asserting `triage.json` shape — grep
   `tests/` and refit to `triage_result.json`.
4. **Updated** any test importing `parse_triage_output` — refit to
   `load_phase_result`.

Risk: concurrent with each step. Touches: test files only.

#### Landing order

| Step | Risk | Notes |
|---|---|---|
| 36-1 | none | new file + new test |
| 36-2 | low | producer-only; consumer still reads the .md |
| 36-3 | low | producer-only; no consumer until later |
| 36-4 | low | producer-only |
| 36-5 | medium | first consumer flip — patch payload builder + steps |
| 36-6 | medium | convert sees triage — the python311 fix |
| 36-7 | low | cleanup deletes; only safe after 36-5 + 36-6 |
| 36-8 | low | rides alongside each step |

---

### Step 37 — compose-time patch drift: handler-side defer + patch-side relevance pass — shipped

Landed across 37-1..37-4 in commits `7664cefad85`, `22c7419b091`,
`30064a0295a`, and the 37-4 commit at the head of the branch
(playbook + per-verdict ESCALATE_MANUAL routing + lifecycle
transition + manual_handoff reason). Convert now ships partial
overlays with `DeferredPatch` context; patch agent emits per-patch
verdicts; bundles route to MANUAL only on `escalated` verdicts
(rebuild_ok=true with all-regenerated-or-dropped is full
agent_fixed). 1620 tests pass.

Surfaced on the lang/python311 class of port. When the upstream
pkg-plist churns between releases, the framework-layer
``diffs/pkg-plist.diff`` carries hunks whose context drifts off the
new upstream lines. Convert produces a syntactically-valid
``overlay.dops`` that references the diff; compose reapply tries to
apply the diff and rejects several hunks (typical: `Hunk #N failed at
LLL`); ``_verify_conversion`` fails with ``reason_code=reapply_failed``
and the bundle dies at ``convert_gave_up``. Patch never runs because
the substrate isn't dops-converted yet (the partial overlay is wiped
on rollback), and even if it ran the patch agent has no fixture
mechanism to fix a compose-time framework patch.

The recurring drift is the dominant failure mode for big-port
classes (python*, perl*, php*, anything that maintains a substantial
DragonFly-vs-FreeBSD plist delta). Maintaining a wholesale
``dragonfly/pkg-plist`` per port isn't viable — they're hundreds of
KB and churn upstream. Step 37 unblocks the chain by making convert
ship a *partial* overlay (omitting the rejecting patches) and
handing the rejected patches to the patch agent as **intent, not
authority**.

#### Premise

A deferred ``diffs/*.diff`` is not "fix this so it applies." It's
"figure out what this patch was doing semantically, decide whether
that intent is still relevant against current upstream, and act."
Three outcomes per deferred patch:

1. **Still relevant, just stale context** — patch agent writes a
   fresh diff achieving the same intent.
2. **No longer relevant** — patch agent drops the patch with a
   one-line rationale (upstream removed the lines / changed shape).
3. **Partially relevant** — patch agent writes a smaller patch
   covering the still-applicable subset.

Per-patch verdicts (regenerated / dropped / escalated) let the
bundle progress even if a subset escalates — operator picks up only
the unresolved subset, not the whole port.

#### Goal

Convert produces a partial overlay that composes successfully.
Patch agent receives the deferred patches with semantic context and
emits a verdict per patch. Bundle "succeeds" once every deferred
patch has a verdict; operator surface activates only on per-patch
escalations, not on the whole port.

#### Scope (in)

- **Handler-side** (``_verify_conversion``): detect hunk-reject
  failures from compose's stdout, drop the offending ``patch apply
  diffs/<file>.diff`` line from ``overlay.dops``, retry compose. Cap
  the iterative drop (e.g. 3). Record the dropped patches with
  enough context for patch to think semantically.
- **Typed schema additions**: ``ConvertResult.deferred_patches:
  list[DeferredPatch]``; ``PatchResult.deferred_verdicts:
  list[DeferredVerdict]``.
- **Patch payload**: new ``## Deferred from Convert`` section
  rendering each deferred patch's path + original content + reject
  summary + target file.
- **Patch prompt clause**: relevance-check task with the three-
  outcome verdict shape.
- **Convert/patch playbooks**: a new ``convert-deferred-patch-
  relevance.md`` covering the semantic-intent framing.

#### Scope (out)

- **Convert agent prompt changes.** Convert keeps doing what it does
  — translate the substrate. The defer machinery is entirely
  handler-side; the agent doesn't need to know about it.
- **Maintaining wholesale ``dragonfly/<file>`` replacements** as an
  alternative. Not viable for big-port classes; this step makes the
  diff-based approach robust to drift instead.
- **Compose-time patch regeneration tooling.** The patch agent uses
  the existing ``apply_intent`` machinery (``add_patch`` /
  ``replace_in_patch``) — no new tool surface.
- **Lifecycle changes.** Convert still ends at ``CONVERT_OK`` on
  partial success; patch fires as today's resume-deferred-triage
  path enqueues it.

#### Data flow

```
Convert agent  →  overlay.dops (full)
       ↓
Handler reapply  →  rc=2, "Hunk #N failed at LLL" in stdout
       ↓
Handler parse + drop "patch apply diffs/pkg-plist.diff", retry
       ↓
Reapply ok  →  ConvertResult.deferred_patches = [{path, content,
                                                  rejects, target}]
       ↓
CONVERT_OK  →  fresh triage  →  patch enqueued
       ↓
Patch payload carries "## Deferred from Convert" with semantic ctx
       ↓
Patch agent per-patch verdict:
  - regenerated → apply_intent(replace_in_patch or add_patch)
  - dropped     → no edit; rationale recorded
  - escalated   → no edit; reason recorded
       ↓
PatchResult.deferred_verdicts persisted; bundle resolves
```

#### Sub-steps

Each sub-step is a self-contained commit. 37-1 unlocks convert
*alone* (partial-success + deferred_patches recorded but patch
doesn't act on them yet — same UX as today but bundle status reads
honestly). 37-2 adds the per-patch context. 37-3 turns the patch
agent on. 37-4 closes the loop with playbooks + tests.

##### 37-1 — handler-side parse + drop + retry (no patch consumption yet)

Files: ``dportsv3/agent/runner.py`` (``_verify_conversion``).

- New helper ``_parse_compose_rejects(stdout, stderr)`` returns
  ``list[{path, rejected_hunks}]`` from compose's diag.
  Recognizes ``Hunk #N failed at LLL`` and the corresponding
  ``patching file X`` / ``--- a/diffs/<file>.diff`` lines.
- New helper ``_drop_patch_apply_from_overlay(overlay_text, path)``
  removes the line referencing the dropped diff. Lossless edit; no
  re-parse of dops needed.
- Wrap the existing ``mat = worker.materialize_dports(...)`` call in
  a bounded loop: on hunk-reject shape, drop one patch, retry.
  Cap at ``DP_HARNESS_CONVERT_MAX_DROPS`` (default 3).
- Plumb the recorded drops onto the job dict so
  ``_write_convert_phase_result`` can include them in
  ``ConvertResult``.
- ``deferred_patches: list[str]`` (just paths for now — rich context
  comes in 37-2).

End state after 37-1: convert succeeds on python311 if exactly 1-3
diffs/*.diff fail with rejects; bundle moves to ``CONVERT_OK`` with
``deferred_patches`` visible in the activity feed.

Risk: low. Behind a flag (``DP_HARNESS_CONVERT_DEFER_DROPS``) if
caution wanted; otherwise the drop only happens on the specific
hunk-reject shape, so non-reject failures still die as today.

##### 37-2 — rich deferred-patch context

Files: ``dportsv3/agent/phase_result.py``,
``dportsv3/agent/runner.py``.

- Promote ``deferred_patches`` field on ``ConvertResult`` from
  ``list[str]`` to ``list[DeferredPatch]``:
  ```python
  @dataclass(frozen=True)
  class DeferredPatch:
      path: str               # diffs/pkg-plist.diff
      target_file: str        # pkg-plist (inferred from --- a/X line)
      original_content: str   # full diff text (capped at e.g. 16KB)
      reject_summary: str     # "Hunks #1 #3 #4 failed at 249, 2929, 2972"
  ```
- Handler reads each dropped diff file's content (already on disk,
  not deleted) before retry, attaches it to the in-memory list.
- ``_write_convert_phase_result`` serializes the typed list.
- ``analysis/convert_result.json`` schema bump (``schema_version=2``);
  ``load_phase_result`` handles the bump via the existing
  ``PhaseResultVersionMismatch`` path.

End state: ``convert_result.json`` carries enough context for a
human (and 37-3's patch agent) to do the semantic-relevance check
without re-reading the bundle.

Risk: low. Producer-only.

##### 37-3 — patch payload + agent prompt

Files: ``dportsv3/agent/context.py`` (new
``DeferredFromConvertSection``), ``dportsv3/agent/prompts.py``
(``PATCH_INTENT_SYSTEM`` clause).

- New section in ``PATCH_SECTIONS`` (priority between
  ``TriageSummarySection`` and ``PriorAttemptsSection``):
  ```
  ## Deferred from Convert
  Convert produced a partial overlay; the following framework
  patches were dropped because their hunks rejected against the
  current upstream. For EACH entry, decide its relevance against
  current upstream and emit a per-patch verdict (regenerated /
  dropped / escalated).
  
  ### diffs/pkg-plist.diff → pkg-plist
  Reject summary: Hunks #1 #3 #4 failed at 249, 2929, 2972
  Original content:
  ```diff
  ...
  ```
  ```
- Reads the typed ``DeferredPatch`` list via ``load_phase_result``
  from the originating convert bundle.
- Prompt clause in ``PATCH_INTENT_SYSTEM``: one paragraph framing
  the relevance-check task and the three-verdict outcome.
- ``PatchResult`` gains ``deferred_verdicts: list[DeferredVerdict]``
  with shape ``{path, verdict, rationale, intents_emitted}``.
- ``parse_rebuild_proof`` (or equivalent) recognizes the new
  verdicts block.

End state: patch agent sees the deferred patches in its payload and
can act on them. If 37-1/37-2 already shipped without 37-3, the
patch agent simply ignores the unrecognized section — graceful
degradation.

Risk: medium. Prompt change needs observation across real runs to
confirm the agent picks up the rule. The relevance-check task is
genuinely hard; expect a learning loop on prompt + playbook.

##### 37-4 — playbook + tests + lifecycle hardening

Files: ``docs/agent-playbooks/convert-deferred-patch-relevance.md``,
``tests/test_runner_convert_defer.py``,
``tests/test_patch_deferred_section.py``.

- Playbook entry teaching the patch agent how to reason about the
  three verdicts with worked examples (e.g. "patch removes
  ``_sysconfigdata__freebsd99_*`` from plist; if those lines no
  longer exist upstream → drop; if they moved → regenerate at new
  line numbers; if shape changed → write smaller subset"). Triggers:
  ``flows: [patch]`` + ``classifications: [plist-error,
  patch-error]``.
- Handler tests: synthetic stdout with hunk-reject shape →
  ``_parse_compose_rejects`` extracts; ``_drop_patch_apply_from_overlay``
  edits cleanly; full convert path with N=1, 2, 3 drops; cap
  enforcement at N=4 drops bails to ``CONVERT_GAVE_UP``.
- Payload tests: deferred patches render in the expected section
  shape; absence of deferred patches → section omitted.
- Per-patch escalation handling: ``deferred_verdicts`` with
  ``verdict=escalated`` rows surface on bundle page; bundle stays
  ``agent_fixed`` only if every verdict is ``regenerated`` or
  ``dropped``.

Risk: low after 37-1/37-2/37-3 land.

#### Landing order

| Step | Risk | Notes |
|---|---|---|
| 37-1 | low | handler-only; bundle moves to CONVERT_OK on partial success |
| 37-2 | low | producer-only; richer deferred_patches context |
| 37-3 | medium | first consumer; patch agent attempts the relevance check |
| 37-4 | low | playbook + tests + per-verdict escalation polish |

#### LOC estimate

~440 LOC total. ~90 handler, ~20 schema, ~80 payload+prompt, ~50
playbook, ~200 tests.

#### Dependencies

- **Independent of Step 35** (build-time patch baseline). Step 35
  fixes the `make patch` baseline for the patch agent's own tools;
  Step 37 is about compose-time framework patches the handler
  drives, distinct layer.
- **Composes with Step 36** — uses ``PhaseResult`` typed contracts
  for deferred_patches / deferred_verdicts.
- **Composes with Step 25** (edit-intent DSL) — patch's relevance
  pass uses existing ``add_patch`` / ``replace_in_patch`` intents;
  no new intent type needed.

#### Out of scope (deferred)

- Multi-bundle deferred-patch state sharing. Each bundle's convert
  produces its own deferred list. No cross-bundle "this port's
  pkg-plist diff always drifts" memo — that's playbook/operator
  territory.
- Auto-regeneration of `diffs/*.diff` from the patch agent's
  verdicts back into the framework layer. Out of scope; the patch
  agent operates on dops substrate only, so regenerated diffs live
  in `overlay.dops` `patch.apply` blocks, not back in `diffs/`.

### Step 38 — target-scope plumbing for the intent layer — shipped

The engine fully supports per-target scoping. The semantic pass tracks
`current_targets` as it walks statements (`semantic.py:358-440`). The
compose layer filters ops by `{@any, target}` (`apply.py:296`).
Multi-target overlay fixtures + tests exist
(`fixtures/dportsv3/valid/multi_target.dops`,
`test_dportsv3_semantic.py:96`). And the runner already knows the env
target per build (`runner.py:5030` reads `job["target"]`).

But the intent layer ignores the dimension entirely. The Translator
constructor (`translator.py:65`) takes `(workspace, origin, mode)` —
no target. Every renderer's `_append_overlay` lands at EOF with no
scope awareness. No intent schema carries scope information. The
existing strip-prefilter (`_dops.py:441` `_strip_existing_mk_set`) is
scope-blind — a latent bug that will corrupt the substrate the moment
any multi-target overlay touches a re-emitted `mk set`.

The result: even though the runner knows it's building on `@2026Q2`,
the agent has no way to emit a fix scoped to `@2026Q2`. Every
patch-agent edit goes into the `@any` block by default. Build-line-
specific deprecations cannot be expressed.

#### Goal

After Step 38, the patch agent can emit intents with a small scope
vocabulary (`@any` for universal, `@current` for this-build-only).
The renderer resolves `@current` from the env target, places ops to
maintain an `@any-first` structural invariant on `overlay.dops`, and
the engine's "specific overrides general" semantics emerge naturally
from declaration order without the agent having to reason about
ordering.

The latent strip-prefilter bug is closed in the same pass by removing
the prefilter outright (Step 38 follows the "no implicit cleanup"
principle from the [intent gaps plan](intent-surface-gaps-plan.md)).

#### Sub-steps

**38a — Translator gets the env target.**

`Translator.__init__` grows a `target` parameter (default `None` for
backward-compat). Every caller — `worker.apply_intent`, the test
harness — passes the env target through. When the runner constructs a
Translator, it reads `job["target"]` (already available at
`runner.py:5030`) and threads it down. Renderers gain access via
`t.target`.

This is the minimum-viable plumbing. ~30 LOC across `translator.py` +
callers; no behavior change yet (renderers ignore `t.target` until
38b).

**38b — scope vocabulary + `_ensure_target_scope` placement helper.**

Two engine-valid scope values exposed to the agent:

- `@any` (default) — applies universally on every build.
- `@current` — resolves at apply time to `t.target` (e.g. `@2026Q2`).
  The agent never types a literal `@YYYYQX`.

New helper `_dops.py::_ensure_target_scope(overlay_text, scope) ->
(new_text, insertion_point)`:

- Parse the overlay into sections by `target @X` directives.
- For `@any`: locate (or create) the `@any` section at the head of
  the operation portion (right after the header directives).
- For a resolved `@Q`: locate (or create) the `@Q` section. Always
  placed AFTER all `@any` ops.
- Return the modified text and the line index where the caller should
  append its statement.

The helper is the single source of truth for placement. Every
renderer calls it.

**38c — structural invariant: `@any` first, `@Q` sections after.**

The renderer enforces this on every write. The invariant:

```
target @any
<all @any-scoped ops>

target @2026Q2      (optional)
<all @2026Q2-scoped ops>

target @2026Q3      (optional)
<all @2026Q3-scoped ops>

... etc.
```

Why this matters: the engine applies ops in declaration order,
filtered by scope. With `@any` first, `@Q` ops always run **after**
matching `@any` ops on a `@Q` build, so `@Q` overrides `@any`. This
is the "specific overrides general" intuition the agent expects.
Without the invariant, an `@any` op accidentally placed after a `@Q`
op would silently override it on the `@Q` build.

The invariant is the renderer's responsibility. If an existing
overlay violates it (e.g. legacy convert output, hand-edited file),
the renderer **refuses** the write with an actionable error pointing
at the malformed section. Auto-repair would be sneaky and surprising.

**38d — existing intent schemas grow an optional `scope` field.**

Seven intents: `replace_in_patch`, `drop_patch`, `add_patch`,
`add_file`, `change_makefile`, `bump_portrevision`,
`replace_in_dops_block`.

Each schema gains:

```json
"scope": {
  "enum": ["@any", "@current"],
  "default": "@any"
}
```

Renderers inspect `intent.scope`, resolve `@current` via `t.target`,
and call `_ensure_target_scope` before appending their statement.

Backward-compatible: omitting `scope` defaults to `@any`, matching
today's implicit behavior.

**38e — remove the strip-prefilter entirely.**

`_strip_existing_mk_set` in `_dops.py:441` is deleted. Its single
caller (`change_makefile op=set`) appends unconditionally. Re-emitting
`op=set FOO "x"` produces a second `mk set FOO "x"` line; the engine
processes ops in declaration order and the second `mk set` wins on
both `@any` and the current target. Substrate carries redundant lines
but compose output is correct.

This closes the scope-blind bug (there's no prefilter to be blind).
It also aligns the renderer with the "no implicit cleanup" principle
in [intent-surface-gaps-plan.md](intent-surface-gaps-plan.md) — every
intent does exactly one thing, predictably. If the agent wants to
clean up a redundant `mk set` from the overlay, it uses an explicit
delete intent (Family A in the gaps plan), not implicit prefiltering.

**38f — `get_effective_overlay` tool.**

New agent tool. Given `origin`, returns the ops effective for the
current build target — filtered + ordered as the engine would apply
them, with scope tags on each op. Filtered-out ops listed separately.

This lets the agent reason about effective state without having to
mentally apply scope filtering to the raw overlay file every time it
reads. With multi-target overlays in production, this becomes nearly
essential; without it, agents will systematically misread mixed-scope
files.

Schema (rough):

```
get_effective_overlay(origin) -> {
  target: "@2026Q2",
  effective_ops: [
    {kind: "mk.var.set", scope: "@any", line: 6, ...},
    {kind: "mk.var.token_add", scope: "@2026Q2", line: 12, ...}
  ],
  filtered_out: [
    {kind: "...", scope: "@2026Q3", line: 18, ...}
  ]
}
```

**38g — playbook + prompt updates.**

- Each of the 7 intent playbooks gains a "Scoping" section with the
  rule: "universal fix → omit `scope` or set `@any`; build-line-
  specific fix → `@current`."
- `prompts.py:510-525` adds a one-paragraph note: scope is a cross-
  cutting capability; default is `@any`; use `@current` for build-
  specific fixes.
- New `intent-scoping.md` cross-cutting playbook explaining the model
  in one place.

**38h — tests.**

- Translator-target plumbing: constructor accepts target; renderers
  can read it.
- `_ensure_target_scope` placement: empty overlay, overlay with only
  `@any`, overlay with `@any + @Q`, overlay with multiple `@Q`
  sections.
- Structural invariant enforcement: refuse writes that would produce
  `@Q` before `@any` for the same key; reject on overlays that
  already violate the invariant.
- Each renderer with each scope value (`@any`, `@current`): round-
  trip through the engine parser and verify scope is preserved.
- Strip-prefilter removal regression: re-emitting `op=set` produces
  two `mk set` lines (intentional; documents the new behavior).
- `get_effective_overlay`: filtering works; ordering preserved;
  filtered_out list correct.

#### LOC estimate

- 38a constructor + plumbing: ~50
- 38b scope helper: ~80
- 38c invariant enforcement: ~50
- 38d schema updates × 7 intents: ~40 (mostly JSON)
- 38e strip-prefilter removal: ~10 (mostly deletion)
- 38f effective-overlay tool: ~120
- 38g playbook + prompt: ~600 words content, ~30 LOC for prompt
- 38h tests: ~250

~600 LOC + content. Most of the surface is the helper + tests; the
schema + plumbing is small.

#### Order

38a → 38b → 38c → 38d → 38e → 38f → 38h → 38g. Plumbing and helper
first (no behavior change yet); structural invariant + schema updates
next so scope is wired through; strip-prefilter removal after that to
close the latent bug; effective-overlay tool to round out the agent's
reading surface; tests and docs last so they reflect the landed
behavior.

#### Why not earlier

Convert produces `@any`-only overlays today. The multi-target
capability has been engine-supported but unused at the intent layer
since the dops engine landed. Doing this work pre-emptively would
have been speculation about a use case that didn't exist. The recent
intent-surface gap analysis
([docs/intent-surface-gaps.md](intent-surface-gaps.md)) surfaced the
first concrete need: agents have to be able to express build-line-
specific fixes without overscoping to `@any`. Now the work has a
concrete trigger.

#### Dependencies

- **Hard**: nothing — the engine already supports target scoping
  end-to-end (parser, semantic pass, apply layer).
- **Soft**: the Family A delete intents in
  [intent-surface-gaps-plan.md](intent-surface-gaps-plan.md)
  (`drop_file`, `drop_target_block`, `drop_dops_directive`). Those
  should be designed with scope-awareness from day one if Step 38
  ships first — saves a v2 of each schema later.
- **Coupling**: Step 38 closes the latent `_strip_existing_mk_set`
  bug in passing. Without Step 38, that bug stays latent (no
  production overlay uses multi-target today, but the day one does —
  silent corruption).

#### Relationship to intent-surface-gaps

The [intent-surface-gaps plan](intent-surface-gaps-plan.md) (Family A
delete intents, Family B missing-directive intents) is **content** —
what the agent can express. Step 38 is **substrate plumbing** — how
those expressions are placed in the overlay. They're independent but
compatible: any new intent landed in either plan should consume
`scope` via the same field added in 38d.

Recommended sequencing: **Step 38 lands first**, then Family A delete
intents inherit scope from day one. The reverse (Family A first, then
retrofit scope) means rewriting every delete intent's renderer.

### Step 39 — intent surface gap closure: Family A delete intents — shipped

**Shipped 2026-06-05.** Three scope-aware delete intents plus their
playbook/prompt surface, each its own commit:

- 39a `drop_mk_directive` — `bfb6ae8bcde`
- 39b `drop_file` — `ff9bf706b53`
- 39c `drop_target_block` — `4830bc9342d`
- 39d playbooks + prompt wiring — `a1b67fd40cc`

The two playbook-coverage gate tests
(`test_every_intent_type_has_a_playbook`,
`test_intent_reference_attaches_every_intent_playbook`) are green; the
agent can now select all three deletes. The design record below is
retained as-shipped.

After Step 38 the agent has full target-scope capability end-to-end —
it can emit per-build or universal fixes, read the engine's
effective view via ``get_effective_overlay``, and has playbook +
prompt guidance on when to reach for each. But of the 23 distinct
dops directive shapes the engine understands, only ``patch apply``
has fully-symmetric create+delete intent coverage (``add_patch`` ↔
``drop_patch``). The other 22 are create-heavy: the agent can
construct most directive shapes but cannot remove them.

The consequence is a recurring shape across agent bundles: an agent
correctly identifies that a substrate line should be gone, has no
intent to express that, and reaches for a workaround that either
accumulates dead-weight in ``overlay.dops`` (emit a counter-op on
top of the line it can't remove) or corrupts the substrate (reach
for a heavyweight ``add_patch`` to source-patch a directive the
engine could have handled cleanly).

Step 39 closes the Family A delete intents that map directly to
observed agent workarounds. Three new intents, all scope-aware
from day one (consuming Step 38's plumbing).

#### Goal

After Step 39, the agent can explicitly delete:

- A specific ``mk set/unset/add/remove VAR`` line in
  ``overlay.dops`` (the dmidecode-shape thrash where re-emitting a
  ``mk add`` produces an add+remove pair on disk).
- A non-patch ``file copy`` / ``file materialize`` install
  directive (the "convert emitted a stale resource I want gone"
  shape).
- An ``mk target set`` / ``mk target append`` heredoc block (the
  "convert produced a ``dfly-patch:`` target that's no longer
  needed" shape — today the agent can only gut the body to
  ``@true`` via ``replace_in_dops_block``, leaving an empty target
  on disk).

Cleanup of redundant substrate becomes explicit and per-intent,
matching Step 38e's "no implicit cleanup" principle.

#### Sub-steps

**39a — ``drop_mk_directive`` intent.** (shipped — `bfb6ae8bcde`)

Single discriminated intent covering all four ``mk var`` ops:

```json
{
  "type": "drop_mk_directive",
  "kind": "set" | "unset" | "add" | "remove",
  "key": "USES",
  "value": "alias",
  "scope": "@any"
}
```

Schema: ``kind`` discriminator selects which dops line shape to
match. ``key`` is the variable name. ``value`` is required for
``kind=add`` and ``kind=remove`` (must match the line's token);
ignored for ``kind=set`` and ``kind=unset``. ``scope`` is optional
with the standard ``["@any", "@current"]`` enum from Step 38d.

Renderer locates the matching dops line in ``overlay.dops`` (scope
filter applied first when ``scope`` is specified). If exactly one
match, the line is removed. Refuses if zero matches (operator must
verify the line existed). Refuses if multiple matches at the same
scope (ambiguous; agent must add more discrimination via
``scope``).

Closes the dmidecode-shape thrash: agent that previously emitted
``mk add USES alias`` and then realized it was wrong now emits
``drop_mk_directive(kind=add, key=USES, value=alias)`` and the
prior line is gone from disk.

**39b — ``drop_file`` intent.** (shipped — `ff9bf706b53`)

```json
{
  "type": "drop_file",
  "target": "files/extra-config.in",
  "reason": "stale convert output — file removed in 1.50.0",
  "scope": "@any"
}
```

Removes a ``file copy SRC -> <target>`` or ``file materialize SRC -> <target>``
directive from ``overlay.dops``. Distinct from ``drop_patch``
which already covers patch-shaped destinations (``dragonfly/patch-*``);
``drop_file`` handles everything else (port-local resources, generated
files, etc.). Schemas mutually exclusive: ``drop_patch`` refuses
non-``dragonfly/patch-*`` paths; ``drop_file`` refuses
``dragonfly/patch-*`` paths.

Deletes the on-disk resource file in the resource case (mirrors
``drop_patch``'s file deletion for ``file_materialize`` shape).
``reason`` field required, same as ``drop_patch``.

**39c — ``drop_target_block`` intent.** (shipped — `4830bc9342d`)

```json
{
  "type": "drop_target_block",
  "block_name": "dfly-patch",
  "reason": "no longer needed after upstream 2.0 fixed file paths",
  "scope": "@any"
}
```

Removes an ``mk target set NAME <<TAG ... TAG`` or ``mk target append
NAME <<TAG ... TAG`` heredoc block from ``overlay.dops`` — the
whole block, open line through closing tag inclusive. **Accepts
``scope``** (standard ``["@any", "@current"]`` enum from Step 38d):
verified that the engine does NOT reject same-name target blocks
across scopes — ``build_plan`` on an overlay with two ``mk target
set dfly-patch`` blocks under different ``target`` directives
returns ``ok=True`` with two ops (``semantic.py:163-172`` validates
only name/heredoc_tag/recipe non-None; the ``E_SEM_DUPLICATE_*``
checks exist only for PORT/TYPE/REASON/MAINTAINER). So ``block_name``
alone does NOT uniquely identify the block when the same target
appears in multiple scopes; the scope filter is applied first, then
the name match within that scope. Refuses if the name+scope pair
still matches multiple blocks. ``reason`` field required.

Closes the heredoc-deletion gap. 39c lands the actual intent and
``intent-scoping.md`` moves ``drop_target_block`` into the
scope-accepting list alongside ``drop_mk_directive`` and
``drop_file``.

> **Latent issue (out of Step 39 scope — fixed in Step 40d):**
> existing ``replace_in_dops_block`` matches the *first* block by
> name and has no scope awareness. With same-name blocks across
> scopes now confirmed legal at the engine layer, ``replace_in_dops_block``
> can silently edit the wrong block. Carried to Step 40d, which reuses
> the same scope-filtered block-finder.

**39d — playbook + prompt updates.** (shipped — `a1b67fd40cc`)

- New playbooks ``intent-drop_mk_directive.md``,
  ``intent-drop_file.md``, ``intent-drop_target_block.md``. Each
  with When-to-use / Don't-use-when / shape / scoping / failure
  modes, matching the existing per-intent playbook structure.
- Update ``intent-change_makefile.md``: the Step 38e
  accumulation-after-re-emit paragraph now points at
  ``drop_mk_directive`` as the explicit cleanup path. Closes the
  38e gap that was tracked as "future intent" in the playbook
  text.
- Update ``intent-scoping.md``: ``drop_mk_directive``,
  ``drop_file``, and ``drop_target_block`` all accept scope (add
  to the scope-accepting list).
- Update ``prompts.py`` PATCH_INTENT_SYSTEM: intent type list
  "seven" → "ten" (deletes grouped as the symmetric inverses), and
  scope coverage "5 of 7" → "8 of 10" (the three deletes accept
  scope; only ``drop_patch`` and ``replace_in_dops_block`` don't).
- As-shipped extra: closed a test gap — the three deletes were
  absent from ``_SCOPE_BEARING_INTENTS`` in
  ``test_target_scope_plumbing.py``, so their ``scope`` field went
  unchecked by ``test_schema_for_surfaces_scope_field``; added.

#### Sub-step boundaries and ordering

Each of 39a/39b/39c is independently landable. Order matches
expected agent payoff:

```
39a (drop_mk_directive) → 39b (drop_file) → 39c (drop_target_block) → 39d (playbooks + prompt)
```

39a first because it directly closes the dmidecode-shape thrash
that's been observed multiple times. 39b second because it's
mechanically simplest (extends the existing ``drop_patch`` shape
with a path-prefix discriminator). 39c last among the engine
changes because it's the most complex matcher (multi-line heredoc
extent extraction). 39d bundled at the end so the playbook
updates reflect the full new surface in one commit.

Per the per-phase rewrite rule, the companion plan doc
``docs/intent-surface-gaps-plan.md`` rewrites as items land — same
lifecycle as Step 38.

#### LOC estimate

- 39a renderer + helper + schema + dataclass: ~80
- 39b renderer + schema + dataclass: ~50
- 39c renderer + schema + dataclass (multi-line heredoc matcher
  reuses 38c+38d-2 helpers): ~70
- 39d playbooks + prompt: 0 LOC + ~1500 words content

Total ~200 LOC + ~250 lines of tests (per Step 38's test floor
of 4 per new intent, 3 per modified).

#### Order

```
39a implement → review → chat → tests → commit
39b implement → review → chat → tests → commit
39c implement → review → chat → tests → commit
39d playbooks + prompt → review → chat → commit
```

Same cycle as Step 38. Each sub-step is its own commit.

#### Why not earlier

Pre-Step-38 the new delete intents would have been scope-blind,
matching the latent ``_strip_existing_mk_set`` bug we removed in
38e. Landing Family A delete intents before Step 38 would have
either (a) baked scope-blindness into every new intent's renderer,
requiring a v2 later, or (b) blocked on the same scope-foundation
work Step 38 ended up doing — without the benefit of having the
foundation already proven through Step 38's 86 dedicated tests.

Now the substrate is sound: scope plumbing through Translator
(38a), placement helper (38b), invariant gate (38c), schema
pattern (38d), prefilter removal (38e), agent-readable filtered
view (38f), playbook + prompt guidance (38g). Step 39 builds on
this foundation; every new delete intent inherits scope-awareness
via the same ``scope`` field added in 38d-4, the same renderer
dispatch in ``_append_overlay`` (38d-3), the same
``get_effective_overlay`` for verification (38f).

#### Dependencies

- **Hard**: Step 38. Every new intent consumes the ``scope`` field
  pattern, the ``_append_overlay`` scope dispatch, the placement
  helper, and the invariant gate. None of the Family A intents
  would work cleanly without that foundation.
- **Soft**: Family B missing-directive intents (``change_condition``
  for ``mk disable-if``/``mk replace-if``, ``add_target_block`` for
  heredoc creation, ``remove_file_at_compose`` for ``file
  remove``, etc.). Independent of Step 39 and can land in parallel
  or after.

#### Relationship to intent-surface-gaps docs

The reference matrix at ``docs/intent-surface-gaps.md`` records
the 23 directives × CRUD coverage. After Step 39, the rows for
``mk set``, ``mk unset``, ``mk add``, ``mk remove`` get a ✅ in
the Delete column (39a). The rows for ``file copy`` and ``file
materialize`` get a ✅ in the Delete column for non-patch paths
(39b). The row for ``mk target set/append`` heredocs gets a ✅
in the Delete column (39c).

After Step 39 the matrix counts shift:
- Directives with no delete intent: 20 of 23 → **14 of 23** (-6).
- Fully agent-manageable directives (Create + Delete): 1 of 23
  → **3 of 23** (counting drop_mk_directive symmetric with
  change_makefile, drop_file symmetric with add_file resource,
  drop_target_block symmetric pending the future add_target_block
  in Family B).

The companion plan ``docs/intent-surface-gaps-plan.md`` rewrites
to remove the Family A items as they land, then narrows to
Family B (missing-directive intents) for the next push.

#### What Step 39 does NOT do

- **Family B intents** (``change_condition``, ``add_block``,
  ``add_target_block``, ``drop_target_makefile``, ``rename_target``,
  ``remove_file_at_compose``, ``edit_line``). These close
  directive families with no agent surface at all; separate effort
  from Step 39's symmetric-delete focus.
- **Family C generalized ``edit_overlay``**. Still deferred per
  the gap-plan's locked decisions.
- **``add_patch`` upsert semantics**. Adjacent but not Family A
  proper; the current ``drop_patch`` + ``add_patch`` pattern
  works. Defer.
- **Convert-side improvements** that would prevent the agent from
  ever needing some of these deletes (e.g., a convert pass that
  doesn't emit stale ``file copy`` lines in the first place).
  Out of scope for the patch-agent intent surface work.

### Step 42 — delete the intent layer; the patch agent edits `overlay.dops` directly in dops DSL — shipped

> **FINAL REQUIREMENT (2026-06-05):** the per-directive intent layer MUST
> GO. The patch agent edits `overlay.dops` free-hand in dops syntax,
> exactly like the convert agent, guarded only by the generic
> `validate_dops` (`check_dsl`) + `assess_dops` gates. **No migration** —
> intents are never replayed (`changes.diff` is the canonical payload),
> so this is a straight deletion + tool swap, not a deprecation cycle.

This replaces the entire `apply_intent` → `Translator.apply` →
per-intent `_dops.py` renderer stack. The patch agent gets convert's
edit surface (`put_file` + `validate_dops` + `dops_reference`, reading
with `grep`/`get_file`) and writes dops lines/blocks itself. 42a/b/c
(the `add_dops` fold + the private `_delete_scoped`/scoped-replace
plumbing) are deleted along with everything else — the structured-intent
design they belonged to is gone.

#### Guardrails (generic, grammar-free — convert already runs all three)

1. **`validate_dops` / `check_dsl`** (`engine/api.py`) on every write —
   engine parse + semantics, `line:column` + `E_*` codes the LLM reacts
   to. Note (verified 2026-06-05): `check_dsl` does **no** path-safety —
   it accepts `file materialize x -> ../../etc/passwd` clean.
2. **`assess_dops`** substrate gate at the worker boundary
   (`worker.assess_dops` → `overlay_state.assess_overlay`) — shared with
   convert (`runner.py:2816`); flags the half-migrated (compat + dops)
   state. **Stays.**
3. **`_resolve_path`** at compose (`apply_common.py:47`) — the real
   path-escape backstop; rejects absolute/escaping operands with
   `E_APPLY_INVALID_PATH`. This is why direct free-hand dops editing is
   safe even though `check_dsl` isn't path-aware.

#### The coupling that *forces* intents today (must die)

`worker._reject_intent_path_put_file` (`worker.py:193`, called from
`put_file` at `worker.py:583`): when `DP_HARNESS_PATCH_USE_INTENT` is
on and the caller is the patch flow, it **refuses** any `put_file` to
`/work/DeltaPorts/ports/<origin>/` and tells the agent to use
`apply_intent` instead. That is the hard runtime block. Everything else
(the `patch_tool_names()` gate branch, `PATCH_INTENT_SYSTEM`, the
intent-log accumulator) is scaffolding around it.

#### Ordered cut (leaf consumers → core; the package stays importable and convert keeps working after every numbered step)

1. **Tool surface (`tools.py`).** Delete the `apply_intent` +
   `intent_reference` schemas (141–169), `_INTENT_TOOL_NAMES`,
   `_LEGACY_WRITE_TOOL_NAMES`, and `patch_use_intent_enabled`.
   `patch_tool_names()` collapses to one flat set with no gate branch:
   convert's 7 (`env_verify`, `list_dir`, `get_file`, `grep`,
   `put_file`, `dops_reference`, `validate_dops`) + `get_effective_overlay`
   + `emit_diff` + build-loop (`materialize_dports`, `extract`, `dupe`,
   `genpatch`, `install_patches`, `dsynth_build`, `dsynth_log`).
2. **Prompt (`prompts.py`).** Delete `PATCH_INTENT_SYSTEM` (~450–700).
   Rewrite the live `PATCH_SYSTEM` to direct `overlay.dops` editing:
   read with `grep`/`get_file`, write with `put_file`, validate with
   `validate_dops`, look up syntax via `dops_reference` — mirroring the
   convert prompt. Fix the stray intent reference at `prompts.py:24`.
3. **Worker (`worker.py`).** Delete `_reject_intent_path_put_file`
   (193) + its call (583) — *the force-intents block*. Delete
   `apply_intent` (1917), `intent_reference` (2221), the intent-log
   accumulator (`_INTENT_LOGS`, `_intent_log_key`, `_ensure_intent_log`,
   `drain_intent_log`, `peek_intent_log`, 1278–1348), and the
   half-migration/`_CREATION_INTENTS` state-machine comments that exist
   only for `apply_intent`. **Relocate** the `Makefile.DragonFly`-on-a-
   dops-port refusal (currently `_dops.py:301`) into a `put_file` guard:
   refuse a `Makefile.DragonFly*` write when `overlay.dops` already
   exists for that origin (`assess_overlay` already classifies the mixed
   state — this is the early-fail ergonomic). Keep `assess_dops` /
   `classify_dops` / `record_target` (shared, convert-used).
4. **Patch flow (`patch.py`).** Drop the gate branch (54–96): one
   prompt, one tool set, no `peek_intent_log` priming.
5. **Per-attempt preflight (`steps.py`).** Make the `intent_flow`
   branch (914–960) unconditional — the pre-job `assert_port_clean`
   check was good and applies to the direct-edit flow too; just drop the
   `if intent_flow` guard. Remove the `apply_intent` activity-row
   special-case (135–143) and the drain at 1038.
6. **Runner (`runner.py`).** Drop the `drain_intent_log` →
   `analysis/intent_log.json` bundle wiring (3521–3536). Keep
   `record_target` (3828/3964 — still needed for `get_effective_overlay`
   compose-target scoping). Rewrite the intent-referencing comments at
   2772/4236/4672.
7. **Tracker (`tracker/server.py`).** Delete `_summary_apply_intent` /
   `_summary_intent_reference` and their registry rows (893–928).
8. **Delete the package.** `dportsv3/agent/edit_intent/` (translator,
   `_dops.py`, `_compat.py`, grammar, validator, log, `schemas/*.json`)
   + its `__init__` exports.
9. **Verify, then test.** *Before* writing any new test, probe the real
   path: write an `overlay.dops` through the patch path
   (`put_file` → `validate_dops` → compose), confirm a path-escape
   operand still dies at `_resolve_path` (`E_APPLY_INVALID_PATH`), and
   confirm a `Makefile.DragonFly` + `overlay.dops` write trips the
   relocated guard. Delete the intent-targeted suites
   (`test_edit_intent.py`, the renderer/translator/scope-plumbing tests
   asserting intent behavior). Then add the thin coverage for the new
   surface.

Order rationale: 1→2 remove the LLM-visible surface; 3→7 remove the
runtime/plumbing that referenced it; 8 deletes the now-orphaned package;
9 verifies. The tree imports cleanly after each numbered step.

#### Open decision (deferred, does not block the cutover)

`str_replace` surgical-edit tool. The edit-tool robustness survey
(2026-06-05) said add it alongside whole-file `put_file` to dodge the
windowed-`get_file` truncation trap on large overlays. **Deferred to a
42 follow-up:** `put_file` + `validate_dops` is the proven-robust
primary and is exactly convert's surface. Ship the deletion first; add
`str_replace` only if smoke testing shows truncation pain.

#### Dependencies / supersedes

- **Hard**: Steps 38 (scope plumbing) and 39 (the scoped deletes).
- **Replaces**: Step 40 (additive-grammar growth — `add_dops` would have
  covered it, but the whole intent surface is gone now) and Step 41.
  Step 39's deletes are deleted with the rest of the intent layer.
- `docs/intent-surface-gaps.md` / `docs/intent-surface-gaps-plan.md`
  retire entirely — they are written around the grow-the-grammar path.

#### 42e — flow playbook (separable, can land before or after the cut)

Flow-level patch knowledge (the `extract→dupe→put_file→genpatch`
workflow, the "`dupe` is only step 1" anti-pattern, failed-patch
recovery) lives today inside `intent-*.md` playbooks, surfaced only on
an `intent_reference` call. With those playbooks deleted, that content
must move into a `flow-patch.md` playbook (frontmatter
`triggers: {flows: [patch]}`, no `intents:` axis, so it surfaces for the
whole patch flow). Playbook + selection-test only, no code change.


### Step 45 — agentic-loop failure-analysis remediation — shipped

> A structured analysis of a large agentic run (triage → convert →
> patch, dozens of bundles) surfaced a cluster of correctness,
> observability, and quality issues across the loop. This step is the
> umbrella record for the remediation: each fix landed in its own
> commit; the explicit *accept* / *reject* decisions are recorded here
> too so the reasoning isn't lost. Problem shapes are described below;
> the per-incident detail lives in the referenced commits.

#### Critical — silent false results / state corruption that compounds

- **C1 — success gate accepted compat writes as `agent_fixed`.** Patch
  success keyed on `rebuild_ok` alone; nothing checked the diff touched
  `overlay.dops`. A run that wrote legacy compat artifacts
  (`Makefile.DragonFly`, `dragonfly/*`) instead of a dops fix was
  stamped fixed. Now success requires the post-patch dops state to be
  `converted`; otherwise → ESCALATE_MANUAL.
  (`5c8e28e435e`, test-debt follow-up `98d81871075`)
- **C2 — no rollback on failed runs poisoned the shared checkout.**
  Budget-exhausted / dead runs left partial writes in the working tree,
  corrupting the next run's classification + compose. Non-success
  terminals now reset the whole tree (`git checkout HEAD -- . && git
  clean -fd`). (`5c8e28e435e`)
- **C3 — origin-scoped diff / classification was blind to
  master/slave.** Slave-port runs write into the master dir; an
  origin-scoped diff recorded nothing and classification ran on the
  slave, hiding both the work and the contamination. Diff + reset are
  now whole-tree; slave ports are refused to MANUAL via an `is_slave`
  gate (deep master-aware dops support deferred to Step 43).
  (`5c8e28e435e`, `97df2f45de5`, `992f60c97b6`)

#### High — zero/low yield, operators blind

- **H1 — `not_in_scope` ports authored compat from nothing.** A port
  with no DragonFly delta skipped convert and asked patch to *create*
  an overlay from a blank port (≈0 durable-fix rate). Convert now
  bootstraps a deterministic header overlay for empty-scope ports so
  they classify `converted` and the retriage → patch flow fills the
  body (Step 44 wiring). (`2bc4cbeb0f1`, slave defer-skip
  `992f60c97b6`)
- **H2 — `convert_gave_up` wrote no operator handoff.** The convert
  terminal lacked the `manual_handoff.md` write the patch path had, so
  convert failures left a resolution string and no explanation. Handoff
  now emitted at the convert-failure funnel. (`fd96ccb0f72`)
- **H4 — budget ceiling routinely hit.** Budgets summed cumulative
  `total_tokens`, re-billing the re-sent history every turn (no caching
  benefit). Budgets now count *billable* tokens (`max(0, prompt −
  cached) + completion`), reading the provider-normalized
  `cached_tokens`; degrades safely to 0. Covers patch and convert.
  (`9ecaa62b6ea`)
- **H3 — unfixable-by-port failures mis-tiered — ACCEPTED.** The only
  genuinely-unfixable signature in scope was missing kernel sources
  (kmod), which is an environment-provisioning concern; MANUAL is
  already the correct routing, so no brittle per-signature guard was
  added.
- **H6 (framework `USES=` divergence) — REJECTED as out of scope.** A
  removed/changed FreeBSD framework feature (e.g. a deleted `Uses/`
  module the DragonFly `Mk` no longer implements) is *framework
  divergence*, not a port bug and not an agentic-loop concern. It must
  be reconciled at the compose/framework boundary *before* a port is
  built — never discovered per-port at build time and patched by the
  LLM. Any agent-layer fix (playbook / tier-rule / prompt) is wrong by
  construction. The loop's only correct involvement is to recognize
  framework divergence and refuse it.

#### Medium — correctness / observability degraded but bounded

- **M1 — `rebuild_proof.json` metadata was LLM-fabricated.** The model
  has no clock and copied the prompt's literal example
  timestamp/command. Only `rebuild_ok` now comes from the agent; the
  harness code-stamps timestamp/origin and the real env-var-templated
  build command at write time. (`fd4dcab5601`, `0cefdc7e536`)
- **M2 — triage tier nondeterminism.** Prose in the `confidence` field
  (`"high — …"`) silently failed the policy enum floor and cascaded to
  MANUAL. Confidence is now a real enum end-to-end (prompt + parser
  coercion + `Literal` type). (`ab01575cac1`)
- **M3 — convert emitted ops that compile but don't apply.** The
  drop-and-retry recovery only handled rejecting framework
  `patch.apply` ops; inline ops that failed reapply
  (`E_APPLY_AMBIGUOUS_MATCH`, `E_APPLY_MISSING_SUBJECT`) hard-failed
  convert, so those ports never reached the patch agent. Generalized:
  `PlanOp` carries its source span, the defer loop drops the first
  failing op of *any* kind by span, records it as deferred intent, and
  (when the overlay empties) bypasses the dead-overlay guard so the
  retriage → patch flow authors the body. `DeferredPatch` gained
  `backing_file` so cleanup distinguishes file-backed from inline
  deferrals. (`38c15454e13`)
- **M4 — a failed triage stranded the bundle.** A triage terminating
  via `TRIAGE_FAIL` (bundle materialization / LLM call / policy load)
  retired the job but left the bundle at `resolution=NULL` with no
  handoff — invisible to retry/take-over and unexplained. `TRIAGE_FAIL`
  now propagates `resolution=triage_failed` and the triage tail writes
  a handoff pointing at infra. (`bd30135e972`)
- **M5 — MANUAL-escalation bucket audit — ACCEPTED.** Reviewed the full
  MANUAL bucket against primary data; it mostly clears. Prose-confidence
  cascades are OBE (fixed by M2). `missing-dep → MANUAL` is a defensible
  policy choice (auto-fixing a missing dependency is unsafe).
  `unknown` + low-confidence → MANUAL is correct by design.
  Checksum/distinfo fetch failures route to MANUAL and *should* stay
  there: auto-regenerating distinfo blindly accepts whatever distfile
  is on the mirror, defeating the integrity check it exists to enforce
  (same principle as H3/H6 — don't auto-fix the thing meant to need
  judgment). Handoff content spot-checked as correct. One low-priority
  ergonomics item noted (not a correctness bug): a driver family that
  fails identically across many slaved ports emits many near-duplicate
  handoffs that could collapse into one family handoff.

#### Out-of-scope items recorded during this work

- **Slave-port deep dops support** → Step 43 (current behavior: refuse
  to MANUAL).
- **Orchestrator-halt resolution gap**: the synthesized `TRIAGE_FAIL` /
  `PATCH_GAVE_UP` paths (orchestrator precheck/halt, no `bundle_id` in
  the transition detail) still don't propagate a resolution — a
  pre-existing gap shared across the triage and patch terminals. The
  handoff is still written. Left as a follow-up.

#### Deferred low-priority cleanups (not yet done)

- **L1 — patch agent occasionally writes compat-era `STATUS` files** in
  its output. Cleanliness only; the C1 gate already prevents these from
  counting as a successful dops fix.
- **L2 — `error_signature` is non-discriminating**: a single hash is
  shared across the large majority of bundles, so it's useless for
  dedup / routing. Wants a more selective signature derivation.
