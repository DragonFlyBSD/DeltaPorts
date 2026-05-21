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
    """Refuse ``put_file`` writes under ``/work/DPorts/``.

    DPorts is the materialized buildable tree — regenerated from
    freebsd-ports + DeltaPorts overlay by ``materialize_dports``. Any
    edits there are wiped on the next regenerate, so they look like
    progress for a turn and then silently disappear. Models often
    fall into this trap; the prompt warns, the worker enforces.
    """
    if chroot_path == "/work/DPorts" or chroot_path.startswith("/work/DPorts/"):
        return {
            "ok": False,
            "error": (
                "put_file rejected: /work/DPorts/ is the materialized port "
                "tree, regenerated by materialize_dports. Edits here are "
                "wiped on the next materialize. Edit "
                "/work/DeltaPorts/ports/<origin>/ instead (patches, "
                "Makefile.DragonFly, overlay.dops, dragonfly/* files), "
                "then call materialize_dports(<origin>) to apply."
            ),
            "path": chroot_path,
            "kind": "dports_write_refused",
        }
    return None


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


def get_file(env: str, path: str) -> dict:
    """Read ``path`` from the env's writable overlay.

    Returns ``encoding='text'`` with raw UTF-8 ``content`` when the file
    decodes cleanly and contains no NULs (the common case for source,
    Makefiles, patches, docs). Falls back to ``encoding='base64'`` for
    binary content. sha256 is computed over the **bytes**, so the round
    trip via ``put_file(expected_sha256=...)`` works regardless of
    encoding.

    Distinct error envelopes for "doesn't exist" vs "is a directory"
    vs "read failed" — the agent can react usefully rather than guessing.
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

    text: str | None = None
    if b"\x00" not in data:
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = None

    if text is not None:
        return {
            "path": path,
            "encoding": "text",
            "content": text,
            "sha256": _sha256(data),
            "size": len(data),
        }
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


def emit_diff(env: str, origin: str, relpath: str) -> dict:
    """Return the working-tree diff for ``ports/<origin>/<relpath>``.

    Pure read — never commits, never stages. ``diff`` is empty when
    the file hasn't been modified vs HEAD. ``ok`` is False only on
    git invocation errors (rc >= 128), not on "no changes" (rc=0).
    """
    paths = env_paths(env)
    rel = f"ports/{origin}/{relpath}"
    p = subprocess.run(
        ["git", "-C", str(paths.deltaports), "diff", "--", rel],
        capture_output=True, text=True, check=False,
    )
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
) -> dict:
    """Recursive grep over the env's writable overlay.

    Uses POSIX ``grep -rn`` (always present on dfly) — not ripgrep,
    which isn't packaged for DragonFly. ``ok=True`` whenever grep
    ran without error, even when there were zero matches (rc=1 from
    grep is "no matches", not a failure). ``ok=False`` only when
    grep itself crashed (rc>=2) or the path is invalid.
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
    # -I skip binary files, --include= glob (gnu/bsd grep both support it).
    cmd = ["grep", "-rnIE"]
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


PORTSDIR = "/work/DPorts"
WRKDIRPREFIX = "/work/obj"


def _make_vars() -> list[str]:
    """Common make variable overrides for ports operations inside the env.

    - ``PORTSDIR``: dports tree lives at /work/DPorts, not /usr/dports.
    - ``WRKDIRPREFIX``: bsd.port.mk defaults to /usr/obj/dports which is
      read-only in the chroot; point it at writable /work/obj.
    - ``BATCH=yes``: skip config dialogs (the agent has no terminal).
    """
    return [
        f"PORTSDIR={PORTSDIR}",
        f"WRKDIRPREFIX={WRKDIRPREFIX}",
        "BATCH=yes",
    ]


def extract(env: str, origin: str) -> dict:
    """Run ``make extract`` for ``origin`` in DPorts; return WRKDIR + WRKSRC on success.

    Sets ``PORTSDIR`` and ``WRKDIRPREFIX`` because the dev-env layout
    doesn't match the conventional ``/usr/dports`` + ``/usr/obj/dports``
    paths bsd.port.mk expects. On failure (``ok`` False), ``wrkdir``
    and ``wrksrc`` are empty.
    """
    port_dir = f"{PORTSDIR}/{origin}"
    make_vars = _make_vars()
    p = _exec(env, "make", "-C", port_dir, *make_vars, "extract", cwd=port_dir)
    if p.returncode != 0:
        return _exec_result(p.returncode, p.stdout, p.stderr,
                            origin=origin, wrkdir="", wrksrc="")
    q = _exec(env, "make", "-C", port_dir, *make_vars,
              "-V", "WRKDIR", "-V", "WRKSRC", cwd=port_dir)
    if q.returncode != 0:
        return _exec_result(q.returncode, q.stdout, q.stderr,
                            origin=origin, wrkdir="", wrksrc="",
                            extract_step="query-wrkdir")
    lines = [line.strip() for line in q.stdout.splitlines() if line.strip()]
    wrkdir = lines[0] if len(lines) > 0 else ""
    wrksrc = lines[1] if len(lines) > 1 else ""
    return _exec_result(0, p.stdout, p.stderr,
                        origin=origin, wrkdir=wrkdir, wrksrc=wrksrc)


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
