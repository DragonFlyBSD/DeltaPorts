"""Shared HTTP client wrapper for network delivery providers
(Step 11d-3 base; reused by GitLab + Gitea in 11d-4).

Three operations every REST provider needs: GET (list/search),
POST (create), PATCH (update). The wrapper handles:

- Token injection — the auth header shape varies per provider
  (Bearer / PRIVATE-TOKEN / token <x>), so callers supply the
  headers dict at client construction and the wrapper passes
  it through verbatim.
- Rate-limit handling — HTTP 429 with optional ``Retry-After``
  header retries with exponential backoff up to
  ``max_attempts`` total tries (1 initial + N-1 retries); on
  exhaustion raises ``DeliveryRateLimitError``.
- Auth errors — HTTP 401 / 403 → ``DeliveryAuthError`` (no
  retry; the token is the problem, retrying won't help).
- Other 4xx / 5xx → ``DeliveryError`` with the status code +
  body excerpt for the operator's create_failed row.
- Connection errors — ``httpx.HTTPError`` wrapped as
  ``DeliveryError`` so providers don't need to know about
  httpx exception hierarchies.

Constructed with a base URL + default headers; methods take a
relative path. Returns the parsed JSON body on success.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from . import (
    DeliveryAuthError,
    DeliveryError,
    DeliveryRateLimitError,
)


__all__ = ["DeliveryHttpClient"]


# Patterns we scrub from response body excerpts before they land
# in a DeliveryError message (and consequently in the operator-
# visible bundle_review_requests.error column). Some misconfigured
# proxies echo request headers in the response body; without
# scrubbing, a Bearer token could persist in the DB.
_TOKEN_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._\-]+"),
    re.compile(r"(?i)\btoken\s+[A-Za-z0-9._\-]+"),
    re.compile(r"(?i)Authorization\s*:\s*[^\r\n,;]+"),
    # GitHub fine-grained / personal access token prefixes.
    re.compile(r"\bghp_[A-Za-z0-9]+"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]+"),
    # GitLab personal access tokens.
    re.compile(r"\bglpat-[A-Za-z0-9_\-]+"),
)


def _scrub(s: str) -> str:
    """Replace recognized token/auth-header patterns with
    [REDACTED]. Belt-and-suspenders against tokens leaking via
    response-body echoes into DeliveryError messages."""
    for pat in _TOKEN_PATTERNS:
        s = pat.sub("[REDACTED]", s)
    return s


# Bound the retry backoff so a misbehaving server can't push us
# past the operator's patience. 0.5, 1, 2, 4, 8 seconds = ~15s
# total wall time at max.
_MAX_BACKOFF_SECONDS = 8.0


@dataclass
class DeliveryHttpClient:
    """Synchronous REST client used by GitHub / GitLab / Gitea
    providers. Wraps ``httpx.Client`` with auth + retry policy.

    ``base_url`` is the API root (e.g.
    ``"https://api.github.com"``). ``headers`` carries the
    provider-specific auth shape; for GitHub:

        {"Authorization": "Bearer <token>",
         "Accept": "application/vnd.github+json",
         "X-GitHub-Api-Version": "2022-11-28"}
    """
    base_url: str
    headers: dict[str, str] = field(default_factory=dict)
    timeout: float = 30.0
    # Total tries before giving up on 429s: 1 initial + (N-1)
    # retries with exponential backoff. The default of 3 means
    # the wrapper tries once, retries twice, then raises.
    max_attempts: int = 3
    # The injected sleep/client are seams the tests replace to
    # avoid actually waiting and to monkeypatch the HTTP layer.
    _sleep: Any = field(default=time.sleep)
    _client_factory: Any = field(default=httpx.Client)

    def get(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> Any:
        return self._request("GET", path, params=params)

    def post(
        self,
        path: str,
        *,
        json: dict[str, Any] | None = None,
    ) -> Any:
        return self._request("POST", path, json=json)

    def patch(
        self,
        path: str,
        *,
        json: dict[str, Any] | None = None,
    ) -> Any:
        return self._request("PATCH", path, json=json)

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        """One request with retry. Returns parsed JSON body or
        raises a structured DeliveryError subclass."""
        url = self.base_url.rstrip("/") + "/" + path.lstrip("/")
        attempt = 0
        while True:
            attempt += 1
            try:
                with self._client_factory(timeout=self.timeout) as c:
                    resp = c.request(
                        method, url, json=json, params=params,
                        headers=self.headers,
                    )
            except httpx.HTTPError as exc:
                # Connection errors / timeouts / etc. — surface
                # with a clear message but don't auto-retry; the
                # caller (orchestrator.deliver) writes a
                # create_failed row and gives up.
                raise DeliveryError(
                    f"HTTP {method} {url} failed: "
                    f"{type(exc).__name__}: {exc}"
                ) from exc

            # Success — return body.
            if 200 <= resp.status_code < 300:
                try:
                    return resp.json()
                except ValueError:
                    return resp.text

            # Auth errors are permanent — no retry, no body
            # disclosure (would log a token).
            if resp.status_code in (401, 403):
                raise DeliveryAuthError(
                    f"HTTP {resp.status_code} on {method} {url}: "
                    f"check the token / repo permissions"
                )

            # Rate limit — retry with backoff if we have budget.
            if resp.status_code == 429:
                if attempt >= self.max_attempts:
                    raise DeliveryRateLimitError(
                        f"HTTP 429 on {method} {url} after "
                        f"{self.max_attempts} attempts; abandoning"
                    )
                # Prefer Retry-After if present; otherwise
                # exponential backoff capped at _MAX_BACKOFF_SECONDS.
                retry_after = resp.headers.get("Retry-After", "")
                try:
                    delay = float(retry_after) if retry_after else 0.0
                except ValueError:
                    delay = 0.0
                if delay <= 0:
                    delay = min(2 ** (attempt - 1) * 0.5,
                                _MAX_BACKOFF_SECONDS)
                self._sleep(delay)
                continue

            # Any other status — surface as a generic delivery
            # error with body excerpt for the operator. Scrub
            # token-looking strings before they land in the
            # bundle_review_requests.error column.
            body_excerpt = _scrub((resp.text or "")[:300])
            raise DeliveryError(
                f"HTTP {resp.status_code} on {method} {url}: "
                f"{body_excerpt}"
            )
