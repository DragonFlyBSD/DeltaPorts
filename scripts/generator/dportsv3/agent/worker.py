"""Worker tool functions for the agent harness, on top of dev-env primitives.

Every function takes the dev-env name as its first argument. Filesystem
operations work host-side on the env's writable overlay
(``env_dir/writable/...``); commands that must run inside the chroot
shell out to ``dportsv3 dev-env exec``.

The agent sees chroot-absolute paths like ``/work/DeltaPorts/ports/...``;
those translate to ``<env_dir/writable>/work/DeltaPorts/ports/...`` on
the host. Paths outside ``/work/`` are rejected — the agent has no
business writing to ``/etc`` or anywhere else in the chroot.

No git operations. The dev-env's writable overlay is the workspace;
dirty edits stay dirty. ``emit_diff`` runs ``git diff`` for audit but
never commits.

Design note: we drive dev-env via its public CLI (``python -m dportsv3
dev-env ...``) rather than importing ``dports_dev_env`` directly. The
CLI is the contract that's stable across dev-env refactors; the
``EnvironmentStore`` internals are not. Subprocess overhead is bounded
(``env_paths`` is cached per-process; ``env_verify`` runs once per
attempt; chroot-bound ops are unavoidably subprocess anyway).
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

# How to invoke `dportsv3`. The wrapper script at the repo root is the
# only entry point that knows how to route the `dev-env` subcommand —
# it dispatches to a separate venv. Using `python -m dportsv3` would
# bypass that routing and fail.
#
# Resolution order:
#   1. DPORTSV3_CMD env var (whitespace-split) — for tests / overrides
#   2. <repo>/dportsv3 sibling lookup relative to this file
#   3. `dportsv3` on PATH (via shutil.which)
# Otherwise, raise at first use.
def _resolve_dportsv3_cmd() -> list[str]:
    override = os.environ.get("DPORTSV3_CMD")
    if override:
        return override.split()
    sibling = Path(__file__).resolve().parents[4] / "dportsv3"
    if sibling.is_file():
        return [str(sibling)]
    found = shutil.which("dportsv3")
    if found:
        return [found]
    raise RuntimeError(
        "could not locate dportsv3 wrapper "
        "(set DPORTSV3_CMD, put dportsv3 on PATH, or run from the repo)"
    )


_DPORTSV3_CMD: list[str] | None = None


def _dportsv3_cmd() -> list[str]:
    global _DPORTSV3_CMD
    if _DPORTSV3_CMD is None:
        _DPORTSV3_CMD = _resolve_dportsv3_cmd()
    return _DPORTSV3_CMD


# -----------------------------------------------------------------------------
# Path resolution + dev-env state
# -----------------------------------------------------------------------------


@dataclass(frozen=True)
class EnvPaths:
    """Host-side paths for a dev-env."""
    env_dir: Path
    writable: Path

    @property
    def deltaports(self) -> Path:
        return self.writable / "work" / "DeltaPorts"


def _run_dportsv3(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [*_dportsv3_cmd(), *args],
        capture_output=True, text=True, check=False,
    )


@lru_cache(maxsize=32)
def env_paths(env: str) -> EnvPaths:
    """Resolve host-side paths for ``env``. Cached per-process.

    Each ``dportsv3 dev-env path`` invocation costs a few hundred ms;
    caching means repeated tool calls during one patch attempt pay it
    only once. The env_dir for a given name is immutable for the
    lifetime of that env, so caching is safe.
    """
    p1 = _run_dportsv3("dev-env", "path", env)
    if p1.returncode != 0:
        raise RuntimeError(f"dev-env path failed: {(p1.stderr or p1.stdout).strip()}")
    p2 = _run_dportsv3("dev-env", "path", env, "--writable")
    if p2.returncode != 0:
        raise RuntimeError(f"dev-env path --writable failed: {(p2.stderr or p2.stdout).strip()}")
    return EnvPaths(env_dir=Path(p1.stdout.strip()), writable=Path(p2.stdout.strip()))


def env_verify(env: str) -> dict:
    """Return the env's state dict; raise if not usable.

    Wraps ``dportsv3 dev-env status NAME``. Fails only when the env is
    missing or in a non-``ready`` state (``creating``, ``destroying``,
    ``failed``). A ``root_mounted: false`` env is still usable: host-side
    tool ops operate on the writable overlay directly, and
    ``dportsv3 dev-env exec`` auto-mounts on demand for chroot ops.
    """
    p = _run_dportsv3("dev-env", "status", env)
    if p.returncode != 0:
        raise RuntimeError(f"dev-env status failed: {(p.stderr or p.stdout).strip()}")
    info = json.loads(p.stdout.strip())
    if info.get("status") != "ready":
        raise RuntimeError(f"env {env} not ready: status={info.get('status')}")
    return info


def _resolve_chroot_path(paths: EnvPaths, chroot_path: str) -> Path:
    """Translate an in-chroot absolute path to its host-side location.

    Only paths under ``/work/`` are allowed; ``..`` traversal escaping
    the writable overlay is rejected via ``Path.relative_to`` after
    realpath resolution.
    """
    if not (chroot_path == "/work" or chroot_path.startswith("/work/")):
        raise ValueError(f"path must be under /work (got {chroot_path!r})")
    rel = chroot_path.lstrip("/")  # "work/..."
    resolved = (paths.writable / rel).resolve()
    writable_root = paths.writable.resolve()
    try:
        resolved.relative_to(writable_root)
    except ValueError as exc:
        raise ValueError(f"path escapes writable overlay: {chroot_path!r}") from exc
    return resolved


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# -----------------------------------------------------------------------------
# Tool functions (host-side, simple set — chroot-bound ones land in step 2c)
# -----------------------------------------------------------------------------


def get_file(env: str, path: str) -> dict:
    """Read ``path`` from the env's writable overlay. Returns base64."""
    paths = env_paths(env)
    host = _resolve_chroot_path(paths, path)
    if not host.is_file():
        raise FileNotFoundError(f"no such file: {path}")
    data = host.read_bytes()
    return {
        "path": path,
        "encoding": "base64",
        "content": base64.b64encode(data).decode("ascii"),
        "sha256": _sha256(data),
        "size": len(data),
    }


def put_file(
    env: str,
    path: str,
    content: str,
    *,
    encoding: str = "text",
    expected_sha256: str | None = None,
) -> dict:
    """Write ``content`` to ``path`` in the env's writable overlay.

    ``encoding`` is ``"text"`` (UTF-8) or ``"base64"``. If
    ``expected_sha256`` is given, the existing file's sha256 must match
    or the write fails (optimistic lock against the LLM editing stale
    content). File mode is preserved for existing files.
    """
    paths = env_paths(env)
    host = _resolve_chroot_path(paths, path)

    if encoding == "base64":
        data = base64.b64decode(content.encode("ascii"))
    elif encoding == "text":
        data = content.encode("utf-8")
    else:
        raise ValueError(f"unknown encoding: {encoding}")

    if expected_sha256 is not None:
        if not host.is_file():
            raise RuntimeError(
                f"put_file: expected_sha256 given but file does not exist: {path}"
            )
        current = _sha256(host.read_bytes())
        if current != expected_sha256:
            raise RuntimeError(
                f"put_file: sha256 mismatch on {path} "
                f"(expected {expected_sha256[:12]}…, got {current[:12]}…)"
            )

    mode = host.stat().st_mode & 0o777 if host.is_file() else None
    host.parent.mkdir(parents=True, exist_ok=True)
    host.write_bytes(data)
    if mode is not None:
        host.chmod(mode)
    return {"path": path, "sha256": _sha256(data), "size": len(data)}


def emit_diff(env: str, origin: str, relpath: str) -> dict:
    """Return the working-tree diff for ``ports/<origin>/<relpath>``.

    Pure read — never commits, never stages. Output is empty when the
    file hasn't been modified vs HEAD.
    """
    paths = env_paths(env)
    rel = f"ports/{origin}/{relpath}"
    p = subprocess.run(
        ["git", "-C", str(paths.deltaports), "diff", "--", rel],
        capture_output=True, text=True, check=False,
    )
    if p.returncode not in (0,):
        raise RuntimeError(f"git diff failed: {p.stderr.strip()}")
    return {"origin": origin, "relpath": relpath, "diff": p.stdout}


def grep(
    env: str,
    pattern: str,
    path: str,
    *,
    include: str | None = None,
    max_bytes: int = 8192,
) -> dict:
    """Run ``rg`` over the env's writable overlay; cap output at ``max_bytes``."""
    paths = env_paths(env)
    host = _resolve_chroot_path(paths, path)
    if not host.exists():
        raise FileNotFoundError(f"no such path: {path}")
    cmd = ["rg", "--no-heading", "--line-number"]
    if include:
        cmd.extend(["-g", include])
    cmd.extend(["--", pattern, str(host)])
    p = subprocess.run(cmd, capture_output=True, text=True, check=False)
    output = p.stdout
    truncated = False
    if len(output) > max_bytes:
        output = output[:max_bytes]
        truncated = True
    return {
        "root": str(host),
        "pattern": pattern,
        "matches": output,
        "truncated": truncated,
        "max_bytes": max_bytes,
    }


# -----------------------------------------------------------------------------
# Chroot-bound tool functions — all shell out via `dportsv3 dev-env exec`
# -----------------------------------------------------------------------------


def _exec(env: str, *argv: str, cwd: str = "/work/DeltaPorts") -> subprocess.CompletedProcess:
    """Run ``argv`` inside the dev-env chroot, return CompletedProcess.

    ``dev-env exec`` auto-mounts the env root on demand. Stdout/stderr
    are captured; callers decide how to surface them to the LLM.
    """
    return subprocess.run(
        [*_dportsv3_cmd(), "dev-env", "exec", "--cwd", cwd, env, "--", *argv],
        capture_output=True, text=True, check=False,
    )


def materialize_dports(env: str, origin: str) -> dict:
    """Regenerate the DPorts tree for ``origin`` using the env's ``reapply`` helper.

    ``reapply`` wraps ``dportsv3 compose --origin ORIGIN ...`` inside
    the chroot; the helper lives at
    ``scripts/tools/dev-env/dports_dev_env/helpers.py:32-57``.
    """
    p = _exec(env, "reapply", origin)
    if p.returncode != 0:
        raise RuntimeError(
            f"materialize_dports({origin}) failed (rc={p.returncode}): "
            f"{(p.stderr or p.stdout).strip()[:300]}"
        )
    return {"origin": origin, "stdout": p.stdout, "stderr": p.stderr}


def extract(env: str, origin: str) -> dict:
    """Run ``make extract`` for ``origin`` in DPorts; return WRKDIR + WRKSRC.

    Issued inside the chroot; the actual extracted source ends up at
    ``WRKDIR`` (typically under ``WRKDIRPREFIX/usr/dports/<origin>/work``).
    We query the variables back with ``make -V`` so the LLM can address
    files inside WRKSRC with subsequent tools.
    """
    port_dir = f"/work/DPorts/{origin}"
    p = _exec(env, "make", "-C", port_dir, "extract", cwd=port_dir)
    if p.returncode != 0:
        raise RuntimeError(
            f"extract({origin}) failed (rc={p.returncode}): "
            f"{(p.stderr or p.stdout).strip()[:300]}"
        )
    q = _exec(env, "make", "-C", port_dir, "-V", "WRKDIR", "-V", "WRKSRC", cwd=port_dir)
    if q.returncode != 0:
        raise RuntimeError(f"could not query WRKDIR/WRKSRC: {q.stderr.strip()}")
    lines = [line.strip() for line in q.stdout.splitlines() if line.strip()]
    wrkdir = lines[0] if len(lines) > 0 else ""
    wrksrc = lines[1] if len(lines) > 1 else ""
    return {"origin": origin, "wrkdir": wrkdir, "wrksrc": wrksrc}


def dupe(env: str, path: str) -> dict:
    """Run ``dupe PATH`` inside the chroot (clone-with-backup of a WRKSRC file).

    ``path`` is an in-chroot absolute path to the source file. ``dupe``
    creates a ``<file>.orig`` snapshot so a later ``genpatch`` can
    produce a unified diff against the unmodified original.
    """
    p = _exec(env, "dupe", path)
    if p.returncode != 0:
        raise RuntimeError(
            f"dupe({path}) failed (rc={p.returncode}): "
            f"{(p.stderr or p.stdout).strip()[:300]}"
        )
    return {"path": path, "stdout": p.stdout}


def genpatch(env: str, path: str) -> dict:
    """Run ``genpatch PATH`` inside the chroot; return generated patch files.

    ``genpatch`` writes its output under ``/work/genpatch-out/`` (or
    wherever the in-chroot helper is configured); we list the resulting
    ``patch-*`` files for ``install_patches`` to pick up.
    """
    p = _exec(env, "genpatch", path)
    if p.returncode != 0:
        raise RuntimeError(
            f"genpatch({path}) failed (rc={p.returncode}): "
            f"{(p.stderr or p.stdout).strip()[:300]}"
        )
    paths = env_paths(env)
    genpatch_out = paths.writable / "work" / "genpatch-out"
    patches: list[str] = []
    if genpatch_out.is_dir():
        for f in sorted(genpatch_out.iterdir()):
            if f.is_file() and f.name.startswith("patch-"):
                patches.append(f.name)
    return {"path": path, "output_dir": str(genpatch_out), "patches": patches}


def install_patches(env: str, origin: str, patches: list[str] | None = None) -> dict:
    """Copy patches from ``/work/genpatch-out/`` into DeltaPorts overlay.

    Destination is
    ``<env_dir/writable>/work/DeltaPorts/ports/<origin>/dragonfly/``.
    Host-side file copy; no chroot exec needed since both source and
    destination are in the writable overlay. If ``patches`` is None,
    every ``patch-*`` file in ``genpatch-out/`` is installed.
    """
    paths = env_paths(env)
    src = paths.writable / "work" / "genpatch-out"
    dst = paths.deltaports / "ports" / origin / "dragonfly"
    if not src.is_dir():
        raise FileNotFoundError(f"genpatch output dir does not exist: {src}")
    if patches is None:
        candidates = [f for f in sorted(src.iterdir()) if f.is_file() and f.name.startswith("patch-")]
    else:
        candidates = [src / name for name in patches]
        missing = [str(p) for p in candidates if not p.is_file()]
        if missing:
            raise FileNotFoundError(f"missing patches: {missing}")
    dst.mkdir(parents=True, exist_ok=True)
    installed: list[str] = []
    for f in candidates:
        target = dst / f.name
        shutil.copy2(f, target)
        installed.append(str(target.relative_to(paths.deltaports)))
    return {"origin": origin, "destination": str(dst), "installed": installed}


def dsynth_build(env: str, origin: str) -> dict:
    """Run ``dbuild ORIGIN`` inside the chroot (dsynth build).

    The ``dbuild`` helper at
    ``scripts/tools/dev-env/dports_dev_env/helpers.py:62-90`` invokes
    ``dsynth -p $DPORTS_DSYNTH_PROFILE build <origin>``. Returns the
    rc + captured stdout/stderr; the caller decides whether
    ``rebuild_ok`` (typically: rc==0 and no failure markers).
    """
    p = _exec(env, "dbuild", origin)
    return {
        "origin": origin,
        "rc": p.returncode,
        "rebuild_ok": p.returncode == 0,
        "stdout": p.stdout,
        "stderr": p.stderr,
    }
