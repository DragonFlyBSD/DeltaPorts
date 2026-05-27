"""Tests for delivery._http — the httpx wrapper.

Monkeypatch the client factory so no real HTTP fires. The tests
verify request shape (URL / headers / body / params), response
parsing, and the exception mapping (401/403 → auth, 429 → rate
limit after retries, others → generic DeliveryError).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx
import pytest

from dportsv3.delivery import (
    DeliveryAuthError,
    DeliveryError,
    DeliveryRateLimitError,
)
from dportsv3.delivery._http import DeliveryHttpClient


@dataclass
class _FakeResponse:
    status_code: int
    json_body: Any = None
    text_body: str = ""
    headers: dict[str, str] | None = None

    def json(self):
        if self.json_body is None:
            raise ValueError("no json")
        return self.json_body

    @property
    def text(self):
        return self.text_body


class _FakeClient:
    """Replaces httpx.Client. Stores the request and returns a
    pre-canned response sequence."""

    captured: list[dict[str, Any]] = []
    responses: list[_FakeResponse] = []

    def __init__(self, *, timeout: float = 30.0):
        self.timeout = timeout

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def request(self, method, url, *, json=None, params=None, headers=None):
        _FakeClient.captured.append({
            "method": method, "url": url,
            "json": json, "params": params,
            "headers": dict(headers or {}),
        })
        if not _FakeClient.responses:
            raise AssertionError(
                "test exhausted the pre-canned response queue"
            )
        resp = _FakeClient.responses.pop(0)
        # Real httpx.Response exposes .headers as a dict-like;
        # our fake wraps. The wrapper code only does .get() so
        # dict suffices.
        resp.headers = resp.headers or {}
        return resp


@pytest.fixture(autouse=True)
def _reset_fake():
    _FakeClient.captured = []
    _FakeClient.responses = []
    yield
    _FakeClient.captured = []
    _FakeClient.responses = []


def _client(**kw):
    """Construct a DeliveryHttpClient wired to the fake client."""
    return DeliveryHttpClient(
        base_url="https://api.example.com",
        headers={"Authorization": "Bearer tok"},
        _client_factory=_FakeClient,
        _sleep=lambda _s: None,   # no actual waiting in tests
        **kw,
    )


# ---------------------------------------------------------------------
# Happy paths — shape + parse
# ---------------------------------------------------------------------


def test_get_returns_parsed_json():
    _FakeClient.responses = [
        _FakeResponse(200, json_body={"ok": True, "id": 42}),
    ]
    data = _client().get("/things")
    assert data == {"ok": True, "id": 42}
    sent = _FakeClient.captured[0]
    assert sent["method"] == "GET"
    assert sent["url"] == "https://api.example.com/things"
    assert sent["headers"]["Authorization"] == "Bearer tok"


def test_post_sends_json_body():
    _FakeClient.responses = [
        _FakeResponse(201, json_body={"number": 7}),
    ]
    out = _client().post("/things", json={"name": "x"})
    assert out == {"number": 7}
    sent = _FakeClient.captured[0]
    assert sent["method"] == "POST"
    assert sent["json"] == {"name": "x"}


def test_patch_sends_json_body():
    _FakeClient.responses = [
        _FakeResponse(200, json_body={"updated": True}),
    ]
    out = _client().patch("/things/1", json={"body": "new"})
    assert out == {"updated": True}
    assert _FakeClient.captured[0]["method"] == "PATCH"


def test_get_passes_query_params():
    _FakeClient.responses = [_FakeResponse(200, json_body=[])]
    _client().get("/search", params={"q": "hello", "page": 2})
    assert _FakeClient.captured[0]["params"] == {"q": "hello", "page": 2}


def test_relative_path_joins_to_base_url():
    """Whether or not the caller includes a leading slash, the
    resulting URL is the base + path with exactly one separator."""
    _FakeClient.responses = [_FakeResponse(200, json_body={})]
    _client().get("things/1")  # no leading slash
    assert _FakeClient.captured[0]["url"] == (
        "https://api.example.com/things/1"
    )


def test_response_without_json_returns_text():
    """If the body isn't JSON, the wrapper returns the raw text
    so callers handling text/plain endpoints don't crash."""
    _FakeClient.responses = [
        _FakeResponse(200, json_body=None, text_body="hello"),
    ]
    out = _client().get("/text")
    assert out == "hello"


# ---------------------------------------------------------------------
# Auth errors — no retry, no body leak
# ---------------------------------------------------------------------


@pytest.mark.parametrize("status", [401, 403])
def test_auth_errors_raise_immediately(status):
    _FakeClient.responses = [_FakeResponse(status, text_body="bad token")]
    with pytest.raises(DeliveryAuthError, match="check the token"):
        _client().get("/anything")
    # Only one attempt fired — no retries.
    assert len(_FakeClient.captured) == 1


# ---------------------------------------------------------------------
# Rate limiting — retries with backoff, exhausts to error
# ---------------------------------------------------------------------


def test_429_retries_then_succeeds():
    _FakeClient.responses = [
        _FakeResponse(429, text_body=""),
        _FakeResponse(429, text_body=""),
        _FakeResponse(200, json_body={"ok": True}),
    ]
    data = _client(max_attempts=5).get("/x")
    assert data == {"ok": True}
    assert len(_FakeClient.captured) == 3


def test_429_exhausts_to_rate_limit_error():
    _FakeClient.responses = [
        _FakeResponse(429, text_body=""),
        _FakeResponse(429, text_body=""),
        _FakeResponse(429, text_body=""),
    ]
    with pytest.raises(DeliveryRateLimitError, match="after 3 attempts"):
        _client(max_attempts=3).get("/x")


def test_429_honors_retry_after_header(monkeypatch):
    sleeps: list[float] = []
    _FakeClient.responses = [
        _FakeResponse(429, text_body="",
                      headers={"Retry-After": "1.5"}),
        _FakeResponse(200, json_body={"ok": True}),
    ]
    client = DeliveryHttpClient(
        base_url="https://api.example.com",
        headers={"Authorization": "Bearer tok"},
        _client_factory=_FakeClient,
        _sleep=lambda s: sleeps.append(s),
    )
    client.get("/x")
    assert sleeps == [1.5]


def test_429_invalid_retry_after_falls_back_to_backoff():
    sleeps: list[float] = []
    _FakeClient.responses = [
        _FakeResponse(429, headers={"Retry-After": "not-a-number"}),
        _FakeResponse(200, json_body={}),
    ]
    client = DeliveryHttpClient(
        base_url="https://api.example.com",
        headers={"Authorization": "Bearer tok"},
        _client_factory=_FakeClient,
        _sleep=lambda s: sleeps.append(s),
    )
    client.get("/x")
    # First retry's backoff is 0.5s per the wrapper's policy
    # (2^0 * 0.5).
    assert sleeps == [0.5]


# ---------------------------------------------------------------------
# Generic errors — 4xx/5xx → DeliveryError with body excerpt
# ---------------------------------------------------------------------


def test_404_raises_delivery_error_with_body():
    _FakeClient.responses = [
        _FakeResponse(404, text_body="Not Found"),
    ]
    with pytest.raises(DeliveryError, match="HTTP 404") as exc_info:
        _client().get("/missing")
    assert "Not Found" in str(exc_info.value)


def test_500_raises_delivery_error():
    _FakeClient.responses = [
        _FakeResponse(500, text_body="oops"),
    ]
    with pytest.raises(DeliveryError, match="HTTP 500"):
        _client().get("/broken")
    # No retry on 5xx — we don't know if the server is in a state
    # where retrying helps.
    assert len(_FakeClient.captured) == 1


# ---------------------------------------------------------------------
# Connection errors
# ---------------------------------------------------------------------


@pytest.mark.parametrize("body,expected_redacted_present", [
    # Bearer header echoed in body.
    ("Bearer ghp_abc123def", True),
    # GitHub PAT in body.
    ("token leaked: ghp_xyz789mno", True),
    # GitLab PAT.
    ("trace: glpat-aaaa1111bbbb2222", True),
    # Authorization header verbatim.
    ("Authorization: Bearer secret_token", True),
    # No token in body — nothing to scrub.
    ("plain error message no secrets", False),
])
def test_error_body_excerpt_scrubs_tokens(body, expected_redacted_present):
    """Finding 6 (11d-3 review): tokens that show up in echoed
    response bodies must be redacted before they land in the
    DeliveryError (and therefore in bundle_review_requests.error)."""
    _FakeClient.responses = [_FakeResponse(500, text_body=body)]
    with pytest.raises(DeliveryError) as exc_info:
        _client().get("/x")
    msg = str(exc_info.value)
    if expected_redacted_present:
        assert "[REDACTED]" in msg
        # And the original secret is gone.
        for marker in ("ghp_abc", "ghp_xyz", "glpat-", "secret_token"):
            assert marker not in msg
    else:
        assert "[REDACTED]" not in msg


def test_httpx_error_wraps_to_delivery_error():
    class _BoomClient:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def request(self, *a, **kw):
            raise httpx.ConnectTimeout("boom")

    client = DeliveryHttpClient(
        base_url="https://api.example.com",
        headers={},
        _client_factory=_BoomClient,
        _sleep=lambda _s: None,
    )
    with pytest.raises(DeliveryError, match="ConnectTimeout"):
        client.get("/x")
