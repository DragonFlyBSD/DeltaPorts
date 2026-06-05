# Agentic loop — roadmap & priority order

> **Agentic plan set:** [Roadmap & priority order](agentic-consolidation-plan.md) · [Phase 4 — DB consolidation](agentic-phase4-db.md) · [Operator loop](agentic-operator-loop.md) · [Architecture backlog](agentic-architecture-backlog.md)

This is the living index for the agentic-loop plan. It carries the
current snapshot and the canonical pending-work order; the detailed
design records live in three theme files (split out 2026-05-28 when
this document outgrew a single file):

| Document | Contents | Lifecycle |
|---|---|---|
| [Phase 4 — DB consolidation](agentic-phase4-db.md) | state-server + tracker onto one DB (Steps 5/6/8) | frozen / done |
| [Operator loop](agentic-operator-loop.md) | Steps 1–11, 28, 29, 30 — manual escalation, verify/accept, delivery, branch isolation | mostly shipped |
| [Architecture backlog](agentic-architecture-backlog.md) | Steps 12–27, 31 — abstraction, refactors, lifecycle hardening, edit-intent DSL, single-service consolidation, playbook design records | mostly pending |

The *operative* playbooks the queue runner loads at fix time are not
in this plan set — they live as individual files in
`docs/agent-playbooks/`. The plan only carries their design record
(Step 19/27, in the architecture backlog).

### Snapshot (2026-05-28)

For the canonical pending-work order see **[Current priority order](#current-priority-order-as-of-2026-05-28)** at the
bottom. One-line summary:

- **Shipped:** Steps 1–10, 11a–d (11d-4 deferred), 19, 20, 27, 28,
  29, 30. The full operator loop — verify/accept/reject, GitHub
  delivery, failed-bundle actions, context-aware re-triage,
  per-bundle branch isolation — is in place.
- **Next:** 32 (job model — JobSpec/JobRecord + claim-time abandon
  guard, migration-free) alongside 26 items 1–4 (lifecycle
  hardening) → 25 (edit-intent DSL) → 24 (prompts/quickref) → 16
  (UX) → refactor + abstraction backlog. 11d-4 (GitLab/Gitea)
  on-demand only.
- **New steps since last edit:** Step 28 (failed-bundle action
  matrix), Step 29 (context-aware re-triage), Step 30 (per-bundle
  branch isolation), all shipped; plus Q1/Q2/config-split folded
  under Step 30. Step 32 (job model, migration-free) and Steps 33–34
  (operator SSO via a Redmine OIDC provider plugin + public read-only
  exposure) added pending; 33–34 are trigger-gated on the
  public-exposure decision.

### Update (2026-06-05) — edit-intent surface thread

The edit-intent work (Step 25) has continued in the architecture
backlog as a series of surface-hardening steps. Shipped since the
2026-05-28 snapshot:

- **Step 36** — typed phase results (`PhaseResult` contract).
- **Step 37** — compose-time patch drift: handler-side defer +
  patch-side relevance pass.
- **Step 38** — target-scope plumbing for the intent layer
  (`@any`/`@current`, `get_effective_overlay`).
- **Step 39** — Family A delete intents: `drop_mk_directive`,
  `drop_file`, `drop_target_block` + playbooks/prompt
  (`bfb6ae8bcde`, `ff9bf706b53`, `4830bc9342d`, `a1b67fd40cc`). The
  patch agent can now create *and* delete every Family A substrate
  shape.

**Next on this thread:** Step 40 (Family B missing-directive
intents — `change_condition`, `add_target_block`,
`remove_file_at_compose`, plus 40d which fixes the latent
scope-blindness in `replace_in_dops_block`). Step 41 (Family C
generalized `edit_overlay`) remains deferred behind a 41a
re-evaluation gate. See the
[architecture backlog](agentic-architecture-backlog.md) Steps 38–41
for the detailed records. This thread runs alongside the
26/32/21 hardening order below — it does not reorder it.


## Current priority order (as of 2026-05-28)

Replaces every "Suggested updated order" line scattered through the
post-implementation sections.

Shipped (no work needed):

- **Phase 4: 1–8** (per the Status table in [Phase 4 — DB consolidation](agentic-phase4-db.md)).
- **1–10, 11a–d, 19, 20, 27, 28, 29, 30** (per-step status in the
  [operator-loop](agentic-operator-loop.md) and
  [architecture-backlog](agentic-architecture-backlog.md) docs).
  The entire operator-facing loop — verify/accept/reject (11c),
  delivery to GitHub (11d), failed-bundle actions (28),
  context-aware re-triage (29), and per-bundle branch isolation
  (30) — is shipped. The 2026-05-24 "broken on both sides"
  rationale no longer holds; both the success and failure operator
  surfaces exist.
- **11b** four slices: `6800f9c5216`, `1454a55ca11`, `ef584cf6937`,
  `ee3afa36a70`.
- **11.0** (out-of-plan) — empty-`changes.diff` bug, fixed
  `2d9de6c4edc`. Subsumed by Step 30's canonical-diff rework.
- **Step 20 post-shipment fixes** (`5369db9fd4e`, `ccab8ebad88`,
  `300b7b1e96a`).
- **Step 27** — unified playbook library, 27a–g, 2026-05-26.
  Subsumed Step 19 and Step 14's KEDB portions.
- **Step 19** — shipped via Step 27f (`c7e1c865298`).

Pending, in recommended order:

1. **11d-4** — GitLab + Gitea delivery providers. The only
   unshipped slice of an otherwise-complete Step 11. Deferred, not
   blocking: `_http.py` + `_git.py` are provider-agnostic, so this
   is small when a non-GitHub target actually appears. Do it
   on-demand, not speculatively.
2. **32** — job model definition: one `JobSpec` serializer for the
   `.job` file (ends the per-field shotgun edit across the four
   enqueue functions + the shell hook), an explicit spec-vs-state
   ownership split, and a claim-time DB-state guard that closes the
   operator-abandon race (DB says dead, file still runs). Deliberately
   **migration-free**, so it has no dependency on Step 21 and items 1
   (JobSpec) + 4 (claim guard) can start immediately. Sits at the same
   FSM seam as Step 26, so run it alongside 26 items 1–4. The
   `spec_json`/DB-canonical end-state and a `JobControlRequest` intent
   table are explicitly deferred to a future migration-allowed step.
3. **26 (items 1–4 first)** — lifecycle hardening: lineage +
   attempt counter, `TRANSIENT_FAIL` edge, per-state timeout,
   `originating_bundle_id` for resolution propagation. The operator
   loop is feature-complete but its FSM seams are where the recurring
   bugs live. Items 1+4 add columns (hard dep on Step 21 to avoid a
   second migration); items 5–9 are pure FSM cleanups and can
   interleave whenever.
4. **25** — edit-intent DSL. Architectural; design (25a) first.
   The structural answer to the empty-diff / edit-surface class of
   bug. Can develop in parallel with anything once 25a is approved.
5. **24** — prompts/quickref consolidation. Cheap, no behavior
   change. Folds in the stale-`delivery.diff`-comment cleanup noted
   under Step 30. Before 25d's prompt rewrite so it isn't against a
   messy baseline. What remains is the structural `dops_quickref.md`
   audit against the engine source-of-truth.
6. **16** — UX review (dashboard live-refresh + the rest).
7. **23 → 22** — execution layer then steps.py refactor. 23 first
   so 22's phase-helper extraction lands against the consolidated
   `chroot_exec`.
8. **21 → 31 → 17 → 18** — consolidation + remote chain, in this
   order:
   - **21** DB layer consolidation — centralize scattered writes into
     `dportsv3.db.writes`, settle connection patterns. No behavior
     change. Natural landing site for Step 26 items 1+4.
   - **31** fold the artifact-store into the tracker — one service,
     one port, one auth surface. Makes the tracker the single writer
     process. Pure HTTP routing once 21 has a clean write surface.
   - **17** remote runners — runner stops opening `state.db` directly,
     uses the tracker's HTTP write endpoints + new read/claim
     endpoints. Only load-bearing when a second builder appears.
   - **18** security hardening — auth on the (now single) service.
9. **12 → 13 → 14 (system-prompt decomposition only) → 15** —
   abstraction work. 12 unblocks 13/14/15. Step 14's KEDB metadata
   work shipped via Step 27b; what remains is system-prompt
   decomposition (`PATCH_SYSTEM_SECTIONS`, per-section telemetry).
10. **33 → 34** — operator SSO + public read-only exposure.
    Trigger-gated on the decision to expose the tracker to the public
    internet. **When that's scheduled, 33→34 jump to the front of the
    active order** — they gate the public flip. 33 builds a **Redmine
    OIDC provider plugin** (Doorkeeper + doorkeeper-openid_connect; no
    maintained one exists) so the tracker authenticates devs via SSO
    against Redmine, with the tracker owning RBAC (roles mapped from
    the OIDC `groups` claim). It's independently valuable *now*: 33g
    closes the self-asserted-`operator` integrity gap in the lifecycle
    audit and the delivered-PR provenance, regardless of exposure.
    Accepted cost: a Redmine plugin maintained across Redmine/Rails
    upgrades. 33 is independent of the 17/18 *machine*-auth chain
    (human vs machine auth are orthogonal); 34 requires the
    runner/hook `/v1` write endpoints be network-isolated or
    machine-authed, which is where it intersects 31 + 17/18. Reconcile
    with Step 18's scope at scheduling time (18 was runner-token-
    rotation only).

Rationale for the head of the order: as of 2026-05-28 the operator's
primary interaction loop is closed — both success and failure bundles
have full action surfaces, delivery reaches GitHub, and per-bundle
branches stopped diffs from leaking across jobs. The remaining work is
no longer about closing the loop but about hardening it (26, 32),
restructuring the edit surface (25), and paying down abstraction debt
(24/21/22/23/12–15). Steps 26 and 32 lead because the loop is now wide
enough that seam/model-level bugs are the dominant failure mode: 26
hardens the FSM transitions, 32 fixes the job *model* underneath them
(single `JobSpec`, spec-vs-state ownership, the claim-time abandon
guard). 32 is deliberately migration-free so it carries no dependency
tax and can run alongside 26's column work (which waits on Step 21).
Everything below them is cleanup that can wait for a real trigger (a
second builder for 17/18, a non-GitHub target for 11d-4).
