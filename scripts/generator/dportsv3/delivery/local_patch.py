"""LocalPatchProvider — Step 11d-1's no-network fallback provider.

Writes the bundle's ``analysis/changes.diff`` to a designated
outbox directory along with a sidecar metadata JSON carrying the
templated commit-message fields. Default provider when
``delivery.toml`` is missing or sets ``provider.type =
"local-patch"``.

Operator workflow: tracker Accepts a verified bundle → this
provider writes ``<outbox>/<branch_name>.patch`` plus
``<branch_name>.metadata.json`` → operator runs ``git apply``
on their own clone manually, edits the commit if needed, opens
a PR with whatever tooling they prefer.

Idempotency:
- If ``<branch_name>.patch`` already exists AND its content's
  SHA-256 matches what we'd write, this is a no-op re-Accept:
  returns ``status='updated'`` with the existing record.
- If the file exists with a DIFFERENT SHA, we refuse with a
  conflict — overwriting the operator's intermediate state
  silently is the wrong default. Operator removes the stale
  file (or accepts under a different branch_name) to retry.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Final

from . import (
    DeliveryConfigError,
    DeliveryConflictError,
    DeliveryError,
    ReviewRequestResult,
)


__all__ = ["LocalPatchProvider"]


@dataclass
class LocalPatchProvider:
    """No-network provider. ``outbox`` is the directory we write
    into (caller resolves from ``$DPORTSV3_DELIVERY_OUTBOX`` via
    ``DeliveryConfig.outbox``).

    The outbox itself must be created by the operator — a missing
    outbox is a config error, not something we silently work around.
    Subdirectories *under* the outbox (driven by branch names with
    ``/`` separators, e.g. ``agentic/devel-foo-<ts>``) are created
    on demand via ``mkdir(parents=True)``.
    """
    outbox: Path
    name: Final[str] = "local-patch"

    def create_review_request(
        self,
        *,
        clone_dir: Path,           # unused for local-patch
        branch_name: str,
        base_branch: str,
        title: str,
        body: str,
        labels: list[str],
        diff_text: str,
        diff_sha256: str,
        draft: bool = False,       # unused for local-patch
        existing_diff_sha256: str | None = None,  # unused — own idempotency via on-disk SHA
    ) -> ReviewRequestResult:
        if not self.outbox:
            raise DeliveryConfigError(
                "LocalPatchProvider: outbox is unset. Set "
                "$DPORTSV3_DELIVERY_OUTBOX to a writable directory."
            )
        outbox = Path(self.outbox)
        if not outbox.is_dir():
            raise DeliveryConfigError(
                f"LocalPatchProvider: outbox {outbox!s} doesn't "
                f"exist. Create it first (we don't auto-mkdir to "
                f"avoid surprising the operator)."
            )

        # Defensive: validate branch_name doesn't escape outbox.
        # A '/' in branch_name would create subdirectories which
        # is fine, but '..' or absolute paths would break out.
        if branch_name.startswith("/") or ".." in branch_name.split("/"):
            raise DeliveryError(
                f"LocalPatchProvider: refusing unsafe branch_name "
                f"{branch_name!r} (contains '..' or absolute path)"
            )
        # Slashes in branch_name produce nested dirs in the outbox;
        # mkdir parents so e.g. "agentic/devel-skalibs-<ts>" lands at
        # "<outbox>/agentic/devel-skalibs-<ts>.patch".
        patch_path = outbox / f"{branch_name}.patch"
        meta_path = outbox / f"{branch_name}.metadata.json"
        patch_path.parent.mkdir(parents=True, exist_ok=True)

        # Idempotency check: if the patch file already exists,
        # compare SHA to decide same-content (update) vs different-
        # content (conflict).
        existing_status = "created"
        if patch_path.is_file():
            try:
                existing = patch_path.read_bytes()
            except OSError as exc:
                raise DeliveryError(
                    f"LocalPatchProvider: outbox patch {patch_path!s} "
                    f"is unreadable: {exc}"
                ) from exc
            existing_sha = hashlib.sha256(existing).hexdigest()
            if existing_sha == diff_sha256:
                existing_status = "updated"
            else:
                raise DeliveryConflictError(
                    f"LocalPatchProvider: {patch_path!s} already "
                    f"exists with different content (sha256 "
                    f"{existing_sha[:12]}… vs incoming {diff_sha256[:12]}…). "
                    f"Operator must remove or rename the existing file."
                )

        # Write atomically: temp file in same dir, then rename.
        # On the update-same-content path we still rewrite — cheap
        # and keeps the metadata's `updated_at` fresh.
        tmp_patch = patch_path.with_suffix(patch_path.suffix + ".tmp")
        tmp_meta = meta_path.with_suffix(meta_path.suffix + ".tmp")
        try:
            tmp_patch.write_text(diff_text)
            tmp_patch.replace(patch_path)
        except OSError as exc:
            raise DeliveryError(
                f"LocalPatchProvider: write {patch_path!s} failed: {exc}"
            ) from exc

        metadata = {
            "branch": branch_name,
            "base_branch": base_branch,
            "title": title,
            "body": body,
            "labels": list(labels),
            "diff_sha256": diff_sha256,
            "draft": draft,
            "written_at": datetime.now(timezone.utc).isoformat(),
            "status": existing_status,
        }
        try:
            tmp_meta.write_text(json.dumps(metadata, indent=2) + "\n")
            tmp_meta.replace(meta_path)
        except OSError as exc:
            raise DeliveryError(
                f"LocalPatchProvider: write {meta_path!s} failed: {exc}"
            ) from exc

        return ReviewRequestResult(
            provider=self.name,
            # The "PR ID" for local-patch is the patch filename
            # relative to outbox — gives operators something to
            # paste back into the tracker UI if they want.
            provider_pr_id=f"{branch_name}.patch",
            url=str(patch_path),
            branch=branch_name,
            title=title,
            status=existing_status,
        )
