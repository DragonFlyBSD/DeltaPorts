"""Operator-triggered delivery of an accepted bundle's fix to an
upstream code-hosting platform (Step 11d).

The agent loop itself doesn't touch this module — delivery is a
separate, explicit, operator-action phase that runs *after* a
bundle has been verified and accepted. The Step-11d plan section
of ``docs/agentic-consolidation-plan.md`` carries the full design.

Public surface:

- ``ReviewProvider`` — Protocol every provider implements
  (``LocalPatchProvider``, ``GitHubProvider``, etc.).
- ``ReviewRequestResult`` — dataclass returned from
  ``create_review_request``.
- ``DeliveryError`` and friends — structured exceptions providers
  raise on failure paths the caller cares about.

11d-1 ships just the Protocol + ``LocalPatchProvider``. Network
providers land in 11d-3 / 11d-4.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


__all__ = [
    "ReviewProvider",
    "ReviewRequestResult",
    "DeliveryError",
    "DeliveryAuthError",
    "DeliveryRateLimitError",
    "DeliveryConflictError",
    "DeliveryConfigError",
]


@dataclass(frozen=True)
class ReviewRequestResult:
    """Outcome of one ``create_review_request`` call.

    ``status`` mirrors ``bundle_review_requests.status``:
    - ``created`` — happy path, new PR opened.
    - ``updated`` — idempotency hit, existing open PR's body
      patched in place.
    """
    provider: str
    provider_pr_id: str
    url: str | None
    branch: str
    title: str
    status: str  # "created" | "updated"


class DeliveryError(Exception):
    """Base class for delivery failures the caller should surface
    to the operator."""


class DeliveryAuthError(DeliveryError):
    """The provider rejected the token (HTTP 401 / 403)."""


class DeliveryRateLimitError(DeliveryError):
    """The provider returned a rate-limit response (HTTP 429) and
    the wrapper's retry budget is exhausted. Caller should not
    auto-retry."""


class DeliveryConflictError(DeliveryError):
    """The provider returned a conflict (e.g. a different PR
    already exists for the same head) the wrapper can't
    auto-resolve."""


class DeliveryConfigError(DeliveryError):
    """Configuration problem: missing TOML, malformed field,
    token unreadable, outbox missing, etc."""


class ReviewProvider(Protocol):
    """Push a branch + open a review request on the upstream
    platform. Implementations live in sibling modules
    (``local_patch.py``, ``github.py``, ...).

    Idempotency: implementations look for an existing open
    review request matching ``(origin, target, signature)``
    via the DB's ``bundle_review_requests`` partial-unique
    index and return ``status='updated'`` instead of opening
    a duplicate.
    """

    name: str  # "github" / "gitlab" / "gitea" / "local-patch"

    def create_review_request(
        self,
        *,
        clone_dir: Path,
        branch_name: str,
        base_branch: str,
        title: str,
        body: str,
        labels: list[str],
        diff_text: str,
        diff_sha256: str,
        draft: bool = False,
    ) -> ReviewRequestResult:
        """Push the branch + open a review request.

        ``clone_dir`` is the operator's local DeltaPorts clone
        (resolved by the caller from ``$DPORTSV3_OPERATOR_CLONE``).
        Network providers use it to run git operations (fetch,
        checkout, apply, commit, push). ``LocalPatchProvider``
        ignores it.

        ``diff_text`` and ``diff_sha256`` are the bundle's
        ``analysis/changes.diff`` — passed in directly so the
        provider doesn't need to refetch from the artifact store.
        """
        ...
