"""WRKSRC-aware genpatch + from_dupe lookup.

The skalibs bundle surfaced two compounding bugs in the legacy
`genpatch` tool wiring: the script's strip-prefix check fails
when WORKTREE isn't set, and the filename derivation encodes the
full chroot path when called with an absolute arg. Fix: cache
WRKSRC from `extract()`, then have `genpatch()` cd into WRKSRC
and pass a relative arg (clean filename, lands in WRKSRC).
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from dportsv3.agent import worker


def _make_workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "DeltaPorts"
    ws.mkdir()
    subprocess.run(["git", "-C", str(ws), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(ws), "config", "user.email", "t@t"],
                   check=True)
    subprocess.run(["git", "-C", str(ws), "config", "user.name", "t"],
                   check=True)
    (ws / "README").write_text("baseline\n")
    subprocess.run(["git", "-C", str(ws), "add", "README"], check=True)
    subprocess.run(["git", "-C", str(ws), "commit", "-qm", "init"],
                   check=True)
    return ws


# ---------------------------------------------------------------------
# Cache + peek_wrksrc
# ---------------------------------------------------------------------


def test_peek_wrksrc_miss_returns_none():
    worker._WRKSRC_CACHE.clear()
    assert worker.peek_wrksrc("env-x", "devel/foo") is None


def test_peek_wrksrc_hit_returns_cached_value():
    worker._WRKSRC_CACHE.clear()
    worker._WRKSRC_CACHE[("env-x", "devel/foo")] = "/work/obj/devel/foo/1.0"
    try:
        assert (
            worker.peek_wrksrc("env-x", "devel/foo")
            == "/work/obj/devel/foo/1.0"
        )
    finally:
        worker._WRKSRC_CACHE.clear()


# ---------------------------------------------------------------------
# genpatch wrapper — cache-hit and cache-miss branches
# ---------------------------------------------------------------------


def test_genpatch_cache_hit_uses_cd_and_relpath(tmp_path, monkeypatch):
    """When the cache has an entry for (env, origin) and `path`
    starts with that wrksrc, the wrapper runs
    `cd <wrksrc> && WORKTREE=/work/obj genpatch <relpath>` and
    reports patch_path / patch_basename derived from the relpath."""
    worker._WRKSRC_CACHE.clear()
    worker._WRKSRC_CACHE[("env-x", "devel/foo")] = (
        "/work/obj/devel/foo/foo-1.0"
    )

    paths = SimpleNamespace(env_dir=tmp_path, writable=tmp_path,
                            deltaports=tmp_path / "DeltaPorts")
    paths.deltaports.mkdir()
    (tmp_path / "work" / "genpatch-out").mkdir(parents=True)
    monkeypatch.setattr(worker, "env_paths", lambda env: paths)

    captured = {}

    def fake_exec(env, *args, **kwargs):
        captured["env"] = env
        captured["argv"] = args
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(worker, "_exec", fake_exec)

    result = worker.genpatch(
        "env-x",
        "/work/obj/devel/foo/foo-1.0/src/include/foo.h",
    )

    cmd = captured["argv"][-1]
    # shlex.quote doesn't quote shell-safe strings; assert the
    # substantive bits rather than exact quoting.
    assert "cd /work/obj/devel/foo/foo-1.0" in cmd
    assert "WORKTREE=/work/obj" in cmd
    assert "genpatch src/include/foo.h" in cmd
    assert result["wrksrc"] == "/work/obj/devel/foo/foo-1.0"
    assert result["origin"] == "devel/foo"
    assert result["patch_basename"] == "patch-src_include_foo.h"
    assert result["patch_path"] == (
        "/work/obj/devel/foo/foo-1.0/patch-src_include_foo.h"
    )

    worker._WRKSRC_CACHE.clear()


def test_genpatch_cache_miss_uses_legacy_staging(tmp_path, monkeypatch):
    """No cache entry → wrapper falls back to
    `cd /work/genpatch-out && WORKTREE=... genpatch <abs>`. Still
    sets WORKTREE so the script's inside-work-area check doesn't
    fail-open."""
    worker._WRKSRC_CACHE.clear()
    paths = SimpleNamespace(env_dir=tmp_path, writable=tmp_path,
                            deltaports=tmp_path / "DeltaPorts")
    paths.deltaports.mkdir()
    (tmp_path / "work" / "genpatch-out").mkdir(parents=True)
    monkeypatch.setattr(worker, "env_paths", lambda env: paths)

    captured = {}

    def fake_exec(env, *args, **kwargs):
        captured["argv"] = args
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(worker, "_exec", fake_exec)

    result = worker.genpatch(
        "env-x", "/work/obj/devel/foo/foo-1.0/src/foo.c",
    )

    cmd = captured["argv"][-1]
    assert "cd /work/genpatch-out" in cmd
    assert "WORKTREE=/work/obj" in cmd
    assert "/work/obj/devel/foo/foo-1.0/src/foo.c" in cmd
    # No wrksrc-derived fields on the cache-miss path.
    assert result["wrksrc"] is None
    assert result["patch_basename"] is None
    assert result["patch_path"] is None


def test_genpatch_path_at_wrksrc_root_refuses(tmp_path, monkeypatch):
    """Caller passed the WRKSRC root itself (not a file under it).
    The script can't operate on a directory; wrapper returns
    ok=False with a clear error."""
    worker._WRKSRC_CACHE.clear()
    worker._WRKSRC_CACHE[("env-x", "devel/foo")] = (
        "/work/obj/devel/foo/foo-1.0"
    )
    paths = SimpleNamespace(env_dir=tmp_path, writable=tmp_path,
                            deltaports=tmp_path / "DeltaPorts")
    paths.deltaports.mkdir()
    monkeypatch.setattr(worker, "env_paths", lambda env: paths)
    # _exec shouldn't even fire for this case.
    monkeypatch.setattr(worker, "_exec",
                        lambda *a, **kw: pytest.fail("_exec should not run"))

    result = worker.genpatch(
        "env-x", "/work/obj/devel/foo/foo-1.0",
    )
    assert result["ok"] is False
    assert "file path under WRKSRC" in result["error"]
    worker._WRKSRC_CACHE.clear()


def test_genpatch_cache_hit_matches_on_path_prefix_only(
    tmp_path, monkeypatch,
):
    """Multiple cached ports for the same env — the wrapper picks
    the one whose wrksrc prefixes `path`. Sibling ports don't
    accidentally claim each other's paths."""
    worker._WRKSRC_CACHE.clear()
    worker._WRKSRC_CACHE[("env-x", "devel/foo")] = (
        "/work/obj/devel/foo/foo-1.0"
    )
    worker._WRKSRC_CACHE[("env-x", "devel/bar")] = (
        "/work/obj/devel/bar/bar-2.0"
    )

    paths = SimpleNamespace(env_dir=tmp_path, writable=tmp_path,
                            deltaports=tmp_path / "DeltaPorts")
    paths.deltaports.mkdir()
    monkeypatch.setattr(worker, "env_paths", lambda env: paths)
    monkeypatch.setattr(
        worker, "_exec",
        lambda *a, **kw: SimpleNamespace(
            returncode=0, stdout="", stderr=""
        ),
    )

    result = worker.genpatch(
        "env-x", "/work/obj/devel/bar/bar-2.0/lib/b.c",
    )
    assert result["wrksrc"] == "/work/obj/devel/bar/bar-2.0"
    assert result["origin"] == "devel/bar"
    worker._WRKSRC_CACHE.clear()
