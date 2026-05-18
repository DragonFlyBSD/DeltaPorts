"""Worker tool functions for the agent harness, on top of dev-env primitives.

Every function takes the dev-env name as its first argument. Filesystem
operations work host-side on the env's writable overlay
(``env_dir/writable/...``); commands that must run inside the chroot
shell out to ``dportsv3 dev-env exec`` (added in step 2c).

The agent sees chroot-absolute paths like ``/work/DeltaPorts/ports/...``;
those translate to ``<env_dir/writable>/work/DeltaPorts/ports/...`` on
the host. Paths outside ``/work/`` are rejected — the agent has no
business writing to ``/etc`` or anywhere else in the chroot.

No git operations. The dev-env's writable overlay is the workspace;
dirty edits stay dirty. ``emit_diff`` runs ``git diff`` for audit but
never commits.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

DPORTSV3 = os.environ.get("DPORTSV3", "dportsv3")


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
        [DPORTSV3, *args],
        capture_output=True, text=True, check=False,
    )


def env_paths(env: str) -> EnvPaths:
    """Resolve host-side paths for ``env``.

    Calls ``dportsv3 dev-env path NAME`` and ``dportsv3 dev-env path NAME
    --writable`` — adds a few hundred ms; cache the result per job.
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

    Wraps ``dportsv3 dev-env status NAME``. Fails fast on a missing,
    failed, or unmounted env so the patch agent doesn't waste tool calls.
    """
    p = _run_dportsv3("dev-env", "status", env)
    if p.returncode != 0:
        raise RuntimeError(f"dev-env status failed: {(p.stderr or p.stdout).strip()}")
    info = json.loads(p.stdout.strip())
    if info.get("status") != "ready":
        raise RuntimeError(f"env {env} not ready: status={info.get('status')}")
    if not info.get("root_mounted"):
        raise RuntimeError(f"env {env} root not mounted")
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
