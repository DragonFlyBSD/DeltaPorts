"""``reset_port`` wipes the per-origin WRKDIR, resets the substrate
to HEAD, and re-materializes the compose tree from baseline.

Cross-run pollution before this evolved: early Step 25g only did
the substrate reset, leaving WRKDIR populated; that became the
first regression (next job's ``extract`` no-op'd, agent's
``get_file`` read polluted source). The follow-up adds a baseline
``reapply`` so the compose tree at
``/work/artifacts/compose/<target>/<origin>/`` reflects HEAD
rather than the agent's last patched output — otherwise an
operator verify (or the next attempt's first read) starts against
stale compose output.

Stage order: ``make clean`` (best-effort) → substrate reset
(load-bearing) → ``reapply`` (best-effort). ``make clean`` runs
first against the still-patched substrate because its in-tree
Makefile is what the existing WRKDIR was authored against;
``reapply`` runs last against the now-reset substrate so the
composed tree reflects HEAD. A ``reapply`` failure is treated as
"baseline already broken" — surfaced but not flipped to ok=False.

Tests cover:
- Successful all three stages → ok=True, workdir_clean_ok=True,
  reapply_ok=True, calls fired in the documented order.
- ``make clean`` failure → ok=True, workdir_clean_ok=False,
  workdir_clean_error present; substrate reset and reapply still
  run.
- Substrate reset failure → ok=False, reapply NOT invoked.
- ``reapply`` failure → ok=True, reapply_ok=False, reapply_error
  present.
- WRKSRC + materialize caches are cleared on every reset.
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


def _make_exec_recorder(reset_rc=0, clean_rc=0, reapply_rc=0,
                        reset_out="", reset_err="",
                        clean_out="", clean_err="",
                        reapply_out="", reapply_err=""):
    """Return (recorded_calls, fake_exec). The fake routes by argv
    shape: ``reapply`` as argv[0] → reapply; otherwise a
    ``/bin/sh -c <cmd>`` shape with ``git checkout`` in the cmd →
    substrate reset, ``make `` in the cmd → workdir clean. Other
    invocations raise to flag unexpected calls."""
    calls: list[tuple[str, ...]] = []

    def _fake(env, *argv, **kwargs):
        calls.append(argv)
        if argv and argv[0] == "reapply":
            return SimpleNamespace(returncode=reapply_rc,
                                   stdout=reapply_out, stderr=reapply_err)
        cmd = argv[-1] if argv else ""
        if "git checkout" in cmd:
            return SimpleNamespace(returncode=reset_rc,
                                   stdout=reset_out, stderr=reset_err)
        if "make " in cmd:
            return SimpleNamespace(returncode=clean_rc,
                                   stdout=clean_out, stderr=clean_err)
        raise AssertionError(f"unexpected _exec invocation: {argv!r}")

    return calls, _fake


def test_reset_port_runs_clean_then_substrate_then_reapply(monkeypatch):
    calls, fake = _make_exec_recorder()
    monkeypatch.setattr(worker, "_exec", fake)

    result = worker.reset_port("test-env", "devel/foo")

    assert result["ok"] is True
    assert result["workdir_clean_ok"] is True
    assert result["reapply_ok"] is True
    # C2: substrate reset is whole-tree, so leftovers outside the origin
    # subtree (failed-run dirt, slave→master writes) are rolled back too.
    assert result["paths_changed"] == ["."]
    # Three invocations in the documented order: clean (runs
    # against the still-patched substrate), then substrate reset,
    # then reapply (against the now-reset baseline).
    assert len(calls) == 3
    assert "make " in calls[0][-1]
    assert "WRKDIRPREFIX=" in calls[0][-1]
    assert "git checkout HEAD -- ." in calls[1][-1]
    assert "git clean -fd" in calls[1][-1]
    assert calls[2][0] == "reapply"
    assert calls[2][1] == "devel/foo"


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
    keys but does not flip the result to ok=False — substrate reset
    (load-bearing) still runs and succeeds."""
    calls, fake = _make_exec_recorder(
        clean_rc=2, clean_err="make: no such target 'clean'",
    )
    monkeypatch.setattr(worker, "_exec", fake)

    result = worker.reset_port("test-env", "devel/foo")

    assert result["ok"] is True
    assert result["workdir_clean_ok"] is False
    assert "make: no such target" in result["workdir_clean_error"]
    # All three stages still ran — make clean failure must not
    # short-circuit the substrate reset or reapply.
    assert len(calls) == 3


def test_reset_port_tolerates_reapply_failure(monkeypatch):
    """``reapply`` failure means baseline HEAD itself doesn't
    compose — that was the state before reset_port ran, so it
    isn't a regression we caused. Surface it but don't flip ok."""
    _, fake = _make_exec_recorder(
        reapply_rc=2,
        reapply_err="compose: E_COMPOSE_APPLY_FAILED on ports/devel/foo",
    )
    monkeypatch.setattr(worker, "_exec", fake)

    result = worker.reset_port("test-env", "devel/foo")

    assert result["ok"] is True
    assert result["reapply_ok"] is False
    assert "E_COMPOSE_APPLY_FAILED" in result["reapply_error"]


def test_reset_port_substrate_failure_skips_reapply(monkeypatch):
    """If the substrate reset itself fails, don't proceed to
    reapply — the compose tree we'd regenerate would be against a
    half-reset substrate. The result must reflect the
    substrate-reset error."""
    calls, fake = _make_exec_recorder(
        reset_rc=128, reset_err="fatal: not a git repository",
    )
    monkeypatch.setattr(worker, "_exec", fake)

    result = worker.reset_port("test-env", "devel/foo")

    assert result["ok"] is False
    assert result["rc"] == 128
    assert "fatal: not a git repository" in result["stderr_tail"]
    # Only the clean + substrate commands fired; reapply was
    # skipped because we're returning early on substrate failure.
    assert len(calls) == 2
    assert "make " in calls[0][-1]
    assert "git checkout" in calls[1][-1]


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


def test_reset_port_does_not_leak_state_when_caches_were_empty(monkeypatch):
    """Empty-cache case: no entries to pop, behavior is the same."""
    _, fake = _make_exec_recorder()
    monkeypatch.setattr(worker, "_exec", fake)

    result = worker.reset_port("test-env", "devel/foo")

    assert result["ok"] is True
    assert ("test-env", "devel/foo") not in worker._WRKSRC_CACHE
    assert ("test-env", "devel/foo") not in worker._MATERIALIZE_STATE
