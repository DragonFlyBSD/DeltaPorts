"""Step 30 slice 1: per-bundle branch lifecycle in worker.

Three behaviors under test:

1. ``_resolve_bundle_base_branch`` reads the env's
   ``origin/HEAD`` symbolic-ref, caches per env, falls back to
   ``master`` on failure.

2. ``checkout_bundle_branch`` is idempotent + state-aware:
   - already-current → no-op
   - branch exists but not current → switch
   - branch absent → switch to base first, then create
   Refuses without a bundle_id.

3. ``drop_bundle_branch`` switches off the branch (if currently
   checked out) then force-deletes. Idempotent on missing branch.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from dportsv3.agent import worker


@pytest.fixture(autouse=True)
def _clear_caches():
    worker._BUNDLE_BASE_BRANCH_CACHE.clear()
    yield
    worker._BUNDLE_BASE_BRANCH_CACHE.clear()


def _exec_recorder(scripts: dict[str, tuple[int, str, str]]):
    """Return (calls, fake_exec). ``scripts`` maps a substring of
    the shell command to (rc, stdout, stderr). The first matching
    substring wins. Unmatched commands return rc=0 with empty
    streams (safe default for "this call wasn't load-bearing").
    """
    calls: list[str] = []

    def _fake(env, *argv, **kwargs):
        cmd = argv[-1] if argv else ""
        calls.append(cmd)
        for needle, (rc, out, err) in scripts.items():
            if needle in cmd:
                return SimpleNamespace(
                    returncode=rc, stdout=out, stderr=err,
                )
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    return calls, _fake


# --- _resolve_bundle_base_branch -------------------------------------


def test_base_branch_reads_origin_head(monkeypatch):
    calls, fake = _exec_recorder({
        "symbolic-ref": (0, "origin/main\n", ""),
    })
    monkeypatch.setattr(worker, "_exec", fake)

    assert worker._resolve_bundle_base_branch("e1") == "main"
    # Cached on second call — no extra subprocess.
    assert worker._resolve_bundle_base_branch("e1") == "main"
    assert sum("symbolic-ref" in c for c in calls) == 1


def test_base_branch_fallback_to_master(monkeypatch):
    """When the symbolic-ref isn't set, the shell command's
    ``|| echo master`` fallback fires and we get master."""
    calls, fake = _exec_recorder({
        "symbolic-ref": (0, "master\n", ""),  # echo master path
    })
    monkeypatch.setattr(worker, "_exec", fake)

    # The wrapper command echoes "master" when symbolic-ref fails;
    # the function should pass that through after the origin/ strip.
    assert worker._resolve_bundle_base_branch("e1") == "master"


def test_base_branch_per_env_cache(monkeypatch):
    calls, fake = _exec_recorder({
        "symbolic-ref": (0, "origin/main\n", ""),
    })
    monkeypatch.setattr(worker, "_exec", fake)

    worker._resolve_bundle_base_branch("env-a")
    worker._resolve_bundle_base_branch("env-b")
    # Two separate envs, two separate cache entries → two
    # subprocess invocations.
    assert sum("symbolic-ref" in c for c in calls) == 2


# --- checkout_bundle_branch ------------------------------------------


def test_checkout_refuses_without_bundle_id(monkeypatch):
    result = worker.checkout_bundle_branch("e1", "")
    assert result["ok"] is False
    assert "bundle_id" in result.get("error", "").lower()


def test_checkout_noop_when_already_current(monkeypatch):
    """Convert just finished, leaving bundle/<id> checked out. The
    follow-up patch's checkout call should be a no-op."""
    calls, fake = _exec_recorder({
        "symbolic-ref": (0, "origin/main\n", ""),
        "rev-parse --abbrev-ref": (0, "bundle/b-abc\n", ""),
    })
    monkeypatch.setattr(worker, "_exec", fake)

    result = worker.checkout_bundle_branch("e1", "b-abc")
    assert result["ok"] is True
    assert result["reused"] is True
    assert result["created"] is False
    assert result["branch"] == "bundle/b-abc"
    # No checkout / branch-creation commands fired.
    assert not any("checkout" in c for c in calls)
    assert not any("git checkout -b" in c for c in calls)


def test_checkout_switches_to_existing_branch(monkeypatch):
    """Currently on bundle/<other>, target branch already exists
    (rare: would mean the bundle's branch was created earlier).
    Direct checkout, no create."""
    calls, fake = _exec_recorder({
        "symbolic-ref": (0, "origin/main\n", ""),
        "rev-parse --abbrev-ref": (0, "bundle/other\n", ""),
        "rev-parse --verify --quiet": (0, "abc123\n", ""),
    })
    monkeypatch.setattr(worker, "_exec", fake)

    result = worker.checkout_bundle_branch("e1", "b-abc")
    assert result["ok"] is True
    assert result["reused"] is True
    assert result["created"] is False
    # Existing-branch path uses plain ``git checkout <branch>``,
    # not ``-b``.
    assert any("git checkout bundle/b-abc" in c for c in calls)
    assert not any("checkout -b" in c for c in calls)


def test_checkout_creates_from_base_when_branch_absent(monkeypatch):
    """First job for a bundle. Current branch is the env's main;
    target branch doesn't exist. Should switch to base first
    (no-op if already there) then ``git checkout -b``."""
    calls, fake = _exec_recorder({
        "symbolic-ref": (0, "origin/main\n", ""),
        "rev-parse --abbrev-ref": (0, "main\n", ""),
        "rev-parse --verify --quiet": (1, "", ""),  # branch absent
    })
    monkeypatch.setattr(worker, "_exec", fake)

    result = worker.checkout_bundle_branch("e1", "b-abc")
    assert result["ok"] is True
    assert result["created"] is True
    assert result["reused"] is False
    # The create path runs ``git checkout main && git checkout -b
    # bundle/b-abc`` as a single shell pipeline.
    create_cmd = next(
        (c for c in calls if "git checkout -b bundle/b-abc" in c),
        None,
    )
    assert create_cmd is not None
    assert "git checkout main" in create_cmd


def test_checkout_creates_from_base_even_when_on_other_bundle(
    monkeypatch,
):
    """Critical case: bundle B's first job runs after bundle A's
    finished. Env is on bundle/A. We must switch to main FIRST so
    bundle/B doesn't inherit A's commits."""
    calls, fake = _exec_recorder({
        "symbolic-ref": (0, "origin/main\n", ""),
        "rev-parse --abbrev-ref": (0, "bundle/A\n", ""),
        "rev-parse --verify --quiet": (1, "", ""),  # bundle/B absent
    })
    monkeypatch.setattr(worker, "_exec", fake)

    result = worker.checkout_bundle_branch("e1", "B")
    assert result["ok"] is True
    assert result["created"] is True
    # Confirm the create command switches to base (main) first.
    create_cmd = next(
        (c for c in calls if "git checkout -b bundle/B" in c), None,
    )
    assert create_cmd is not None
    assert "git checkout main" in create_cmd


def test_checkout_surfaces_failure(monkeypatch):
    """Branch-creation subprocess fails → returns ok=False with
    the rc and stderr tail."""
    calls, fake = _exec_recorder({
        "symbolic-ref": (0, "origin/main\n", ""),
        "rev-parse --abbrev-ref": (0, "main\n", ""),
        "rev-parse --verify --quiet": (1, "", ""),
        "git checkout -b": (
            128, "", "fatal: A branch named 'bundle/X' already exists\n",
        ),
    })
    monkeypatch.setattr(worker, "_exec", fake)

    result = worker.checkout_bundle_branch("e1", "X")
    assert result["ok"] is False
    assert result["rc"] == 128
    assert "already exists" in result["stderr_tail"]
    assert "create branch" in result["error"].lower()


# --- drop_bundle_branch ----------------------------------------------


def test_drop_branch_when_currently_checked_out(monkeypatch):
    """Branch is current → must switch to base before deleting,
    otherwise git refuses."""
    calls, fake = _exec_recorder({
        "symbolic-ref": (0, "origin/main\n", ""),
        "rev-parse --abbrev-ref": (0, "bundle/done\n", ""),
        "rev-parse --verify --quiet": (0, "deadbeef\n", ""),
    })
    monkeypatch.setattr(worker, "_exec", fake)

    result = worker.drop_bundle_branch("e1", "done")
    assert result["ok"] is True
    assert result["removed"] is True
    # The drop should have fired (a) switch-to-base and (b) the
    # actual delete.
    assert any("git checkout main" in c for c in calls)
    assert any("git branch -D bundle/done" in c for c in calls)


def test_drop_branch_idempotent_on_missing(monkeypatch):
    """Branch absent → ok=True, removed=False with a reason
    string. No delete fires."""
    calls, fake = _exec_recorder({
        "symbolic-ref": (0, "origin/main\n", ""),
        "rev-parse --abbrev-ref": (0, "main\n", ""),
        "rev-parse --verify --quiet": (1, "", ""),
    })
    monkeypatch.setattr(worker, "_exec", fake)

    result = worker.drop_bundle_branch("e1", "neverexisted")
    assert result["ok"] is True
    assert result["removed"] is False
    assert result["reason"] == "branch_absent"
    assert not any("git branch -D" in c for c in calls)


def test_drop_branch_when_not_currently_checked_out(monkeypatch):
    """Branch exists but env is on another branch → no need to
    switch; just delete. Skip the switch step."""
    calls, fake = _exec_recorder({
        "symbolic-ref": (0, "origin/main\n", ""),
        "rev-parse --abbrev-ref": (0, "main\n", ""),
        "rev-parse --verify --quiet": (0, "deadbeef\n", ""),
    })
    monkeypatch.setattr(worker, "_exec", fake)

    result = worker.drop_bundle_branch("e1", "done")
    assert result["ok"] is True
    assert result["removed"] is True
    # No pre-delete checkout call should have been needed.
    assert not any(
        "git checkout main" in c and "branch -D" not in c
        for c in calls
    )


def test_drop_branch_refuses_without_bundle_id():
    result = worker.drop_bundle_branch("e1", "")
    assert result["ok"] is False
    assert "bundle_id" in result.get("error", "").lower()


def test_branch_name_strips_job_suffix(monkeypatch):
    """Defensive: caller passes a job filename by accident.
    Strip ``.job`` from the bundle name so the branch isn't
    ``bundle/<...>.job``."""
    assert worker._branch_name_for("b-abc.job") == "bundle/b-abc"
    assert worker._branch_name_for("b-abc") == "bundle/b-abc"
