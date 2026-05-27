"""Tests for delivery.github — the GitHubProvider.

Monkeypatch both the HTTP layer and the git module so no
network call and no real git operation fires. The tests cover:

- Happy create path: no existing PR → POST → returns 'created'.
- Idempotency: existing open PR → PATCH → returns 'updated'.
- Labels failure is best-effort: PR still returned even if the
  label-add call raises.
- Invalid repo slug rejected on construction.
- Each git step's failure surfaces as DeliveryError (orchestrator
  catches at the boundary).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from dportsv3.delivery import (
    DeliveryAuthError,
    DeliveryConfigError,
    DeliveryError,
)
from dportsv3.delivery.github import GitHubProvider


# ---------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------


@dataclass
class _FakeHttp:
    """Replaces DeliveryHttpClient. Records every call; returns
    a pre-canned response per (method, path-prefix)."""
    get_responses: list[Any] = field(default_factory=list)
    post_responses: list[Any] = field(default_factory=list)
    patch_responses: list[Any] = field(default_factory=list)
    calls: list[dict[str, Any]] = field(default_factory=list)
    headers: dict[str, str] = field(default_factory=dict)

    def get(self, path, *, params=None):
        self.calls.append(
            {"method": "GET", "path": path, "params": params}
        )
        if not self.get_responses:
            raise AssertionError(f"unexpected GET {path}")
        return self.get_responses.pop(0)

    def post(self, path, *, json=None):
        self.calls.append(
            {"method": "POST", "path": path, "json": json}
        )
        if not self.post_responses:
            raise AssertionError(f"unexpected POST {path}")
        resp = self.post_responses.pop(0)
        if isinstance(resp, Exception):
            raise resp
        return resp

    def patch(self, path, *, json=None):
        self.calls.append(
            {"method": "PATCH", "path": path, "json": json}
        )
        if not self.patch_responses:
            raise AssertionError(f"unexpected PATCH {path}")
        return self.patch_responses.pop(0)


@dataclass
class _FakeGit:
    """Replaces the _git module. Each method records its call;
    can be configured to raise."""
    raises_on: str | None = None
    raise_exc: Exception | None = None
    calls: list[str] = field(default_factory=list)

    def _maybe_raise(self, name):
        self.calls.append(name)
        if self.raises_on == name and self.raise_exc is not None:
            raise self.raise_exc

    def prepare_clean_branch(self, clone_dir, **kw):
        self._maybe_raise("prepare_clean_branch")

    def apply_diff(self, clone_dir, diff_text):
        self._maybe_raise("apply_diff")

    def commit_diff(self, clone_dir, **kw):
        self._maybe_raise("commit_diff")

    def push_branch(self, clone_dir, **kw):
        self._maybe_raise("push_branch")


def _make_provider(http=None, git=None):
    return GitHubProvider(
        token="ghp_test",
        repo="DragonFlyBSD/DeltaPorts",
        _http_client_factory=lambda headers: (http or _FakeHttp(
            headers=headers,
        )),
        _git_module=git or _FakeGit(),
    )


def _common_args():
    return {
        "clone_dir": Path("/unused"),
        "branch_name": "agentic/devel-foo-20260527",
        "base_branch": "master",
        "title": "devel/foo: fix dsynth build",
        "body": "Verified by verify-fix.\n",
        "labels": ["agentic-fix"],
        "diff_text": "--- a/x\n+++ b/x\n",
        "diff_sha256": "0" * 64,
        "draft": True,
    }


# ---------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------


def test_repo_must_be_owner_slash_name():
    with pytest.raises(DeliveryConfigError, match="owner/name"):
        GitHubProvider(token="x", repo="DragonFlyBSD-no-slash")


def test_http_carries_correct_headers():
    """Auth shape uses Bearer + the GitHub Accept + API-Version
    headers per GitHub's documented client conventions."""
    captured: dict[str, Any] = {}

    def factory(headers):
        captured["headers"] = headers
        return _FakeHttp(
            get_responses=[[]],
            post_responses=[
                {"number": 1, "html_url": "https://x"},
                [],  # labels endpoint response
            ],
        )

    GitHubProvider(
        token="ghp_abc",
        repo="o/r",
        _http_client_factory=factory,
        _git_module=_FakeGit(),
    ).create_review_request(**_common_args())

    assert captured["headers"]["Authorization"] == "Bearer ghp_abc"
    assert "vnd.github" in captured["headers"]["Accept"]


# ---------------------------------------------------------------------
# Happy create path (no existing PR → POST)
# ---------------------------------------------------------------------


def test_create_path_happy(capsys):
    http = _FakeHttp(
        get_responses=[[]],  # no existing open PR
        post_responses=[
            # The PR create response.
            {"number": 4242, "html_url": "https://github.com/o/r/pull/4242"},
            # The labels endpoint response.
            [],
        ],
    )
    git = _FakeGit()
    provider = _make_provider(http=http, git=git)
    result = provider.create_review_request(**_common_args())

    assert result.status == "created"
    assert result.provider_pr_id == "4242"
    assert result.url == "https://github.com/o/r/pull/4242"
    assert result.branch == "agentic/devel-foo-20260527"

    # Git pipeline ran in order.
    assert git.calls == [
        "prepare_clean_branch", "apply_diff",
        "commit_diff", "push_branch",
    ]

    # HTTP sequence: GET existing → POST create → POST labels.
    methods = [c["method"] for c in http.calls]
    assert methods == ["GET", "POST", "POST"]
    assert http.calls[0]["path"] == (
        "/repos/DragonFlyBSD/DeltaPorts/pulls"
    )
    assert http.calls[0]["params"]["state"] == "open"
    assert http.calls[1]["json"]["title"] == (
        "devel/foo: fix dsynth build"
    )
    assert http.calls[1]["json"]["head"] == "agentic/devel-foo-20260527"
    assert http.calls[1]["json"]["base"] == "master"
    assert http.calls[1]["json"]["draft"] is True
    assert "issues/4242/labels" in http.calls[2]["path"]
    assert http.calls[2]["json"]["labels"] == ["agentic-fix"]


# ---------------------------------------------------------------------
# Idempotency (existing open PR → PATCH)
# ---------------------------------------------------------------------


def test_updated_path_when_existing_open_pr():
    existing = {
        "number": 99,
        "html_url": "https://github.com/o/r/pull/99",
    }
    http = _FakeHttp(
        get_responses=[[existing]],   # one matching open PR
        post_responses=[[]],          # labels (best-effort)
        patch_responses=[{
            "number": 99,
            "html_url": "https://github.com/o/r/pull/99",
        }],
    )
    git = _FakeGit()
    provider = _make_provider(http=http, git=git)
    result = provider.create_review_request(**_common_args())

    assert result.status == "updated"
    assert result.provider_pr_id == "99"
    assert result.url == "https://github.com/o/r/pull/99"

    # PATCH targeted the existing PR's body, not a new POST.
    methods = [c["method"] for c in http.calls]
    assert methods == ["GET", "PATCH", "POST"]
    assert "pulls/99" in http.calls[1]["path"]
    assert http.calls[1]["json"] == {"body": "Verified by verify-fix.\n"}


# ---------------------------------------------------------------------
# Labels failure is best-effort
# ---------------------------------------------------------------------


def test_labels_failure_does_not_fail_delivery():
    """The PR is the load-bearing artifact; labels are quality-of-
    life. A labels-endpoint failure must not flip the delivery to
    create_failed."""
    http = _FakeHttp(
        get_responses=[[]],
        post_responses=[
            {"number": 5, "html_url": "https://x/pull/5"},
            DeliveryError("labels endpoint returned 422"),
        ],
    )
    provider = _make_provider(http=http)
    result = provider.create_review_request(**_common_args())
    assert result.status == "created"
    assert result.provider_pr_id == "5"


def test_no_labels_skips_labels_call():
    """If labels=[] no extra POST fires."""
    http = _FakeHttp(
        get_responses=[[]],
        post_responses=[
            {"number": 7, "html_url": "https://x/pull/7"},
        ],
    )
    provider = _make_provider(http=http)
    args = _common_args()
    args["labels"] = []
    provider.create_review_request(**args)
    methods = [c["method"] for c in http.calls]
    assert methods == ["GET", "POST"]


# ---------------------------------------------------------------------
# Git failures propagate
# ---------------------------------------------------------------------


def test_git_apply_failure_propagates_without_http_call():
    """If apply_diff raises, no HTTP request is made — orchestrator's
    create_failed path catches the exception."""
    http = _FakeHttp()
    git = _FakeGit(
        raises_on="apply_diff",
        raise_exc=DeliveryError("apply rejected"),
    )
    provider = _make_provider(http=http, git=git)
    with pytest.raises(DeliveryError, match="apply rejected"):
        provider.create_review_request(**_common_args())
    assert http.calls == []  # no PRs touched


def test_git_push_failure_propagates_without_http_call():
    http = _FakeHttp()
    git = _FakeGit(
        raises_on="push_branch",
        raise_exc=DeliveryError("permission denied"),
    )
    provider = _make_provider(http=http, git=git)
    with pytest.raises(DeliveryError, match="permission denied"):
        provider.create_review_request(**_common_args())
    assert http.calls == []


# ---------------------------------------------------------------------
# Auth failure shows up cleanly
# ---------------------------------------------------------------------


def test_auth_error_from_http_layer_propagates():
    http = _FakeHttp(
        get_responses=[DeliveryAuthError("bad token")],
    )
    # _FakeHttp doesn't raise from get() — emulate via the
    # response queue holding an exception. Adjust the fake to
    # raise if the popped value is an Exception.
    http.get_responses = [None]  # placeholder

    class _AuthHttp(_FakeHttp):
        def get(self, path, *, params=None):
            self.calls.append(
                {"method": "GET", "path": path, "params": params}
            )
            raise DeliveryAuthError("bad token")

    provider = _make_provider(http=_AuthHttp())
    with pytest.raises(DeliveryAuthError, match="bad token"):
        provider.create_review_request(**_common_args())


# ---------------------------------------------------------------------
# Finding 4: same-content short-circuit
# ---------------------------------------------------------------------


def test_same_content_short_circuits_git_pipeline():
    """When existing_diff_sha256 matches the incoming diff_sha256
    AND the GitHub API still reports an open PR on the branch,
    skip the git pipeline + force-push entirely and just PATCH
    the body. Avoids producing noise commits with fresh
    timestamps for byte-identical re-Accepts."""
    sha = "a" * 64
    existing = {"number": 77, "html_url": "https://github.com/o/r/pull/77"}
    http = _FakeHttp(
        get_responses=[[existing]],
        patch_responses=[{
            "number": 77, "html_url": "https://github.com/o/r/pull/77",
        }],
        post_responses=[[]],  # labels
    )
    git = _FakeGit()
    provider = _make_provider(http=http, git=git)
    args = _common_args()
    args["diff_sha256"] = sha
    result = provider.create_review_request(
        **args, existing_diff_sha256=sha,
    )

    assert result.status == "updated"
    assert result.provider_pr_id == "77"
    # No git operations ran.
    assert git.calls == []
    # HTTP sequence: GET (lookup) → PATCH (body) → POST (labels).
    methods = [c["method"] for c in http.calls]
    assert methods == ["GET", "PATCH", "POST"]


def test_different_content_runs_full_pipeline():
    """When the incoming SHA differs from the recorded SHA, the
    short-circuit must not engage — re-deliver via the git
    pipeline so the operator's PR head reflects new content."""
    http = _FakeHttp(
        get_responses=[[{
            "number": 81, "html_url": "https://github.com/o/r/pull/81",
        }]],
        patch_responses=[{
            "number": 81, "html_url": "https://github.com/o/r/pull/81",
        }],
        post_responses=[[]],
    )
    git = _FakeGit()
    provider = _make_provider(http=http, git=git)
    args = _common_args()
    args["diff_sha256"] = "b" * 64
    result = provider.create_review_request(
        **args, existing_diff_sha256="c" * 64,
    )
    assert result.status == "updated"
    # Full git pipeline ran despite the SHA-comparison path
    # being reachable.
    assert git.calls == [
        "prepare_clean_branch", "apply_diff",
        "commit_diff", "push_branch",
    ]


def test_no_existing_sha_runs_full_pipeline():
    """Fresh delivery (no recorded SHA on file) → full pipeline.
    Same as the create-path happy test but exercises the explicit
    None plumbing."""
    http = _FakeHttp(
        get_responses=[[]],
        post_responses=[
            {"number": 9, "html_url": "https://x/pull/9"},
            [],
        ],
    )
    git = _FakeGit()
    provider = _make_provider(http=http, git=git)
    provider.create_review_request(
        **_common_args(), existing_diff_sha256=None,
    )
    assert git.calls == [
        "prepare_clean_branch", "apply_diff",
        "commit_diff", "push_branch",
    ]


def test_short_circuit_falls_through_when_no_open_pr_found():
    """Edge case: recorded SHA matches, but GitHub reports no
    open PR on the branch (e.g. closed out-of-band). Run the
    full pipeline so the operator still gets a delivery rather
    than a silent no-op."""
    sha = "d" * 64
    http = _FakeHttp(
        # First GET = short-circuit probe returns [].
        # Second GET = post-push idempotency check returns [].
        get_responses=[[], []],
        post_responses=[
            {"number": 10, "html_url": "https://x/pull/10"},
            [],  # labels
        ],
    )
    git = _FakeGit()
    provider = _make_provider(http=http, git=git)
    args = _common_args()
    args["diff_sha256"] = sha
    result = provider.create_review_request(
        **args, existing_diff_sha256=sha,
    )
    assert result.status == "created"
    assert git.calls == [
        "prepare_clean_branch", "apply_diff",
        "commit_diff", "push_branch",
    ]
