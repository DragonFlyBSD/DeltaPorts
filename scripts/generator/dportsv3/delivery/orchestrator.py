"""Orchestrate one delivery attempt — Step 11d-2.

Called from the Accept endpoint after the bundle's resolution moves
to ``accepted``. Resolves the configured provider, fetches the
bundle's ``analysis/changes.diff``, builds the templated branch /
title / body, dispatches to the provider, and writes a
``bundle_review_requests`` row recording the outcome.

Delivery is best-effort: any failure here writes a ``create_failed``
row but doesn't propagate. The bundle is still ``accepted``; the
operator sees the failure on the bundle's Delivery card and can
retry (or accept manually outside the loop).

The provider abstraction (Step 11d-1) keeps this module unaware of
GitHub / GitLab specifics; concrete providers land in 11d-3 / 11d-4.
"""

from __future__ import annotations

import hashlib
import os
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import (
    DeliveryConfigError,
    DeliveryError,
    ReviewProvider,
    ReviewRequestResult,
)
from .config import DeliveryConfig, load_delivery_config


@dataclass
class DeliveryOutcome:
    """What the orchestrator returns to the accept endpoint.

    ``status`` aligns with ``bundle_review_requests.status``:
    - ``created`` / ``updated`` — provider succeeded.
    - ``create_failed`` — provider raised; row written with error.
    - ``skipped`` — no provider configured or diff missing; no row
      written. Caller treats as "delivery wasn't attempted, that's
      fine".
    """
    status: str
    provider: str | None = None
    provider_pr_id: str | None = None
    url: str | None = None
    branch: str | None = None
    error: str | None = None
    request_id: int | None = None
    skip_reason: str | None = None  # populated when status='skipped'


def resolve_config(
    *,
    target: str | None = None,
    env: dict[str, str] | None = None,
) -> DeliveryConfig | None:
    """Locate + load delivery.toml from environment.

    Search order:
      1. ``$DPORTSV3_DELIVERY_CONFIG`` — explicit path override.
      2. ``$DPORTSV3_CONFIG_DIR/delivery.toml`` — env-pointed dir.
      3. ``<repo-root>/config/delivery.toml`` — repo-anchored
         default, mirrors how ``agent/runner.py`` finds
         ``agentic-policy.json``. The asymmetry was a footgun:
         operators dropped delivery.toml next to agentic-policy.json
         expecting it to be picked up the same way, and got a
         silent ``no_config`` skip instead.

    Returns ``None`` if no file is found at any of the three (delivery
    silently disabled). Raises ``DeliveryConfigError`` only on a
    config file that EXISTS but is malformed — silent "no config
    found" path doesn't disrupt accepts on systems that haven't
    opted into delivery.
    """
    env = env if env is not None else dict(os.environ)
    explicit = env.get("DPORTSV3_DELIVERY_CONFIG", "").strip()
    if explicit:
        path = Path(explicit)
    else:
        config_dir = env.get("DPORTSV3_CONFIG_DIR", "").strip()
        if config_dir:
            path = Path(config_dir) / "delivery.toml"
        else:
            # Repo-anchored fallback. This file lives at
            # scripts/generator/dportsv3/delivery/orchestrator.py;
            # parents[4] is the repo root.
            path = (
                Path(__file__).resolve().parents[4]
                / "config" / "delivery.toml"
            )
    if not path.is_file():
        return None
    return load_delivery_config(path, target=target, env=env)


def build_provider(cfg: DeliveryConfig) -> ReviewProvider:
    """Construct the concrete provider for ``cfg.provider_type``.

    LocalPatchProvider (11d-1) and GitHubProvider (11d-3) are
    wired. GitLab / Gitea slots land in 11d-4 with the same shape.
    """
    if cfg.provider_type == "local-patch":
        from .local_patch import LocalPatchProvider  # noqa: PLC0415
        # The loader already requires provider.outbox for
        # local-patch — assert here just to surface a clear error
        # if someone hand-constructs a DeliveryConfig in tests.
        if not cfg.outbox:
            raise DeliveryConfigError(
                "LocalPatchProvider requires provider.outbox in "
                "delivery.toml"
            )
        return LocalPatchProvider(outbox=Path(cfg.outbox))
    if cfg.provider_type == "github":
        from .github import GitHubProvider  # noqa: PLC0415
        # The config loader already enforces both fields when
        # provider_type=="github", but be defensive against
        # someone constructing a DeliveryConfig by hand.
        if not cfg.token:
            raise DeliveryConfigError(
                "GitHubProvider requires a token; set "
                "$DPORTSV3_DELIVERY_TOKEN or place it at "
                "$DPORTSV3_CONFIG_DIR/delivery.token"
            )
        if not cfg.repo:
            raise DeliveryConfigError(
                "GitHubProvider requires provider.repo in "
                "delivery.toml (e.g. 'DragonFlyBSD/DeltaPorts')"
            )
        return GitHubProvider(
            token=cfg.token, repo=cfg.repo,
            committer_name=cfg.committer_name,
            committer_email=cfg.committer_email,
        )
    raise DeliveryConfigError(
        f"provider type {cfg.provider_type!r} not wired yet — "
        f"11d-4 ships gitlab + gitea"
    )


def format_branch(
    template: str, *,
    origin: str, target: str, bundle_id: str,
    error_signature: str | None = None,
) -> str:
    """Apply the operator's branch_template to this bundle's data.

    Recognized substitutions:
    - ``{origin}`` — raw origin (e.g. ``devel/foo``)
    - ``{origin_safe}`` — slashes → hyphens (e.g. ``devel-foo``)
    - ``{target}`` — raw target (e.g. ``@2026Q2``)
    - ``{target_safe}`` — without leading ``@`` (e.g. ``2026Q2``)
    - ``{bundle_id}`` — the full bundle ID
    - ``{bundle_short}`` — the trailing timestamp (last 16 chars
      of the bundle ID typically; falls back to whole id)
    - ``{signature_short}`` — first 8 hex chars of the bundle's
      ``error_signature``, or ``bundle_short`` when the signature
      is missing (legacy bundles + non-failure flows). Used by the
      default template so same-(origin, target, root-cause) retries
      converge on one branch / one PR.
    """
    origin_safe = origin.replace("/", "-").replace("_", "-")
    target_safe = target.lstrip("@") if target else ""
    bundle_short = bundle_id[-16:] if len(bundle_id) > 16 else bundle_id
    signature_short = (
        error_signature[:8] if error_signature else bundle_short
    )
    try:
        return template.format(
            origin=origin,
            origin_safe=origin_safe,
            target=target,
            target_safe=target_safe,
            bundle_id=bundle_id,
            bundle_short=bundle_short,
            signature_short=signature_short,
        )
    except KeyError as exc:
        raise DeliveryError(
            f"branch_template references unknown field {exc}; "
            f"known: origin / origin_safe / target / target_safe / "
            f"bundle_id / bundle_short / signature_short"
        ) from exc


def _diffstat(diff_text: str) -> str:
    """Approximate ``git diff --stat`` from a unified diff string.

    We don't have a git repo in hand at message-build time (the diff
    is the canonical artifact), so this counts added/removed lines
    and the changed-file list directly from the diff text. Hunk
    headers and the ``+++``/``---`` file markers are excluded from
    the +/- tally. Returns "" for an empty diff.
    """
    files: list[str] = []
    insertions = deletions = 0
    for line in diff_text.splitlines():
        if line.startswith("diff --git "):
            _, _, rest = line.partition(" b/")
            if rest:
                files.append(rest.strip())
        elif line.startswith("+++ ") or line.startswith("--- "):
            continue
        elif line.startswith("+"):
            insertions += 1
        elif line.startswith("-"):
            deletions += 1
    if not files and not insertions and not deletions:
        return ""
    n = len(files)
    header = (
        f"{n} file{'s' if n != 1 else ''} changed, "
        f"+{insertions}/-{deletions}"
    )
    file_lines = "\n".join(f"- `{f}`" for f in files)
    return header + ("\n\n" + file_lines if file_lines else "")


def _md_section(md: str | None, heading: str, *, max_chars: int = 3000) -> str:
    """Full body under a ``## <heading>`` section (until the next
    ``## `` or EOF), trimmed and length-capped. '' when absent.

    Used to lift the reviewer-relevant prose out of triage.md
    (Root Cause / Evidence) and patch.md (Patch Summary) into the PR
    body. Markdown inside the section is preserved as-is (no
    re-fencing) so quoted log lines / bullets render unchanged.
    """
    if not md:
        return ""
    pat = re.compile(
        rf"^##\s+{re.escape(heading)}\s*\n(.*?)(?=^##\s+|\Z)",
        re.MULTILINE | re.DOTALL | re.IGNORECASE,
    )
    m = pat.search(md)
    if not m:
        return ""
    body = m.group(1).strip()
    if len(body) > max_chars:
        body = body[:max_chars].rstrip() + "\n…(truncated)"
    return body


def _md_inline(md: str | None, heading: str) -> str:
    """First non-empty line under a ``## <heading>`` section."""
    if not md:
        return ""
    pat = re.compile(
        rf"^##\s+{re.escape(heading)}\s*\n(.*?)(?=^##\s+|\Z)",
        re.MULTILINE | re.DOTALL | re.IGNORECASE,
    )
    m = pat.search(md)
    if not m:
        return ""
    for line in m.group(1).splitlines():
        s = line.strip()
        if s:
            return s
    return ""


def format_commit_message(
    *,
    origin: str,
    target: str,
    operator: str,
    model: str | None,
    attempts: int | None,
    tokens: int | None,
    verified_at: str | None,
    diff_text: str | None = None,
    patch_summary: str | None = None,
    root_cause: str | None = None,
    classification: str | None = None,
    confidence: str | None = None,
    evidence: str | None = None,
) -> tuple[str, str]:
    """Build (title, body) for the delivered PR.

    Title is a concise, upstream-facing one-liner — no internal
    target jargon (the base branch already encodes the quarterly).

    Body is sectioned markdown a reviewer can read top-to-bottom,
    telling the full story:
      disclosure → Problem (what/why it failed, from triage) →
      Fix (the agent's rationale, from patch.md) → What changed
      (diffstat) → Verification → Provenance.

    The triage/patch prose is optional — sections degrade gracefully
    when the source artifacts are missing (older bundles, partial
    runs).
    """
    title = f"{origin}: fix build failure on DragonFly"

    sections: list[str] = []

    # Bot disclosure — these are bot-authored PRs; set reviewer
    # expectations honestly and credit the human gate.
    disclosure = (
        "> Generated by the DragonFly agentic build-fix loop and "
        "verified in a clean dev-env"
    )
    disclosure += (
        f". Reviewed and accepted by operator `{operator}` before "
        f"submission." if operator else "."
    )
    sections.append(disclosure)

    # Problem — what was failing and why (from triage).
    prob: list[str] = []
    cls = (classification or "").strip()
    conf = (confidence or "").strip()
    bits = []
    if cls:
        bits.append(f"classified `{cls}`")
    if conf:
        bits.append(f"confidence `{conf}`")
    head = (
        f"The build failed on `{target}`" if target
        else "The build failed"
    )
    prob.append(head + (" — " + ", ".join(bits) if bits else "") + ".")
    rc = (root_cause or "").strip()
    if rc:
        prob.append("")
        prob.append(rc)
    ev = (evidence or "").strip()
    if ev:
        prob.append("")
        prob.append("**Evidence:**")
        prob.append("")
        prob.append(ev)
    sections.append("## Problem\n\n" + "\n".join(prob))

    # Fix — the agent's rationale (from patch.md).
    summary = (patch_summary or "").strip()
    if not summary:
        summary = f"Automated fix for the build failure in `{origin}`."
    sections.append("## Fix\n\n" + summary)

    if diff_text:
        stat = _diffstat(diff_text)
        if stat:
            sections.append(f"## What changed\n\n{stat}")

    verify_lines = [
        "Built successfully in a DragonFly dev-env (dsynth)"
        + (f" for target `{target}`." if target else ".")
    ]
    if verified_at:
        verify_lines.append(f"Verified {verified_at}.")
    sections.append("## Verification\n\n" + "\n".join(verify_lines))

    prov = [f"- Operator: {operator}"]
    agent_parts = []
    if model:
        agent_parts.append(f"model={model}")
    if attempts is not None:
        agent_parts.append(f"attempts={attempts}")
    if tokens is not None:
        agent_parts.append(f"tokens={tokens}")
    if agent_parts:
        prov.append("- Agent: " + " ".join(agent_parts))
    sections.append("## Provenance\n\n" + "\n".join(prov))

    body = "\n\n".join(sections) + "\n"
    return title, body


def deliver(
    *,
    bundle: dict,
    diff_text: str,
    cfg: DeliveryConfig,
    operator: str,
    triage_md: str | None = None,
    patch_md: str | None = None,
    model: str | None,
    attempts: int | None,
    tokens: int | None,
    write_conn: sqlite3.Connection,
) -> DeliveryOutcome:
    """Orchestrate one delivery against the configured provider.

    Writes the ``bundle_review_requests`` row regardless of
    provider outcome (``created`` / ``updated`` on success;
    ``create_failed`` on exception). Returns the outcome so the
    accept endpoint can include it in the response and emit an
    appropriate event.

    Callers handle the "no config" case BEFORE calling deliver —
    this function assumes a valid cfg.
    """
    from dportsv3.tracker.agentic_queries import (  # noqa: PLC0415
        find_open_review_request,
        insert_review_request,
        update_review_request_status,
    )

    bundle_id = bundle.get("bundle_id") or ""
    origin = (bundle.get("origin") or "").strip()
    target = (bundle.get("target") or "").strip()
    error_signature = (bundle.get("error_signature") or "").strip() or None

    branch = format_branch(
        cfg.branch_template,
        origin=origin, target=target, bundle_id=bundle_id,
        error_signature=error_signature,
    )
    title, body = format_commit_message(
        origin=origin, target=target, operator=operator,
        model=model, attempts=attempts, tokens=tokens,
        verified_at=bundle.get("verification_at"),
        diff_text=diff_text,
        patch_summary=_md_section(patch_md, "Patch Summary", max_chars=1500),
        root_cause=_md_section(triage_md, "Root Cause", max_chars=2000),
        classification=_md_inline(triage_md, "Classification"),
        confidence=_md_inline(triage_md, "Confidence"),
        evidence=_md_section(triage_md, "Evidence", max_chars=2500),
    )
    diff_sha256 = hashlib.sha256(diff_text.encode("utf-8")).hexdigest()

    provider = build_provider(cfg)

    # Resolve the operator clone before invoking the provider so a
    # bad provider.clone_dir surfaces with a clear, config-specific
    # error instead of "doesn't exist" from deep inside _git. The
    # loader guarantees clone_dir is set for non-local-patch, but
    # the path can still point at a missing directory.
    if cfg.provider_type != "local-patch":
        clone_path = Path(cfg.clone_dir or "")
        if not clone_path.is_dir():
            request_id = insert_review_request(
                write_conn,
                bundle_id=bundle_id,
                provider=cfg.provider_type,
                status="create_failed",
                error=(
                    f"DeliveryConfigError: provider.clone_dir "
                    f"points at {str(clone_path)!r} which doesn't "
                    f"exist"
                ),
                operator=operator,
                error_signature=error_signature,
            )
            return DeliveryOutcome(
                status="create_failed",
                provider=cfg.provider_type,
                error=(
                    f"provider.clone_dir points at "
                    f"{str(clone_path)!r} which doesn't exist"
                ),
                request_id=request_id,
            )
    else:
        # local-patch never reads clone_dir; pass cwd to satisfy
        # the Protocol signature.
        clone_path = Path(".")

    # Look up an open delivery row BEFORE calling the provider so
    # we can pass its recorded diff_sha256 in — same-content
    # re-Accepts then short-circuit the provider's git pipeline
    # (review Finding 4). Keyed on (provider, branch): the branch
    # name encodes (origin, target, error_signature) under the
    # default template, so this catches genuine retries of the same
    # root cause on the same port without aliasing across ports
    # whose first error lines happen to match. Legacy rows with
    # NULL branch can't collide (partial-unique index doesn't fire
    # on NULL) so we skip the lookup there.
    existing_open = None
    if branch:
        existing_open = find_open_review_request(
            write_conn,
            provider=cfg.provider_type,
            branch=branch,
        )
    existing_diff_sha256 = (
        existing_open["diff_sha256"] if existing_open is not None
        else None
    )

    try:
        result: ReviewRequestResult = provider.create_review_request(
            clone_dir=clone_path,
            branch_name=branch,
            base_branch=cfg.base_branch,
            title=title,
            body=body,
            labels=list(cfg.labels),
            diff_text=diff_text,
            diff_sha256=diff_sha256,
            draft=cfg.draft,
            existing_diff_sha256=existing_diff_sha256,
        )
    except Exception as exc:
        request_id = insert_review_request(
            write_conn,
            bundle_id=bundle_id,
            provider=cfg.provider_type,
            status="create_failed",
            branch=branch,
            title=title,
            error=f"{type(exc).__name__}: {exc}"[:500],
            operator=operator,
            error_signature=error_signature,
        )
        return DeliveryOutcome(
            status="create_failed",
            provider=cfg.provider_type,
            branch=branch,
            error=f"{type(exc).__name__}: {exc}",
            request_id=request_id,
        )

    # On status='updated' the partial-unique index would block a
    # fresh INSERT (an open row already exists for this branch);
    # touch the existing row's last_synced_at instead. status=
    # 'created' is the fresh-write path. The existing_open lookup
    # already ran above the provider call.
    if existing_open is not None and result.status == "updated":
        update_review_request_status(
            write_conn,
            request_id=int(existing_open["id"]),
            status="updated",
            provider_pr_id=result.provider_pr_id,
            url=result.url,
            branch=result.branch,
            diff_sha256=diff_sha256,
        )
        request_id = int(existing_open["id"])
    else:
        try:
            request_id = insert_review_request(
                write_conn,
                bundle_id=bundle_id,
                provider=cfg.provider_type,
                status=result.status,
                provider_pr_id=result.provider_pr_id,
                url=result.url,
                branch=result.branch,
                title=result.title,
                operator=operator,
                error_signature=error_signature,
                diff_sha256=diff_sha256,
            )
        except sqlite3.IntegrityError:
            # Race or stale view: the open-row lookup above missed a
            # row that exists now (concurrent Accept, or the provider
            # returned 'created' against a branch a parallel deliver
            # just inserted). Re-query and reconcile by updating
            # rather than crashing — the upstream PR already exists
            # (provider succeeded), so swallowing the INSERT keeps the
            # row coherent with reality instead of orphaning the PR.
            reconciled = find_open_review_request(
                write_conn,
                provider=cfg.provider_type,
                branch=branch,
            )
            if reconciled is None:
                raise
            update_review_request_status(
                write_conn,
                request_id=int(reconciled["id"]),
                status=result.status,
                provider_pr_id=result.provider_pr_id,
                url=result.url,
                branch=result.branch,
                diff_sha256=diff_sha256,
            )
            request_id = int(reconciled["id"])
    return DeliveryOutcome(
        status=result.status,
        provider=cfg.provider_type,
        provider_pr_id=result.provider_pr_id,
        url=result.url,
        branch=result.branch,
        request_id=request_id,
    )
