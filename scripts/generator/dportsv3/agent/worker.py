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


# Cap per-stream output the LLM sees. Build errors live at the tail of
# the log; we preserve the LAST bytes when truncating, not the first.
_MAX_STREAM_BYTES = 32_768


def _tail(s: str, max_bytes: int = _MAX_STREAM_BYTES) -> tuple[str, bool]:
    if len(s) <= max_bytes:
        return s, False
    return "…[truncated]…\n" + s[-max_bytes:], True


def _exec_result(rc: int, stdout: str, stderr: str, **extra) -> dict:
    """Build a uniform tool-result dict from a subprocess outcome.

    LLM-facing tools return this shape so the harness/LLM can inspect
    the failure rather than recovering from an opaque exception.
    """
    out, out_trunc = _tail(stdout)
    err, err_trunc = _tail(stderr)
    return {
        "ok": rc == 0,
        "rc": rc,
        "stdout_tail": out,
        "stderr_tail": err,
        "stdout_truncated": out_trunc,
        "stderr_truncated": err_trunc,
        **extra,
    }


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
# Path guardrails — observed failure modes the PATCH_SYSTEM prompt warns
# against but weaker models violate anyway. Enforce at the tool boundary
# so the model sees a clear error result and can adjust on the next turn.
# -----------------------------------------------------------------------------


def _reject_dports_write(chroot_path: str) -> dict | None:
    """Refuse ``put_file`` writes to regenerated trees.

    Two trees are read-only outputs the agent must not write to:

    - ``/work/DPorts/`` — the **lock root**: last-known-good DPorts
      checkout. Not regenerated by materialize_dports (that's a common
      misconception). It's pinned to whatever DeltaPorts STATUS says
      was the last successful build. Edits here are out-of-band and
      ignored by every other tool.
    - ``/work/artifacts/compose/<target>/`` — the **compose root**:
      what materialize_dports outputs. Regenerated wholesale; any
      edits are wiped on the next materialize.

    Either way, the answer is the same: edit
    ``/work/DeltaPorts/ports/<origin>/`` (patches, Makefile.DragonFly,
    overlay.dops, dragonfly/*), then call materialize_dports.
    """
    is_lock_root = (
        chroot_path == "/work/DPorts"
        or chroot_path.startswith("/work/DPorts/")
    )
    is_compose_root = (
        chroot_path == "/work/artifacts/compose"
        or chroot_path.startswith("/work/artifacts/compose/")
    )
    if not (is_lock_root or is_compose_root):
        return None
    which = "lock root (/work/DPorts/)" if is_lock_root else (
        "compose root (/work/artifacts/compose/<target>/)"
    )
    return {
        "ok": False,
        "error": (
            f"put_file rejected: {chroot_path!r} is under the {which}. "
            "Lock root = last-known-good DPorts checkout (not regenerated "
            "by materialize). Compose root = materialize_dports output "
            "(wiped on every materialize). Either way, your edit will "
            "not survive. Edit /work/DeltaPorts/ports/<origin>/ instead "
            "(patches, Makefile.DragonFly, overlay.dops, dragonfly/* "
            "files), then call materialize_dports(<origin>) to apply."
        ),
        "path": chroot_path,
        "kind": "regenerated_tree_write_refused",
    }


# Paths inside the dsynth build root that are pure scaffolding /
# per-slot machinery, never the right place to look for a port's
# source. Refusing list_dir/grep here saves the agent from spending
# turns enumerating dsynth internals.
_DSYNTH_SCAFFOLDING_PREFIXES = (
    "/work/dsynth/build/Template",
)


def _reject_dsynth_scaffolding(chroot_path: str, op: str) -> dict | None:
    """Refuse list_dir/grep into dsynth's per-slot Template scaffolding.

    These paths exist (they're dsynth's read-only build "image" each
    slot mounts on top of) but they have nothing to do with any
    particular port. Models trying to find ``Makefile.in.rej`` often
    grep them blindly and burn turns on 500+ noise matches.
    """
    norm = chroot_path.rstrip("/")
    for prefix in _DSYNTH_SCAFFOLDING_PREFIXES:
        if norm == prefix or norm.startswith(prefix + "/"):
            return {
                "ok": False,
                "error": (
                    f"{op} refused: {chroot_path!r} is dsynth's per-slot "
                    "Template scaffolding (read-only build image), not a "
                    "port build dir. For a port's current build state try "
                    "/work/obj/<origin>/ (stale build artifacts) or "
                    "/work/dsynth/build/<slot>/construction/ during a "
                    "live build. For source overlays use "
                    "/work/DeltaPorts/ports/<origin>/."
                ),
                "path": chroot_path,
                "kind": "scaffolding_refused",
            }
    return None


# -----------------------------------------------------------------------------
# Tool functions (host-side, simple set — chroot-bound ones land in step 2c)
# -----------------------------------------------------------------------------


def list_dir(env: str, path: str, *, max_entries: int = 200) -> dict:
    """List the contents of a directory in the env's writable overlay.

    Returns up to ``max_entries`` entries, each with ``name``, ``kind``
    (file/dir/symlink/other), and ``size`` (for files). Useful when
    you don't know what's inside a directory or you need to find a
    config.log / patch / dragonfly-overlay file without a recursive
    grep.
    """
    refused = _reject_dsynth_scaffolding(path, op="list_dir")
    if refused is not None:
        return refused
    paths = env_paths(env)
    host = _resolve_chroot_path(paths, path)
    if not host.exists():
        return {"ok": False, "error": f"no such path: {path}", "kind": "missing", "path": path}
    if not host.is_dir():
        return {"ok": False, "error": f"not a directory: {path}", "kind": "not_a_directory", "path": path}
    entries: list[dict] = []
    truncated = False
    for i, child in enumerate(sorted(host.iterdir())):
        if i >= max_entries:
            truncated = True
            break
        if child.is_symlink():
            kind = "symlink"
            size = 0
        elif child.is_dir():
            kind = "dir"
            size = 0
        elif child.is_file():
            kind = "file"
            try:
                size = child.stat().st_size
            except OSError:
                size = 0
        else:
            kind = "other"
            size = 0
        entries.append({"name": child.name, "kind": kind, "size": size})
    return {
        "ok": True,
        "path": path,
        "entries": entries,
        "truncated": truncated,
        "total_returned": len(entries),
    }


_GET_FILE_DEFAULT_LIMIT_LINES = 200


def get_file(
    env: str,
    path: str,
    *,
    offset_lines: int = 0,
    limit_lines: int = _GET_FILE_DEFAULT_LIMIT_LINES,
) -> dict:
    """Read ``path`` from the env's writable overlay, **line-windowed**.

    Returns up to ``limit_lines`` lines starting at zero-indexed
    ``offset_lines``. Default is the first 200 lines.

    Why line-windowed: whole-file reads pile up in the conversation
    history. A 200KB ``Makefile.in`` returned to the LLM becomes
    200KB of prompt on every subsequent turn — quadratic cost. A
    smoke run on devel/libuv burned 1.5M tokens almost entirely on
    re-sent ``Makefile.in`` content. With windowed reads + grep-first
    discipline, the same investigation costs an order of magnitude less.

    The fields you get back:

    - ``content``: the slice of the file as text (UTF-8) or base64 for
      binary; line-windowing applies only to text content
    - ``total_lines``: total lines in the source file
    - ``first_line`` / ``last_line``: 1-indexed inclusive range covered
    - ``truncated``: True iff there are more lines past ``last_line``
    - ``sha256``: hash over the **full** file's bytes (stable across
      windows so ``put_file(expected_sha256=...)`` works regardless)
    - ``hint`` *(when truncated)*: a line telling you how to resume

    For binary content (NULs / non-UTF-8), the read is still
    whole-file (no line concept), but capped at 32KB to avoid the
    same problem. Use ``grep`` instead for searching binary-ish files.
    """
    paths = env_paths(env)
    host = _resolve_chroot_path(paths, path)
    if not host.exists():
        return {
            "ok": False,
            "error": f"no such file: {path}",
            "kind": "missing",
            "path": path,
        }
    if host.is_dir():
        return {
            "ok": False,
            "error": f"path is a directory; use list_dir or grep instead: {path}",
            "kind": "is_directory",
            "path": path,
        }
    if not host.is_file():
        return {
            "ok": False,
            "error": f"not a regular file: {path}",
            "kind": "not_a_regular_file",
            "path": path,
        }
    data = host.read_bytes()
    full_sha = _sha256(data)
    full_size = len(data)

    text: str | None = None
    if b"\x00" not in data:
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = None

    if text is not None:
        lines = text.splitlines(keepends=True)
        total_lines = len(lines)
        # Clamp inputs defensively. limit_lines<=0 means "use default";
        # offset_lines<0 means "from start".
        if limit_lines <= 0:
            limit_lines = _GET_FILE_DEFAULT_LIMIT_LINES
        if offset_lines < 0:
            offset_lines = 0
        start = min(offset_lines, total_lines)
        end = min(start + limit_lines, total_lines)
        window = "".join(lines[start:end])
        truncated = end < total_lines
        # When the window is empty (offset past EOF, or empty file),
        # report first_line=0 to signal "no content returned" rather
        # than a confusing 1-past-last value. Otherwise it's the
        # 1-indexed first line covered.
        if end > start:
            first_line = start + 1
        else:
            first_line = 0
        result = {
            "path": path,
            "encoding": "text",
            "content": window,
            "sha256": full_sha,
            "size": full_size,
            "total_lines": total_lines,
            "first_line": first_line,
            "last_line": end,
            "truncated": truncated,
        }
        if truncated:
            remaining = total_lines - end
            result["hint"] = (
                f"{remaining} more line(s) past line {end}. To continue, "
                f"call get_file({path!r}, offset_lines={end}). To narrow "
                f"down without reading the whole file, prefer grep "
                f"(returns only matching lines + context)."
            )
        return result

    # Binary path: cap at 32KB and report what was elided. The agent
    # almost never needs raw binary content anyway; this is a backstop.
    _MAX_BINARY_BYTES = 32_768
    if len(data) > _MAX_BINARY_BYTES:
        capped = data[:_MAX_BINARY_BYTES]
        return {
            "path": path,
            "encoding": "base64",
            "content": base64.b64encode(capped).decode("ascii"),
            "sha256": full_sha,
            "size": full_size,
            "truncated": True,
            "hint": (
                f"binary file capped at {_MAX_BINARY_BYTES} bytes of "
                f"{full_size}. Use grep or extract specific tools rather "
                f"than reading the raw bytes."
            ),
        }
    return {
        "path": path,
        "encoding": "base64",
        "content": base64.b64encode(data).decode("ascii"),
        "sha256": full_sha,
        "size": full_size,
        "truncated": False,
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

    Writes under ``/work/DPorts/`` are refused — that tree is wiped on
    the next ``materialize_dports``. The error message points the
    agent at the correct ``/work/DeltaPorts/ports/<origin>/`` path.
    """
    refused = _reject_dports_write(path)
    if refused is not None:
        return refused
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


def _git_diff_with_untracked(repo_dir: Path, rel: str) -> subprocess.CompletedProcess:
    """Run ``git diff`` for ``rel`` against HEAD, including untracked files.

    Plain ``git diff`` is silent on untracked files; agents that create
    new files (e.g. a fresh ``overlay.dops`` on a compat-mode port)
    would otherwise produce an empty diff despite a real edit.
    ``add --intent-to-add`` registers a placeholder entry so the new
    file shows up as an addition in the diff; ``reset`` after returns
    the index to its prior state so we leave no staged residue.
    """
    repo = str(repo_dir)
    subprocess.run(
        ["git", "-C", repo, "add", "--intent-to-add", "--", rel],
        capture_output=True, text=True, check=False,
    )
    diff = subprocess.run(
        ["git", "-C", repo, "diff", "--", rel],
        capture_output=True, text=True, check=False,
    )
    subprocess.run(
        ["git", "-C", repo, "reset", "--", rel],
        capture_output=True, text=True, check=False,
    )
    return diff


def emit_diff(env: str, origin: str, relpath: str) -> dict:
    """Return the working-tree diff for ``ports/<origin>/<relpath>``.

    Pure read — never commits. Captures both modified-tracked and
    freshly-created files (the ``--intent-to-add`` dance in
    ``_git_diff_with_untracked``). ``diff`` is empty only when the path
    truly hasn't changed vs HEAD. ``ok`` is False only on git invocation
    errors (rc >= 128), not on "no changes" (rc=0).
    """
    paths = env_paths(env)
    rel = f"ports/{origin}/{relpath}"
    p = _git_diff_with_untracked(paths.deltaports, rel)
    diff_text, diff_trunc = _tail(p.stdout, max_bytes=_MAX_STREAM_BYTES * 2)
    return _exec_result(
        p.returncode, "", p.stderr,
        origin=origin,
        relpath=relpath,
        diff=diff_text,
        diff_truncated=diff_trunc,
    )


def grep(
    env: str,
    pattern: str,
    path: str,
    *,
    include: str | None = None,
    max_bytes: int = 8192,
    context: int = 3,
) -> dict:
    """Recursive grep over the env's writable overlay, with context lines.

    Uses POSIX ``grep -rn`` (always present on dfly) — not ripgrep,
    which isn't packaged for DragonFly. ``ok=True`` whenever grep
    ran without error, even when there were zero matches (rc=1 from
    grep is "no matches", not a failure). ``ok=False`` only when
    grep itself crashed (rc>=2) or the path is invalid.

    ``context`` (default 3) is the number of surrounding lines
    (``grep -C``) returned per match, so the agent rarely needs to
    fall back to ``get_file`` after grep. Set to 0 to suppress
    context entirely. The token cost difference between "matches +
    a few context lines" and "the whole 200KB Makefile.in" is the
    difference between affordable and not.
    """
    refused = _reject_dsynth_scaffolding(path, op="grep")
    if refused is not None:
        refused["pattern"] = pattern
        refused["matches"] = ""
        refused["match_count"] = 0
        return refused
    paths = env_paths(env)
    host = _resolve_chroot_path(paths, path)
    if not host.exists():
        return {
            "ok": False,
            "error": f"no such path: {path}",
            "pattern": pattern,
            "matches": "",
            "match_count": 0,
        }
    # -E extended regex (closest to rg's default), -r recursive, -n line numbers,
    # -I skip binary files, --include= glob (gnu/bsd grep both support it),
    # -C <n> context lines around each match (both GNU and BSD grep support it).
    cmd = ["grep", "-rnIE"]
    if context and context > 0:
        cmd.append(f"-C{int(context)}")
    if include:
        cmd.append(f"--include={include}")
    cmd.extend(["--", pattern, str(host)])
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except OSError as exc:
        return {
            "ok": False,
            "error": f"grep invocation failed: {exc}",
            "pattern": pattern,
            "matches": "",
            "match_count": 0,
        }
    # grep exit codes: 0 = matches, 1 = no matches, ≥2 = error
    if p.returncode >= 2:
        return {
            "ok": False,
            "error": (p.stderr.strip() or f"grep exited with rc={p.returncode}"),
            "pattern": pattern,
            "matches": p.stdout,
            "match_count": 0,
        }
    output = p.stdout
    match_count = output.count("\n") if output else 0
    truncated = False
    if len(output) > max_bytes:
        output = output[:max_bytes]
        truncated = True
    return {
        "ok": True,
        "root": str(host),
        "pattern": pattern,
        "matches": output,
        "match_count": match_count,
        "truncated": truncated,
        "max_bytes": max_bytes,
    }


# -----------------------------------------------------------------------------
# Chroot-bound tool functions — all shell out via `dportsv3 dev-env exec`
# -----------------------------------------------------------------------------


def _exec(
    env: str,
    *argv: str,
    cwd: str = "/work/DeltaPorts",
    input_text: str | None = None,
    timeout: int | None = None,
) -> subprocess.CompletedProcess:
    """Run ``argv`` inside the dev-env chroot, return CompletedProcess.

    ``dev-env exec`` auto-mounts the env root on demand. Stdout/stderr
    are captured; callers decide how to surface them to the LLM.

    ``input_text`` is fed to the subprocess's stdin (useful for tools
    that prompt — e.g. dsynth's "rebuild local repository? [Y/n]"). If
    None, stdin is /dev/null (so an unexpected prompt fails fast rather
    than hanging).
    """
    return subprocess.run(
        [*_dportsv3_cmd(), "dev-env", "exec", "--quiet", "--cwd", cwd, env, "--", *argv],
        capture_output=True,
        text=True,
        check=False,
        input=input_text if input_text is not None else "",
        timeout=timeout,
    )


def materialize_dports(env: str, origin: str) -> dict:
    """Regenerate the DPorts tree for ``origin`` using ``reapply`` inside the env.

    Returns a result dict (``ok``, ``rc``, ``stdout_tail``,
    ``stderr_tail``, etc.). The LLM inspects ``ok`` and the tails
    to decide what to do — no exceptions bubble up for build failures.

    Env-level failures (missing py311 deps, broken venv) used to be
    inferred from stderr here and tagged onto the result; that path
    moved to ``dportsv3.agent.health`` as a direct probe. The runner
    gates on health before claiming jobs; tool errors that look
    health-related trigger a cache-invalidating re-probe.
    """
    p = _exec(env, "reapply", origin)
    return _exec_result(p.returncode, p.stdout, p.stderr, origin=origin)


WRKDIRPREFIX = "/work/obj"


def probe_overlay_facts(env: str, origin: str):
    """Collect raw overlay facts from inside the dev-env chroot."""
    from dportsv3.agent.overlay_state import (  # noqa: PLC0415
        OverlayFacts,
        makefile_dragonfly_text_auto_safe,
    )

    cmd = r'''
set -u
PORT="$DELTAPORTS_ROOT/ports/$1"
[ -d "$PORT" ] || { echo MISSING=1; exit 0; }
echo MISSING=0
[ -e "$PORT/overlay.dops" ] && echo DOPS=1 || echo DOPS=0
for f in "$PORT"/Makefile.DragonFly "$PORT"/Makefile.DragonFly.@any; do
    [ -e "$f" ] && echo MKDFLY=${f##*/}
done
for f in "$PORT"/Makefile.DragonFly.*; do
    [ -e "$f" ] || continue
    case "${f##*/}" in Makefile.DragonFly.@any) continue ;; esac
    echo TARGET_MKDFLY=${f##*/}
done
if [ -d "$PORT/dragonfly" ]; then
    find "$PORT/dragonfly" -type f | sed "s|^$PORT/||; s|^|DRAGONFLY_FILE=|"
fi
if [ -d "$PORT/diffs" ]; then
    find "$PORT/diffs" -type f \( -name '*.diff' -o -name '*.patch' \) \
        | sed "s|^$PORT/||; s|^|DIFF_FILE=|"
fi
[ -d "$PORT/newport" ] && echo NEWPORT=1 || echo NEWPORT=0
'''
    p = _exec(env, "/bin/sh", "-c", cmd, "_", origin)
    if p.returncode != 0:
        raise RuntimeError(
            f"overlay fact probe failed for {origin!r} in env "
            f"{env!r} (rc={p.returncode}): "
            f"{(p.stderr or '').strip()[:300]}"
        )

    flags: dict[str, list[str] | bool] = {
        "MKDFLY": [],
        "TARGET_MKDFLY": [],
        "DRAGONFLY_FILE": [],
        "DIFF_FILE": [],
    }
    for line in (p.stdout or "").splitlines():
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if key in {"MKDFLY", "TARGET_MKDFLY", "DRAGONFLY_FILE", "DIFF_FILE"}:
            flags.setdefault(key, [])
            assert isinstance(flags[key], list)
            flags[key].append(value)
        else:
            flags[key] = value == "1"

    if flags.get("MISSING"):
        return OverlayFacts(origin=origin, port_exists=False)

    makefiles = tuple(sorted(flags.get("MKDFLY") or ()))
    targeted = tuple(sorted(flags.get("TARGET_MKDFLY") or ()))
    auto_safe = False
    reasons: list[str] = []
    if len(makefiles) == 1 and makefiles[0] == "Makefile.DragonFly":
        cat_cmd = 'cat "$DELTAPORTS_ROOT/ports/$1/Makefile.DragonFly"'
        cat = _exec(env, "/bin/sh", "-c", cat_cmd, "_", origin)
        if cat.returncode == 0:
            auto_safe, reason = makefile_dragonfly_text_auto_safe(cat.stdout)
        else:
            auto_safe, reason = False, "makefile_read_error"
        reasons.append(reason)
    elif makefiles or targeted:
        reasons.append("targeted_or_multiple_makefile_dragonfly")

    return OverlayFacts(
        origin=origin,
        port_exists=True,
        overlay_dops=bool(flags.get("DOPS")),
        makefile_dragonfly=makefiles,
        targeted_makefile_dragonfly=targeted,
        dragonfly_files=tuple(sorted(flags.get("DRAGONFLY_FILE") or ())),
        diff_files=tuple(sorted(flags.get("DIFF_FILE") or ())),
        newport=bool(flags.get("NEWPORT")),
        auto_safe_makefile=auto_safe,
        makefile_reasons=tuple(reasons),
    )


def assess_dops(env: str, origin: str):
    """Return the shared overlay assessment for a port in the dev-env."""
    from dportsv3.agent.overlay_state import assess_overlay  # noqa: PLC0415
    return assess_overlay(probe_overlay_facts(env, origin))


def classify_dops(env: str, origin: str) -> str:
    """Classify a port's dops state by inspecting the dev-env's
    writable overlay (the substrate the convert agent writes into).

    Returns one of: ``converted`` / ``auto_safe_pending`` /
    ``needs_judgment`` / ``not_in_scope``.

    Probes facts inside the chroot, then feeds the shared
    overlay-state assessment used by host-side tooling. The probe
    touches:

    - ``$DELTAPORTS_ROOT/ports/<origin>/overlay.dops``
    - ``$DELTAPORTS_ROOT/ports/<origin>/Makefile.DragonFly[.*]``
    - ``$DELTAPORTS_ROOT/ports/<origin>/dragonfly/`` (with content)
    - ``$DELTAPORTS_ROOT/ports/<origin>/diffs/`` (with .diff/.patch)
    - ``$DELTAPORTS_ROOT/ports/<origin>/newport/``

    The classification rules match :func:`dportsv3.agent.dops.classify`,
    but file checks happen against the env's view, not the host clone.
    """
    return assess_dops(env, origin).state


def validate_dops(env: str, origin: str) -> dict:
    """Run ``dportsv3 dsl check`` against the port's overlay.dops.

    Used by the convert agent to validate its rewrite *before*
    emitting the Conversion Proof. Cheap — parse + semantic check
    via dportsv3.engine.api, no compose, no filesystem materialize.

    Returns ``ok=True`` only when the dsl check exits 0 (no
    diagnostics). On failure, the diagnostics (stderr) carry the
    line/column/code so the agent can fix and retry.

    The full compose-side check still runs in
    ``_verify_conversion`` after the agent finishes — this is the
    cheap inner-loop validation, not a replacement.

    Invokes the dportsv3 script via the ``DELTAPORTS_ROOT`` env var
    that dev-env's ``build_env_dict`` sets inside the chroot — the
    binary lives in the DeltaPorts checkout and isn't on the
    chroot's PATH. Using the env var (not a hardcoded path)
    survives a future relocation.
    """
    cmd = (
        '"$DELTAPORTS_ROOT/dportsv3" dsl check '
        '"$DELTAPORTS_ROOT/ports/$1/overlay.dops"'
    )
    p = _exec(env, "/bin/sh", "-c", cmd, "_", origin)
    return _exec_result(
        p.returncode, p.stdout, p.stderr,
        origin=origin,
        dops_path=f"$DELTAPORTS_ROOT/ports/{origin}/overlay.dops",
    )


_DOPS_QUICKREF_PATH = Path(__file__).resolve().parent / "dops_quickref.md"


def dops_reference(env: str) -> dict:
    """Return the on-demand dops quick-reference.

    Co-located with the agent module (NOT under ``docs/kedb/``) so
    the KEDB auto-loader doesn't ship it in every payload. Call once
    per patch attempt at most, only after confirming there's no
    ``overlay.dops`` yet and you intend to write one.

    The full normative grammar lives at ``docs/dsl-v0.md`` — this
    file is the minimal subset patch agents need most often. The
    ``env`` argument is unused (tool signature parity); the content
    is identical across envs.
    """
    try:
        text = _DOPS_QUICKREF_PATH.read_text(encoding="utf-8")
    except OSError as exc:
        return {
            "ok": False,
            "error": f"failed to read dops quick-reference: {exc}",
            "path": str(_DOPS_QUICKREF_PATH),
        }
    return {
        "ok": True,
        "content": text,
        "encoding": "text",
        "size": len(text),
        "source": "dops_quickref.md (co-located; full grammar: docs/dsl-v0.md)",
    }


def extract(env: str, origin: str) -> dict:
    """Run ``make extract`` against the **compose root** —
    ``$DPORTS_COMPOSE_ROOT`` (= ``/work/artifacts/compose/<target>``).

    Critically not the lock root at ``/work/DPorts/``. The lock root is
    the last-known-good DPorts checkout (what DeltaPorts STATUS tracks
    as ``Last success``); ``materialize_dports`` does NOT update it.
    Building against it would silently use stale Makefile + distinfo
    and produce a WRKSRC pointing at the wrong version. The compose
    root is what dsynth builds and what extract must target.

    Returns ``wrkdir`` + ``wrksrc`` from ``make -V``. ``WRKDIRPREFIX``
    is redirected to ``/work/obj`` (the writable overlay) because
    bsd.port.mk's default (``/usr/obj/dports``) is read-only.

    ``$DPORTS_COMPOSE_ROOT`` is set in the dev-env's chroot environment
    (see ``build_env_dict`` in tools/dev-env helpers); we expand it in
    the child shell rather than threading the target through every
    caller.
    """
    extract_cmd = (
        'set -e; '
        f'cd "$DPORTS_COMPOSE_ROOT/{origin}"; '
        f'make PORTSDIR="$DPORTS_COMPOSE_ROOT" '
        f'     WRKDIRPREFIX="{WRKDIRPREFIX}" '
        f'     BATCH=yes extract'
    )
    p = _exec(env, "/bin/sh", "-c", extract_cmd)
    if p.returncode != 0:
        return _exec_result(p.returncode, p.stdout, p.stderr,
                            origin=origin, wrkdir="", wrksrc="")
    query_cmd = (
        'set -e; '
        f'cd "$DPORTS_COMPOSE_ROOT/{origin}"; '
        f'make PORTSDIR="$DPORTS_COMPOSE_ROOT" '
        f'     WRKDIRPREFIX="{WRKDIRPREFIX}" '
        f'     BATCH=yes -V WRKDIR -V WRKSRC'
    )
    q = _exec(env, "/bin/sh", "-c", query_cmd)
    if q.returncode != 0:
        return _exec_result(q.returncode, q.stdout, q.stderr,
                            origin=origin, wrkdir="", wrksrc="",
                            extract_step="query-wrkdir")
    lines = [line.strip() for line in q.stdout.splitlines() if line.strip()]
    wrkdir = lines[0] if len(lines) > 0 else ""
    wrksrc = lines[1] if len(lines) > 1 else ""
    summary = (
        f"Extracted {origin} from the compose root. "
        f"wrksrc={wrksrc} — use this exact path. "
        f"Do NOT look under /work/DPorts/{origin}/ (that's the lock "
        f"root, last-known-good versions, NOT the just-extracted source)."
        if wrksrc else
        f"Extracted {origin}, but wrkdir/wrksrc query returned empty."
    )
    return _exec_result(0, p.stdout, p.stderr,
                        origin=origin, wrkdir=wrkdir, wrksrc=wrksrc,
                        summary=summary)


def dupe(env: str, path: str) -> dict:
    """Run ``dupe PATH`` inside the chroot (clones source file with .orig backup).

    Returns a result dict; LLM inspects ``ok``.
    """
    p = _exec(env, "dupe", path)
    return _exec_result(p.returncode, p.stdout, p.stderr, path=path)


def genpatch(env: str, path: str) -> dict:
    """Run ``genpatch PATH`` inside the chroot; list generated patch files.

    Always lists ``patch-*`` files from ``/work/genpatch-out/`` regardless
    of rc (genpatch may produce partial output on failure). LLM inspects
    ``ok`` to decide whether the patches are trustworthy.
    """
    p = _exec(env, "genpatch", path)
    paths = env_paths(env)
    genpatch_out = paths.writable / "work" / "genpatch-out"
    patches: list[str] = []
    if genpatch_out.is_dir():
        for f in sorted(genpatch_out.iterdir()):
            if f.is_file() and f.name.startswith("patch-"):
                patches.append(f.name)
    return _exec_result(
        p.returncode, p.stdout, p.stderr,
        path=path,
        output_dir=str(genpatch_out),
        patches=patches,
    )


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


DSYNTH_LOGS_DIR = "/work/dsynth/logs"


def _dsynth_log_path(origin: str) -> str:
    """Where dsynth writes the per-port build log inside the chroot.

    dsynth replaces '/' in the origin with '___' for its log filenames
    (per the dsynth source convention).
    """
    return f"{DSYNTH_LOGS_DIR}/{origin.replace('/', '___')}.log"


def dsynth_build(env: str, origin: str) -> dict:
    """Run ``dsynth build <origin>`` inside the chroot.

    Invokes dsynth directly (not via the ``dbuild`` helper) with:
    - ``-S`` to disable the ncurses TUI (otherwise stdout is curses
      escape codes the LLM can't parse)
    - ``-y`` to assume-yes on all prompts (no stdin gymnastics)

    The result dict carries ``log_hint``: the in-chroot path to the
    per-port build log. On failure, the agent should call
    ``dsynth_log(origin)`` to read it — stdout/stderr_tail here only
    capture the wrapper output, not the actual build error.

    The ``dbuild`` helper at
    ``scripts/tools/dev-env/dports_dev_env/helpers.py:62-90`` is
    intentionally not used: it doesn't pass -S/-y because humans use
    it interactively.
    """
    # Read DPORTS_DSYNTH_PROFILE inside the chroot (set by dev-env's
    # build_env_dict at helpers.py:113) and invoke dsynth directly.
    # Using sh -c so we can reference the env var on the chroot side.
    #
    # ``/work/.dports-agent-hooks-disabled`` short-circuits
    # dsynth-hooks/hook_common.sh so an agent-driven build never
    # produces a new failure bundle. There is one env per target —
    # the same env the operator uses for production dsynth runs — so
    # without this guard every failed attempt the agent makes would
    # fire the hooks, upload a new bundle, and the runner would
    # enqueue another triage job for an origin the agent is already
    # actively patching. That's an unbounded loop in the worst case
    # and pure waste in the best.
    #
    # A flag file (not an env var) because dsynth strips arbitrary
    # env vars before invoking hooks. The trap on EXIT cleans up even
    # if dsynth exits non-zero. ``/work`` is the dev-env's writable
    # overlay so we can write/remove it freely from any in-chroot
    # process.
    cmd = (
        'flag=/work/.dports-agent-hooks-disabled; '
        'trap "rm -f \\"$flag\\"" EXIT; '
        ': > "$flag"; '
        'dsynth -S -y -p "$DPORTS_DSYNTH_PROFILE" build "$1"'
    )
    p = _exec(env, "/bin/sh", "-c", cmd, "_", origin)
    log_hint = _dsynth_log_path(origin)
    return _exec_result(
        p.returncode, p.stdout, p.stderr,
        origin=origin,
        rebuild_ok=p.returncode == 0,
        log_hint=log_hint,
    )


def dsynth_log(env: str, origin: str, tail_lines: int = 200) -> dict:
    """Read the tail of dsynth's per-port build log.

    Call this when ``dsynth_build`` returned ``rebuild_ok=false``;
    the actual error message lives here, not in dsynth_build's
    stdout (which is just wrapper output).

    Returns the last ``tail_lines`` lines from
    ``/work/dsynth/logs/<origin-with-slashes-as-underscores>.log``.
    """
    paths = env_paths(env)
    log_path = paths.writable / "work" / "dsynth" / "logs" / f"{origin.replace('/', '___')}.log"
    if not log_path.is_file():
        return {
            "ok": False,
            "error": f"no log at {log_path}",
            "origin": origin,
            "log_path": str(log_path),
            "tail": "",
        }
    try:
        text = log_path.read_text(errors="replace")
    except OSError as exc:
        return {
            "ok": False,
            "error": f"read failed: {exc}",
            "origin": origin,
            "log_path": str(log_path),
            "tail": "",
        }
    lines = text.splitlines()
    if tail_lines > 0 and len(lines) > tail_lines:
        truncated = True
        kept = lines[-tail_lines:]
    else:
        truncated = False
        kept = lines
    return {
        "ok": True,
        "origin": origin,
        "log_path": str(log_path),
        "tail": "\n".join(kept),
        "truncated": truncated,
        "total_lines": len(lines),
    }
