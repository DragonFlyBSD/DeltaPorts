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
      2. ``$DPORTSV3_CONFIG_DIR/delivery.toml`` — default location.

    Returns ``None`` if no file is configured / present (delivery
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
        if not config_dir:
            return None
        path = Path(config_dir) / "delivery.toml"
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
        if not cfg.outbox:
            raise DeliveryConfigError(
                "LocalPatchProvider requires $DPORTSV3_DELIVERY_OUTBOX"
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
        return GitHubProvider(token=cfg.token, repo=cfg.repo)
    raise DeliveryConfigError(
        f"provider type {cfg.provider_type!r} not wired yet — "
        f"11d-4 ships gitlab + gitea"
    )


def format_branch(
    template: str, *,
    origin: str, target: str, bundle_id: str,
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
    """
    origin_safe = origin.replace("/", "-").replace("_", "-")
    target_safe = target.lstrip("@") if target else ""
    bundle_short = bundle_id[-16:] if len(bundle_id) > 16 else bundle_id
    try:
        return template.format(
            origin=origin,
            origin_safe=origin_safe,
            target=target,
            target_safe=target_safe,
            bundle_id=bundle_id,
            bundle_short=bundle_short,
        )
    except KeyError as exc:
        raise DeliveryError(
            f"branch_template references unknown field {exc}; "
            f"known: origin / origin_safe / target / target_safe / "
            f"bundle_id / bundle_short"
        ) from exc


def format_commit_message(
    *,
    origin: str,
    target: str,
    bundle_id: str,
    bundle_url: str | None,
    one_line_summary: str | None,
    operator: str,
    model: str | None,
    attempts: int | None,
    tokens: int | None,
    verified_at: str | None,
) -> tuple[str, str]:
    """Build (title, body) per the plan's commit-message template
    (§11d "Mechanism" step 5).
    """
    title = f"{origin}: fix dsynth build under {target}"

    lines: list[str] = []
    if one_line_summary:
        lines.append(one_line_summary.strip())
        lines.append("")
    if verified_at:
        lines.append(
            f"Verified by `dportsv3 dev-env verify-fix {bundle_id}` "
            f"({verified_at})."
        )
    lines.append(f"Operator: {operator}")
    agent_parts = []
    if model:
        agent_parts.append(f"model={model}")
    if attempts is not None:
        agent_parts.append(f"attempts={attempts}")
    if tokens is not None:
        agent_parts.append(f"tokens={tokens}")
    if agent_parts:
        lines.append("Agent: " + " ".join(agent_parts))
    if bundle_url:
        lines.append(f"Bundle: {bundle_url}")
    body = "\n".join(lines) + "\n"
    return title, body


def deliver(
    *,
    bundle: dict,
    diff_text: str,
    cfg: DeliveryConfig,
    operator: str,
    bundle_url: str | None,
    one_line_summary: str | None,
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
    )
    title, body = format_commit_message(
        origin=origin, target=target,
        bundle_id=bundle_id, bundle_url=bundle_url,
        one_line_summary=one_line_summary, operator=operator,
        model=model, attempts=attempts, tokens=tokens,
        verified_at=bundle.get("verification_at"),
    )
    diff_sha256 = hashlib.sha256(diff_text.encode("utf-8")).hexdigest()

    provider = build_provider(cfg)

    try:
        result: ReviewRequestResult = provider.create_review_request(
            clone_dir=Path(
                os.environ.get("DPORTSV3_OPERATOR_CLONE", "/nonexistent")
            ),
            branch_name=branch,
            base_branch=cfg.base_branch,
            title=title,
            body=body,
            labels=list(cfg.labels),
            diff_text=diff_text,
            diff_sha256=diff_sha256,
            draft=cfg.draft,
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
    # fresh INSERT (an open row already exists for this signature);
    # touch the existing row's last_synced_at instead. status=
    # 'created' is the fresh-write path. error_signature can be
    # None for legacy bundles — those skip the find_open lookup
    # because the index doesn't fire on NULL signatures.
    existing_open = None
    if error_signature:
        existing_open = find_open_review_request(
            write_conn,
            provider=cfg.provider_type,
            error_signature=error_signature,
        )

    if existing_open is not None and result.status == "updated":
        update_review_request_status(
            write_conn,
            request_id=int(existing_open["id"]),
            status="updated",
            provider_pr_id=result.provider_pr_id,
            url=result.url,
            branch=result.branch,
        )
        request_id = int(existing_open["id"])
    else:
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
        )
    return DeliveryOutcome(
        status=result.status,
        provider=cfg.provider_type,
        provider_pr_id=result.provider_pr_id,
        url=result.url,
        branch=result.branch,
        request_id=request_id,
    )
