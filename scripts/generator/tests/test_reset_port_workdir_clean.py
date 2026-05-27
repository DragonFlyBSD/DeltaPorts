"""``reset_port`` wipes both the substrate AND the per-origin WRKDIR.

Cross-run pollution before this change: the post-job cleanup ran
``git checkout HEAD -- ports/<origin>`` + ``git clean -fd`` on the
substrate, but left ``/work/obj/<origin>/<version>/`` populated.
Next job's ``extract()`` saw an existing WRKDIR and no-op'd; the
agent's ``get_file`` read polluted source; ``genpatch`` diffed
against stale ``.orig`` baselines.

Tests cover:
- Successful substrate reset + successful make clean → ok=True,
  workdir_clean_ok=True, both subprocess invocations fired in order.
- Successful substrate reset + failing make clean → ok=True,
  workdir_clean_ok=False, workdir_clean_error present. (clean is
  best-effort; substrate reset is the load-bearing stage.)
- Failing substrate reset → ok=False, make clean NOT invoked.
- WRKSRC + materialize caches are cleared on every reset, even
  when make clean fails.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from dportsv3.agent import worker


@pytest.fixture(autouse=True)
def _clear_caches():
    """Each test starts with empty in-process caches so the
    test's pre-seeded entries are the only state under
    observation."""
    worker._WRKSRC_CACHE.clear()
    worker._MATERIALIZE_STATE.clear()
    yield
    worker._WRKSRC_CACHE.clear()
    worker._MATERIALIZE_STATE.clear()


def _make_exec_recorder(reset_rc=0, clean_rc=0,
                       reset_out="", reset_err="",
                       clean_out="", clean_err=""):
    """Return (recorded_calls, fake_exec). The fake routes by
    the shell command substring: ``git checkout`` → reset, ``make``
    → clean. Other invocations raise to flag unexpected calls."""
    calls: list[tuple[str, ...]] = []

    def _fake(env, *argv, **kwargs):
        calls.append(argv)
        # All invocations in this code path go through
        # ``/bin/sh -c <cmd>``, so argv[-1] carries the shell cmd.
        cmd = argv[-1] if argv else ""
        if "git checkout" in cmd:
            return SimpleNamespace(returncode=reset_rc,
                                   stdout=reset_out, stderr=reset_err)
        if "make " in cmd:
            return SimpleNamespace(returncode=clean_rc,
                                   stdout=clean_out, stderr=clean_err)
        raise AssertionError(f"unexpected _exec invocation: {argv!r}")

    return calls, _fake


def test_reset_port_runs_substrate_reset_then_make_clean(monkeypatch):
    calls, fake = _make_exec_recorder()
    monkeypatch.setattr(worker, "_exec", fake)

    result = worker.reset_port("test-env", "devel/foo")

    assert result["ok"] is True
    assert result["workdir_clean_ok"] is True
    assert result["paths_changed"] == ["ports/devel/foo"]
    # Two invocations in the right order: substrate first, clean
    # second (so a clean failure can't leave the substrate dirty).
    assert len(calls) == 2
    assert "git checkout HEAD -- ports/devel/foo" in calls[0][-1]
    assert "make " in calls[1][-1]
    assert "WRKDIRPREFIX=" in calls[1][-1]


def test_reset_port_clears_wrksrc_and_materialize_caches(monkeypatch):
    """Once we've asked the WRKDIR to go away, any cached WRKSRC
    path or content hash for it is stale by definition."""
    _, fake = _make_exec_recorder()
    monkeypatch.setattr(worker, "_exec", fake)
    worker._WRKSRC_CACHE[("test-env", "devel/foo")] = "/work/obj/.../wrksrc"
    worker._MATERIALIZE_STATE[("test-env", "devel/foo")] = "a" * 64

    worker.reset_port("test-env", "devel/foo")

    assert ("test-env", "devel/foo") not in worker._WRKSRC_CACHE
    assert ("test-env", "devel/foo") not in worker._MATERIALIZE_STATE


def test_reset_port_tolerates_make_clean_failure(monkeypatch):
    """make clean is best-effort. Failure surfaces as workdir_clean_*
    keys but does not flip the result to ok=False — the substrate
    reset (load-bearing) succeeded."""
    _, fake = _make_exec_recorder(
        clean_rc=2, clean_err="make: no such target 'clean'",
    )
    monkeypatch.setattr(worker, "_exec", fake)

    result = worker.reset_port("test-env", "devel/foo")

    assert result["ok"] is True
    assert result["workdir_clean_ok"] is False
    assert "make: no such target" in result["workdir_clean_error"]


def test_reset_port_clears_caches_even_on_make_clean_failure(monkeypatch):
    """Cache invalidation is unconditional once we've decided to
    clean — a half-cleaned WRKDIR is still stale."""
    _, fake = _make_exec_recorder(clean_rc=1)
    monkeypatch.setattr(worker, "_exec", fake)
    worker._WRKSRC_CACHE[("test-env", "devel/foo")] = "/work/obj/.../wrksrc"
    worker._MATERIALIZE_STATE[("test-env", "devel/foo")] = "a" * 64

    worker.reset_port("test-env", "devel/foo")

    assert ("test-env", "devel/foo") not in worker._WRKSRC_CACHE
    assert ("test-env", "devel/foo") not in worker._MATERIALIZE_STATE


def test_reset_port_substrate_failure_skips_make_clean(monkeypatch):
    """If the substrate reset itself fails, don't proceed to make
    clean — leaves operator/diagnostic-friendly state. The result
    must reflect the substrate-reset error."""
    calls, fake = _make_exec_recorder(
        reset_rc=128, reset_err="fatal: not a git repository",
    )
    monkeypatch.setattr(worker, "_exec", fake)

    result = worker.reset_port("test-env", "devel/foo")

    assert result["ok"] is False
    assert result["rc"] == 128
    assert "fatal: not a git repository" in result["stderr_tail"]
    # Only the substrate command fired; make clean was skipped.
    assert len(calls) == 1
    assert "git checkout" in calls[0][-1]


def test_reset_port_does_not_leak_state_when_caches_were_empty(monkeypatch):
    """Empty-cache case: no entries to pop, behavior is the same."""
    _, fake = _make_exec_recorder()
    monkeypatch.setattr(worker, "_exec", fake)

    result = worker.reset_port("test-env", "devel/foo")

    assert result["ok"] is True
    assert ("test-env", "devel/foo") not in worker._WRKSRC_CACHE
    assert ("test-env", "devel/foo") not in worker._MATERIALIZE_STATE
