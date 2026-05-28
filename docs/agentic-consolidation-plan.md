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
| [Architecture backlog](agentic-architecture-backlog.md) | Steps 12–27 — abstraction, refactors, lifecycle hardening, edit-intent DSL, playbook design records | mostly pending |

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
- **Next:** 26 items 1–4 (lifecycle hardening) → 25 (edit-intent
  DSL) → 24 (prompts/quickref) → 16 (UX) → refactor + abstraction
  backlog. 11d-4 (GitLab/Gitea) on-demand only.
- **New steps since last edit:** Step 28 (failed-bundle action
  matrix), Step 29 (context-aware re-triage), Step 30 (per-bundle
  branch isolation), all shipped; plus Q1/Q2/config-split folded
  under Step 30.


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
2. **26 (items 1–4 first)** — lifecycle hardening: lineage +
   attempt counter, `TRANSIENT_FAIL` edge, per-state timeout,
   `originating_bundle_id` for resolution propagation. Now the
   highest-value pending work: the operator loop is feature-complete
   but its FSM seams are where the recurring bugs live. Items 5–9
   are pure FSM cleanups and can interleave whenever.
3. **25** — edit-intent DSL. Architectural; design (25a) first.
   The structural answer to the empty-diff / edit-surface class of
   bug. Can develop in parallel with anything once 25a is approved.
4. **24** — prompts/quickref consolidation. Cheap, no behavior
   change. Folds in the stale-`delivery.diff`-comment cleanup noted
   under Step 30. Before 25d's prompt rewrite so it isn't against a
   messy baseline. What remains is the structural `dops_quickref.md`
   audit against the engine source-of-truth.
5. **16** — UX review (dashboard live-refresh + the rest).
6. **23 → 22** — execution layer then steps.py refactor. 23 first
   so 22's phase-helper extraction lands against the consolidated
   `chroot_exec`.
7. **21** — DB layer consolidation. Enables 17/18 to plug into a
   clean write surface. Natural landing site for Step 26 items 1+4.
8. **12 → 13 → 14 (system-prompt decomposition only) → 15** —
   abstraction work. 12 unblocks 13/14/15. Step 14's KEDB metadata
   work shipped via Step 27b; what remains is system-prompt
   decomposition (`PATCH_SYSTEM_SECTIONS`, per-section telemetry).
9. **17 → 18** — remote runners + security. Only load-bearing when
   a second builder appears.

Rationale for the head of the order: as of 2026-05-28 the operator's
primary interaction loop is closed — both success and failure bundles
have full action surfaces, delivery reaches GitHub, and per-bundle
branches stopped diffs from leaking across jobs. The remaining work is
no longer about closing the loop but about hardening it (26),
restructuring the edit surface (25), and paying down abstraction debt
(24/21/22/23/12–15). Step 26 leads because the loop is now wide enough
that seam-level FSM bugs are the dominant failure mode; everything
below it is cleanup that can wait for a real trigger (a second builder
for 17/18, a non-GitHub target for 11d-4).
