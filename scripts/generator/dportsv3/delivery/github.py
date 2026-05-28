"""GitHubProvider (Step 11d-3).

Drives a verified bundle's fix all the way to a GitHub PR. Uses
``_http.py`` for the REST calls and ``_git.py`` for the local-clone
git operations. The ReviewProvider contract is satisfied via
``create_review_request``.

End-to-end flow inside ``create_review_request``:

  1. ``_git.prepare_clean_branch`` — fetch + checkout a fresh
     feature branch from origin/<base>.
  2. ``_git.apply_diff`` — git apply --3way the bundle's diff.
  3. ``_git.commit_diff`` — git add -u + git commit -s.
  4. ``_git.push_branch`` — push --force-with-lease to origin.
  5. Check for an existing open PR on this branch:
     ``GET /repos/{owner}/{repo}/pulls?head=<owner>:<branch>&state=open``.
     If found → PATCH the body, return status='updated'.
  6. Otherwise → ``POST /repos/{owner}/{repo}/pulls`` with title /
     head / base / body / draft. Return status='created'.
  7. Best-effort: add labels via
     ``POST /repos/{owner}/{repo}/issues/{number}/labels``. Failure
     here doesn't fail the delivery — the PR is the load-bearing
     artifact.

Token comes from ``DeliveryConfig.token`` (already resolved by the
config loader from env var or 0400 file).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from . import DeliveryConfigError, DeliveryError, ReviewRequestResult
from ._http import DeliveryHttpClient


__all__ = ["GitHubProvider"]


_API_BASE: Final[str] = "https://api.github.com"


@dataclass
class GitHubProvider:
    """GitHub PR-creation provider. Initialize with the operator's
    token + the ``owner/repo`` slug; the ``base_url`` defaults to
    api.github.com but the test seam is here for future GHES."""
    token: str
    repo: str  # "owner/name"
    name: Final[str] = "github"
    base_url: str = _API_BASE
    committer_name: str = "Fred [bot]"
    committer_email: str = "github@dragonflybsd.org"
    # Test seams — overridable so we don't actually push to GitHub
    # or run real git in unit tests.
    _http_client_factory: Any = field(default=None)
    _git_module: Any = field(default=None)

    def __post_init__(self) -> None:
        if "/" not in self.repo:
            raise DeliveryConfigError(
                f"GitHubProvider: repo must be in 'owner/name' "
                f"form, got {self.repo!r}"
            )
        self._owner, self._repo_name = self.repo.split("/", 1)

    def _http(self) -> DeliveryHttpClient:
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self._http_client_factory is not None:
            return self._http_client_factory(headers=headers)
        return DeliveryHttpClient(
            base_url=self.base_url, headers=headers,
        )

    def _git(self) -> Any:
        if self._git_module is not None:
            return self._git_module
        from . import _git as real_git  # noqa: PLC0415
        return real_git

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
        existing_diff_sha256: str | None = None,
    ) -> ReviewRequestResult:
        # Same-content short-circuit (review Finding 4): if the
        # orchestrator already saw an open row for this signature
        # AND its recorded diff SHA matches what we're about to
        # deliver, the git pipeline would only produce a no-op
        # commit (timestamps differ) and a force-push that adds
        # noise to PR history. Probe the API for the existing PR
        # and just PATCH the body. If no PR matches the recorded
        # row (e.g. closed out-of-band), fall through to the full
        # pipeline so the operator still gets a working delivery.
        http = self._http()
        if (
            existing_diff_sha256 is not None
            and existing_diff_sha256 == diff_sha256
        ):
            existing = http.get(
                f"/repos/{self._owner}/{self._repo_name}/pulls",
                params={
                    "head": f"{self._owner}:{branch_name}",
                    "state": "open",
                },
            )
            if isinstance(existing, list) and existing:
                pr = existing[0]
                pr_number = pr.get("number")
                updated = http.patch(
                    f"/repos/{self._owner}/{self._repo_name}"
                    f"/pulls/{pr_number}",
                    json={"body": body},
                )
                url = (
                    (updated or pr).get("html_url")
                    or pr.get("html_url")
                )
                self._apply_labels_best_effort(
                    http, pr_number, labels,
                )
                return ReviewRequestResult(
                    provider=self.name,
                    provider_pr_id=str(pr_number),
                    url=url,
                    branch=branch_name,
                    title=title,
                    status="updated",
                )
            # Fall through: recorded row exists but no open PR
            # found on GitHub. Run the full pipeline.

        # Steps 1-4: local git work. prepare_clean_branch stays
        # OUTSIDE the try/finally: it asserts the clone is clean +
        # on base before touching anything, so if it raises the
        # clone was already in a bad state we didn't create — and
        # restore_to_base (reset --hard) would destroy the operator's
        # own uncommitted work. Once it succeeds, every subsequent
        # step is our doing, so the finally restores base on any exit
        # (success or failure) — otherwise a mid-pipeline failure
        # (e.g. push auth error) leaves the clone wedged on the
        # feature branch and the next Accept's clean-base precondition
        # refuses.
        git = self._git()
        git.prepare_clean_branch(
            clone_dir,
            base_branch=base_branch,
            branch_name=branch_name,
        )
        try:
            git.apply_diff(clone_dir, diff_text)
            git.commit_diff(
                clone_dir, title=title, body=body, signoff=True,
                committer_name=self.committer_name,
                committer_email=self.committer_email,
            )
            git.push_branch(
                clone_dir, branch_name=branch_name, token=self.token,
            )

            # Step 5: idempotency check — does an open PR already
            # exist for this head branch? `head` qualifier is
            # `owner:branch`.
            existing = http.get(
                f"/repos/{self._owner}/{self._repo_name}/pulls",
                params={
                    "head": f"{self._owner}:{branch_name}",
                    "state": "open",
                },
            )
            # GitHub returns a list. Empty → no existing PR. Non-empty
            # → take the first (an open head should be unique to one
            # PR; GitHub enforces this).
            if isinstance(existing, list) and existing:
                pr = existing[0]
                pr_number = pr.get("number")
                # PATCH the body so the operator sees up-to-date
                # context on the existing PR.
                updated = http.patch(
                    f"/repos/{self._owner}/{self._repo_name}"
                    f"/pulls/{pr_number}",
                    json={"body": body},
                )
                url = (updated or pr).get("html_url") or pr.get("html_url")
                self._apply_labels_best_effort(
                    http, pr_number, labels,
                )
                return ReviewRequestResult(
                    provider=self.name,
                    provider_pr_id=str(pr_number),
                    url=url,
                    branch=branch_name,
                    title=title,
                    status="updated",
                )

            # Step 6: create fresh PR.
            created = http.post(
                f"/repos/{self._owner}/{self._repo_name}/pulls",
                json={
                    "title": title,
                    "head": branch_name,
                    "base": base_branch,
                    "body": body,
                    "draft": draft,
                },
            )
            pr_number = created.get("number")
            url = created.get("html_url")
            if pr_number is None:
                raise DeliveryError(
                    f"GitHub PR creation returned no number; body: "
                    f"{str(created)[:300]}"
                )
            # Step 7: labels (best-effort).
            self._apply_labels_best_effort(http, pr_number, labels)
            return ReviewRequestResult(
                provider=self.name,
                provider_pr_id=str(pr_number),
                url=url,
                branch=branch_name,
                title=title,
                status="created",
            )
        finally:
            git.restore_to_base(
                clone_dir,
                base_branch=base_branch,
                scope_paths=git.changed_paths(diff_text),
            )

    def _apply_labels_best_effort(
        self,
        http: DeliveryHttpClient,
        pr_number: int,
        labels: list[str],
    ) -> None:
        """Add labels to the PR. Best-effort: any failure here is
        logged-but-swallowed (the PR exists; labels are an
        operator-quality-of-life concern, not a delivery
        correctness one).

        GitHub's labels endpoint lives under /issues/ not /pulls/
        because PRs are issues under the hood.
        """
        if not labels:
            return
        try:
            http.post(
                f"/repos/{self._owner}/{self._repo_name}"
                f"/issues/{pr_number}/labels",
                json={"labels": list(labels)},
            )
        except DeliveryError:
            # Swallow — the delivery is successful even without
            # labels. The operator can add them by hand.
            pass
