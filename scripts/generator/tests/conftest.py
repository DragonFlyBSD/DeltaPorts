"""Shared pytest fixtures for the dportsv3 test suite."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_env_resolver(monkeypatch):
    """Force env_resolver.list_available_envs to return () for every
    test by default.

    Why: the resolver's auto-pick step reads the host filesystem
    (`/var/cache/dports-dev/envs/`). On a developer machine with
    dev-envs configured, tests that expect "no env" would silently
    auto-pick a real env. We default to "no envs on disk" and let
    tests that exercise auto-pick supply their own value via the
    resolver's ``available_envs`` parameter (the test-friendly
    override that bypasses list_available_envs entirely).

    Also resets the runner's CLI-flag default so each test starts
    from a clean slate without per-file boilerplate.
    """
    from dportsv3.agent import env_resolver, runner
    monkeypatch.setattr(env_resolver, "list_available_envs", lambda: ())
    monkeypatch.setattr(runner, "_CLI_ENV_DEFAULT", None)
    # Reset the gate's TTL cache between tests — a value populated
    # in test A would bleed into test B for up to 1 s and silently
    # mask "the UI change reached the runner" behavior.
    monkeypatch.setattr(runner, "_GATE_RESOLVE_CACHE", None)
