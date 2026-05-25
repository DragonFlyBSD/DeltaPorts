from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from .builder import CreateOptions, EnvironmentBuilder, default_delta_root
from .config import load_config, require_root, validate_cache_root
from .errors import DevEnvError, UsageError
from .fs import safe_remove_tree
from .log import error, info, run_log_context, to_user, warn
from .mounts import mounts_under, ordered_mounts_under, unmount_under
from .session import EnvironmentSession
from .store import EnvironmentStore
from .sync import DirtySyncer


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="dportsv3 dev-env")
    subparsers = parser.add_subparsers(dest="action", metavar="ACTION")

    create = subparsers.add_parser("create", help="Create one throwaway DragonFly chroot dev environment")
    create.add_argument("--name", help="Environment name (default: derived from target/origin)")
    create.add_argument("--target", required=True, help="Compose target, e.g. @2026Q2")
    create.add_argument("--origin", help="Optional selected origin, e.g. editors/vim")
    create.add_argument("--delta-root", help="Host DeltaPorts checkout used to refresh the cache mirror (default: this repo)")
    create.add_argument("--backend", default="chroot", help="Backend name (default: chroot)")
    create.add_argument("--freebsd-branch", help="Override FreeBSD branch (default: derived from target)")
    create.add_argument("--dports-branch", help="Override DPorts branch (default: from config)")
    create.add_argument("--shell", action="store_true", help="Enter the shell after creation succeeds")
    create.add_argument(
        "--allow-dirty",
        action="store_true",
        help="Proceed even if the host DeltaPorts checkout has uncommitted edits (only committed state propagates)",
    )
    create.add_argument(
        "--no-initial-compose",
        action="store_true",
        help="Skip the initial compose at create time; run 'regen' inside the shell",
    )
    create.add_argument(
        "--oracle-profile",
        choices=["off", "local", "ci"],
        default="off",
        help="Oracle profile passed to compose (default: off)",
    )

    shell = subparsers.add_parser("shell", help="Enter one existing environment via chroot")
    shell.add_argument(
        "--refresh",
        action="store_true",
        help="Rewrite rcfile and dsynth.ini; attempt to refresh resolv.conf when writable",
    )
    shell.add_argument("name", help="Environment name")

    destroy = subparsers.add_parser("destroy", help="Unmount and remove one environment")
    destroy.add_argument(
        "--yes",
        action="store_true",
        help="Confirm environment removal without an interactive prompt",
    )
    destroy.add_argument("name", help="Environment name")

    sync_dirty = subparsers.add_parser(
        "sync-dirty",
        help="Sync host unstaged and untracked DeltaPorts changes into one environment",
    )
    sync_dirty.add_argument("name", help="Environment name")

    subparsers.add_parser("list", help="List known environments")
    cleanup = subparsers.add_parser("cleanup-mounts", help="Unmount stale dports-dev mounts under the cache root")
    cleanup.add_argument(
        "--yes",
        action="store_true",
        help="Confirm tear-down of every mount under the cache root (required)",
    )

    exec_ = subparsers.add_parser("exec", help="Run a command inside an environment non-interactively")
    exec_.add_argument("--cwd", default="/work/DeltaPorts", help="Working directory inside the chroot")
    exec_.add_argument("--quiet", action="store_true", help="Suppress INFO mount-prep output")
    exec_.add_argument("name", help="Environment name")
    exec_.add_argument("argv", nargs=argparse.REMAINDER, help="-- CMD [ARGS...] to run inside the env")

    status = subparsers.add_parser("status", help="Print one environment's state as a single JSON line")
    status.add_argument("name", help="Environment name")

    update_ = subparsers.add_parser("update", help="Refresh repo mirrors and fast-forward the env's git checkouts")
    update_.add_argument("--force", action="store_true",
                         help="Pull even when the env's checkouts have uncommitted changes")
    update_.add_argument("name", help="Environment name")

    path_ = subparsers.add_parser("path", help="Print one environment's host-side path")
    path_.add_argument(
        "--writable",
        action="store_true",
        help="Print env_dir/writable (the agent's edit overlay) instead of env_dir",
    )
    path_.add_argument("name", help="Environment name")

    rp = subparsers.add_parser(
        "reset-port",
        help="Step 25g operator escape hatch: reset ports/<origin>/ "
             "to git HEAD in the env's DeltaPorts checkout. "
             "Discards tracked modifications + untracked additions. "
             "Used to clean up after a verify drift refusal or "
             "between agent runs without restarting the env.",
    )
    rp.add_argument("name", help="Environment name")
    rp.add_argument("origin", help="category/portname to reset")
    rp.add_argument(
        "--json", action="store_true",
        help="Emit a single-line JSON result on stdout instead of "
             "a human-readable summary.",
    )

    ab = subparsers.add_parser(
        "apply-and-build",
        help="Substrate primitive: optionally apply a diff to the env's "
             "DeltaPorts overlay, then reapply + dsynth build one origin",
    )
    ab.add_argument("name", help="Environment name")
    ab.add_argument("origin", help="category/portname to build")
    ab.add_argument(
        "--diff", default=None,
        help="Path on host to a unified diff to apply against "
             "the env's DeltaPorts overlay before building "
             "(legacy; prefer --intent-log)",
    )
    ab.add_argument(
        "--intent-log", dest="intent_log", default=None,
        help="Path on host to a Step 25e intent log "
             "(analysis/intent_log.json). Each intent is replayed "
             "via the translator — drift-free, no git apply. "
             "Mutually exclusive with --diff.",
    )
    ab.add_argument(
        "--json", action="store_true",
        help="Emit a single-line JSON result on stdout "
             "(ok, apply_exit, reapply_exit, dsynth_exit, log_path, "
             "applied_diff_sha256). Without --json, prints a one-line "
             "human-readable summary.",
    )

    # ----- hooks: install/uninstall/status the dsynth hooks inside an env -----
    hi = subparsers.add_parser(
        "hooks-install",
        help="Install dsynth hooks into the env's writable etc/dsynth",
    )
    hi.add_argument("name", help="Environment name")
    hi.add_argument(
        "--source",
        help="Override source dir (default: scripts/dsynth-hooks/ in the repo)",
    )
    hi.add_argument(
        "--force",
        action="store_true",
        help="Overwrite an existing dportsv3-hooks.conf",
    )

    hu = subparsers.add_parser(
        "hooks-uninstall",
        help="Remove dsynth hooks installed by dports-dev-env",
    )
    hu.add_argument("name", help="Environment name")
    hu.add_argument(
        "--purge",
        action="store_true",
        help="Also remove dportsv3-hooks.conf",
    )

    hs = subparsers.add_parser(
        "hooks-status",
        help="Report whether hooks are installed in the env, and if any are stale",
    )
    hs.add_argument("name", help="Environment name")
    hs.add_argument(
        "--source",
        help="Override source dir for staleness comparison",
    )

    health = subparsers.add_parser(
        "health",
        help="Probe env health (python_runtime, writable_overlay, dports_compose); "
             "exits 0=ready, 1=broken, 2=degraded",
    )
    health.add_argument("name", help="Environment name")
    health.add_argument(
        "--only",
        action="append",
        default=None,
        help="Run only this check (repeatable). Names: python_runtime, "
             "writable_overlay, dports_compose.",
    )
    health.add_argument(
        "--no-indent",
        action="store_true",
        help="Compact JSON output (single line).",
    )

    return parser


def cmd_list(_args: argparse.Namespace) -> int:
    require_root()
    config = load_config()
    validate_cache_root(config.cache_root)
    store = EnvironmentStore(config)
    for env_dir, env_info in store.list_infos():
        mount_status = "mounted" if mounts_under(env_dir / "root") else "unmounted"
        print(f"{env_info.name}\t{env_info.backend}\t{env_info.target}\t{env_info.origin}\t{mount_status}\t{env_info.status}")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    import json
    import subprocess
    require_root()
    config = load_config()
    validate_cache_root(config.cache_root)
    store = EnvironmentStore(config)
    state = store.load(args.name)
    env_dir = store.env_dir(args.name)
    root_mounted = bool(mounts_under(state.root_dir))
    writable = store.writable_dir(args.name)

    # Per-repo git status (branch + short HEAD) for repos that live in
    # the env's writable overlay. Best-effort: missing or broken repos
    # are reported as null.
    def _git_info(repo_rel: str) -> dict | None:
        repo = writable / repo_rel
        if not (repo / ".git").exists():
            return None
        def _run(*args: str) -> str:
            r = subprocess.run(
                ["git", "-C", str(repo)] + list(args),
                text=True, capture_output=True,
            )
            return r.stdout.strip() if r.returncode == 0 else ""
        return {
            "branch": _run("rev-parse", "--abbrev-ref", "HEAD"),
            "commit": _run("rev-parse", "--short=12", "HEAD"),
            "dirty": bool(_run("status", "--porcelain")),
        }

    print(json.dumps({
        "name": state.name,
        "target": state.target,
        "origin": state.origin,
        "status": state.status,
        "backend": state.backend,
        "oracle_profile": state.oracle_profile,
        "root_mounted": root_mounted,
        "env_dir": str(env_dir),
        "deltaports": _git_info("work/DeltaPorts"),
        "freebsd_ports": _git_info("work/freebsd-ports"),
    }))
    return 0


def cmd_update(args: argparse.Namespace) -> int:
    require_root()
    config = load_config()
    validate_cache_root(config.cache_root)
    store = EnvironmentStore(config)
    from .update import update_env
    update_env(config, store, args.name, force=args.force)
    return 0


def cmd_path(args: argparse.Namespace) -> int:
    require_root()
    config = load_config()
    validate_cache_root(config.cache_root)
    store = EnvironmentStore(config)
    if not store.env_dir(args.name).is_dir():
        raise UsageError(f"environment not found: {args.name}")
    target = store.writable_dir(args.name) if args.writable else store.env_dir(args.name)
    print(str(target))
    return 0


def apply_and_build(
    env_name: str,
    origin: str,
    *,
    diff_path: str | None = None,
    intent_log_path: str | None = None,
) -> dict:
    """Substrate primitive for fix verification (Step 11b Slice 1 +
    Step 25e intent-log replay).

    In-process function — the public callable that the verify-fix
    orchestrator and any future in-process consumer use directly,
    without subprocess-out-and-back-in. The CLI thin-wrapper
    ``cmd_apply_and_build`` just unwraps argparse.Namespace and
    prints; the real work lives here.

    Two replay modes, mutually exclusive:

    - ``diff_path``: legacy. Applies the unified diff with
      ``git apply --3way`` (which has known issues with new-file
      diffs against drifted envs — see Step 25e bandages-retired).
    - ``intent_log_path``: Step 25e. Loads the
      ``analysis/intent_log.json`` shape, replays each intent via
      the in-process Translator, drift-free. Preferred when a
      bundle has both artifacts.

    Then runs ``reapply ORIGIN`` to re-materialize the DPorts tree
    and ``dbuild ORIGIN`` (dsynth). Captures combined dsynth output
    to a log file under writable.

    Returns a dict with: ok, env, origin, applied_diff_sha256
    (legacy mode) or intents_applied (intent mode), apply_exit,
    reapply_exit, dsynth_exit, log_path, stderr_tail, replay_mode.
    """
    import hashlib
    import json
    import shlex

    from .chroot import ChrootRunner, chroot_env
    from .helpers import build_env_dict

    require_root()
    config = load_config()
    validate_cache_root(config.cache_root)
    store = EnvironmentStore(config)
    session = EnvironmentSession(config, store)
    state = session.prepare(env_name)

    env_dir = store.env_dir(env_name)
    writable_root = env_dir / "writable"
    runner = ChrootRunner(state.root_dir)
    env = chroot_env() | build_env_dict(state)

    result: dict = {
        "ok": False,
        "env": env_name,
        "origin": origin,
        "applied_diff_sha256": None,
        "apply_exit": None,
        "reapply_exit": None,
        "dsynth_exit": None,
        "log_path": None,
        "stderr_tail": None,
        "replay_mode": None,
        "intents_applied": None,
    }

    if diff_path is not None and intent_log_path is not None:
        raise UsageError(
            "apply-and-build: pass either --diff OR --intent-log, not both"
        )

    # Intent-log replay (Step 25e). Takes precedence — drift-free,
    # validator-protected, no git apply at all. The translator
    # operates in-process against the env's writable DeltaPorts
    # overlay (no chroot exec needed; the writable layer is the
    # same physical filesystem the chroot sees).
    if intent_log_path is not None:
        intent_host = Path(intent_log_path).expanduser().resolve()
        if not intent_host.is_file():
            raise UsageError(
                f"--intent-log: file not found: {intent_host}"
            )
        result["replay_mode"] = "intent_log"
        workspace = writable_root / "work" / "DeltaPorts"

        # Step 25g pre-replay assertion: refuse if the port subtree
        # has uncommitted changes. Replaying intents against a dirty
        # working tree produces undefined results; better to surface
        # the conflict than silently merge. Operator escape hatch:
        # `dportsv3 dev-env reset-port ENV ORIGIN` or `git stash`.
        dirty = _port_dirty_paths(workspace, origin)
        if dirty:
            tail = (f"intent-log replay refused: ports/{origin}/ has "
                    f"uncommitted changes:\n  "
                    + "\n  ".join(dirty)
                    + "\nrun `dportsv3 dev-env reset-port ENV ORIGIN` "
                    "or `git stash` first")
            result["apply_exit"] = 1
            result["stderr_tail"] = tail[-2000:]
            result["dirty_paths"] = dirty
            sys.stderr.write(tail + "\n")
            return result

        rc, applied_count, err = _replay_intent_log(
            intent_host, workspace, origin,
        )
        result["apply_exit"] = rc
        result["intents_applied"] = applied_count
        if rc != 0:
            result["stderr_tail"] = (err or "")[-2000:]
            sys.stderr.write(err or "")
            return result
    else:
        result["replay_mode"] = "diff" if diff_path is not None else "none"

    # 1. Apply diff (optional, legacy path). Run through the chroot
    #    so the substrate sees its own filesystem — host and chroot
    #    share the physical writable layer, but going through
    #    `chroot exec` keeps the operator memory rule honest
    #    (no host-side tree IO).
    if diff_path is not None:
        diff_host = Path(diff_path).expanduser().resolve()
        if not diff_host.is_file():
            raise UsageError(f"--diff: file not found: {diff_host}")
        diff_bytes = diff_host.read_bytes()
        result["applied_diff_sha256"] = hashlib.sha256(diff_bytes).hexdigest()

        staged_host = writable_root / "work" / ".apply-and-build.diff"
        staged_host.parent.mkdir(parents=True, exist_ok=True)
        staged_host.write_bytes(diff_bytes)
        diff_chroot_path = "/work/.apply-and-build.diff"
        try:
            apply_proc = runner.run(
                ["/bin/sh", "-c",
                 f"cd /work/DeltaPorts && git apply --3way "
                 f"{shlex.quote(diff_chroot_path)}", "_"],
                env=env, capture_output=True,
            )
            result["apply_exit"] = apply_proc.returncode
            if apply_proc.returncode != 0:
                tail = (apply_proc.stderr or "") + (apply_proc.stdout or "")
                result["stderr_tail"] = tail[-2000:]
                sys.stderr.write(tail)
                return result
        finally:
            try:
                staged_host.unlink()
            except FileNotFoundError:
                pass

    # 2. reapply ORIGIN — re-materialize DPorts from the (possibly-
    #    edited) DeltaPorts source.
    reapply_proc = runner.run(
        ["/bin/sh", "-c", f"cd /work/DeltaPorts && reapply "
                          f"{shlex.quote(origin)}", "_"],
        env=env, capture_output=True,
    )
    result["reapply_exit"] = reapply_proc.returncode
    if reapply_proc.returncode != 0:
        tail = (reapply_proc.stderr or "") + (reapply_proc.stdout or "")
        result["stderr_tail"] = tail[-2000:]
        sys.stderr.write(tail)
        return result

    # 3. dbuild ORIGIN — runs dsynth. Capture combined output to a
    #    log file under writable so the orchestrator can POST it.
    log_rel = f"work/artifacts/apply-and-build-{origin.replace('/', '_')}.log"
    log_host = writable_root / log_rel
    log_host.parent.mkdir(parents=True, exist_ok=True)
    log_chroot = f"/{log_rel}"
    build_proc = runner.run(
        ["/bin/sh", "-c",
         f"cd /work/DeltaPorts && dbuild {shlex.quote(origin)} "
         f"> {shlex.quote(log_chroot)} 2>&1", "_"],
        env=env, capture_output=False,
    )
    result["dsynth_exit"] = build_proc.returncode
    result["log_path"] = str(log_host)
    result["ok"] = (build_proc.returncode == 0)

    # Step 25g post-build cleanup: for intent-log mode, leave the
    # env exactly as we found it. Replay modified ports/<origin>/;
    # the build is done (success or fail) so the substrate state
    # is no longer needed. Resetting now means the next verify run
    # starts from a clean baseline without operator intervention.
    # The diff path is unchanged for backward compat — pre-25e
    # bundles rely on the legacy "leave drift in place" behavior.
    if intent_log_path is not None:
        rel = f"ports/{origin}"
        cleanup = runner.run(
            ["/bin/sh", "-c",
             f"cd /work/DeltaPorts && "
             f"git checkout HEAD -- {shlex.quote(rel)} && "
             f"git clean -fd -- {shlex.quote(rel)}", "_"],
            env=env, capture_output=True,
        )
        if cleanup.returncode != 0:
            # Non-fatal: build result stands. Surface in stderr_tail
            # so the operator knows the env may have leftover state.
            warn = (
                f"\n[25g post-build cleanup failed: rc={cleanup.returncode}; "
                f"env's {rel} may have leftover state]\n"
                + (cleanup.stderr or "")[-512:]
            )
            existing = result.get("stderr_tail") or ""
            result["stderr_tail"] = (existing + warn)[-2000:]

    return result


def _port_dirty_paths(workspace: Path, origin: str) -> list[str]:
    """Return the porcelain dirty-paths for ports/<origin>/ in
    ``workspace`` (host-side; the workspace is the shared writable
    layer).

    Empty list = clean. Used by apply_and_build's pre-replay check
    and by the dev-env reset-port CLI's "is there anything to reset"
    sanity message.
    """
    import subprocess as _sp
    rel = f"ports/{origin}"
    try:
        p = _sp.run(
            ["git", "-C", str(workspace),
             "status", "--porcelain", "--", rel],
            capture_output=True, text=True, check=False,
        )
    except Exception:
        return []
    if p.returncode != 0:
        return []
    out: list[str] = []
    for line in (p.stdout or "").splitlines():
        s = line.lstrip()
        if " " in s:
            _, _, rest = s.partition(" ")
            path = rest.strip()
            if "->" in path:
                path = path.split("->", 1)[1].strip()
            out.append(path)
        elif s:
            out.append(s)
    return out


def _git_head(workspace: Path) -> str:
    """Return ``git -C <workspace> rev-parse HEAD`` or ''."""
    import subprocess as _sp
    try:
        p = _sp.run(
            ["git", "-C", str(workspace), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=False,
        )
        if p.returncode == 0:
            return (p.stdout or "").strip()
    except Exception:
        pass
    return ""


def _replay_intent_log(
    intent_log_path: Path, workspace: Path, origin: str,
) -> tuple[int, int, str]:
    """Replay an intent log against a workspace (Step 25e).

    Loads ``analysis/intent_log.json``, walks its intents in order,
    and applies each via the dev-env's in-process Translator. The
    translator's mode is resolved from the log's
    ``mode_at_apply`` field — the assumption is the operator-
    chosen verify env is at the same git HEAD as the original
    apply baseline.

    Returns (rc, applied_count, stderr_blob). rc=0 means every
    intent applied cleanly. The first failure short-circuits and
    returns its error in stderr_blob.

    Implementation note: this function imports the
    ``dportsv3.agent.edit_intent`` package directly. Adding it to
    sys.path is the caller's responsibility — in production, the
    runner already has the generator package importable; in
    tests, the test harness arranges path.
    """
    import json
    import sys as _sys
    from pathlib import Path as _Path

    # Add scripts/generator to sys.path so we can import the
    # edit-intent library. The dev-env tools and the generator
    # share a repo root; locate it relative to this file.
    here = _Path(__file__).resolve()
    candidates = [
        here.parents[3] / "generator",  # scripts/generator
    ]
    for cand in candidates:
        if cand.is_dir() and str(cand) not in _sys.path:
            _sys.path.insert(0, str(cand))

    try:
        from dportsv3.agent.edit_intent import (  # noqa: PLC0415
            IntentError, Translator, parse_intent,
        )
    except ImportError as exc:
        return (1, 0,
                f"intent-log replay requires dportsv3.agent.edit_intent: "
                f"{exc}")

    try:
        doc = json.loads(intent_log_path.read_text())
    except Exception as exc:
        return (1, 0, f"intent log JSON parse failed: {exc}")

    if doc.get("origin") != origin:
        return (1, 0,
                f"intent log origin {doc.get('origin')!r} does not match "
                f"requested origin {origin!r}")

    # Design §8 step 2: assert baseline_commit matches the env's
    # git HEAD before replay. Mismatch means the env has drifted
    # from the agent's apply baseline; replay's results would be
    # against a different starting state. Refuse rather than
    # silently produce a verdict the operator can't trust.
    baseline = doc.get("baseline_commit") or ""
    head = _git_head(workspace)
    if baseline and head and baseline != head:
        return (1, 0,
                f"intent log baseline_commit {baseline[:12]} does not "
                f"match env HEAD {head[:12]}; refusing replay to avoid "
                f"drift. Update the env (dportsv3 dev-env update) so "
                f"its DeltaPorts checkout matches the agent's baseline.")
    # Empty baseline (older logs or git resolution failure during
    # apply) is allowed through — the operator opted-in by triggering
    # verify against this bundle. Logged for forensics.
    if not baseline:
        sys.stderr.write(
            "intent log has no baseline_commit; replay will proceed "
            "but drift cannot be detected\n"
        )

    mode = doc.get("mode_at_apply", "compat")
    if mode not in ("compat", "dops", "convert"):
        return (1, 0, f"unknown mode_at_apply: {mode!r}")

    translator = Translator(workspace, origin, mode)
    applied = 0
    entries = doc.get("intents") or []
    for entry in entries:
        intent_dict = entry.get("intent") or entry  # backward-compat
        # Skip ok=False entries from the original run — they did
        # nothing then, replaying them would emit phantom failures.
        if entry.get("ok") is False:
            continue
        try:
            result = translator.apply(intent_dict)
        except IntentError as exc:
            return (1, applied, f"intent[{applied}] validation: {exc}")
        if not result.ok:
            return (1, applied,
                    f"intent[{applied}] ({result.intent_type}) failed: "
                    f"{result.error}")
        applied += 1
    return (0, applied, "")


def cmd_reset_port(args: argparse.Namespace) -> int:
    """CLI for ``dportsv3 dev-env reset-port`` (Step 25g).

    Mirrors the post-build cleanup from apply_and_build: runs
    ``git checkout HEAD -- ports/<origin>`` + ``git clean -fd``
    inside the chroot. Operator escape hatch when verify refused
    due to drift, or between runs without restarting the env.
    """
    import json
    import shlex as _shlex

    from .chroot import ChrootRunner, chroot_env
    from .helpers import build_env_dict

    require_root()
    config = load_config()
    validate_cache_root(config.cache_root)
    store = EnvironmentStore(config)
    session = EnvironmentSession(config, store)
    state = session.prepare(args.name)

    env_dir = store.env_dir(args.name)
    writable_root = env_dir / "writable"
    workspace = writable_root / "work" / "DeltaPorts"
    runner = ChrootRunner(state.root_dir)
    env = chroot_env() | build_env_dict(state)

    # Report what would be reset BEFORE doing it so the operator
    # can see the scope.
    dirty_before = _port_dirty_paths(workspace, args.origin)

    rel = f"ports/{args.origin}"
    p = runner.run(
        ["/bin/sh", "-c",
         f"cd /work/DeltaPorts && "
         f"git checkout HEAD -- {_shlex.quote(rel)} && "
         f"git clean -fd -- {_shlex.quote(rel)}", "_"],
        env=env, capture_output=True,
    )
    result = {
        "ok": p.returncode == 0,
        "env": args.name,
        "origin": args.origin,
        "rc": p.returncode,
        "paths_reset": dirty_before,
    }
    if p.returncode != 0:
        result["stderr_tail"] = (p.stderr or "")[-2000:]
        sys.stderr.write(p.stderr or "")
    if args.json:
        print(json.dumps(result))
    else:
        if not dirty_before:
            print(f"ok=True env={args.name} origin={args.origin} "
                  "(nothing to reset)")
        else:
            print(f"ok={result['ok']} env={args.name} "
                  f"origin={args.origin} reset={len(dirty_before)} paths")
    return 0 if result["ok"] else 1


def cmd_apply_and_build(args: argparse.Namespace) -> int:
    """CLI wrapper for :func:`apply_and_build`. Higher-level
    orchestrators (e.g. ``dportsv3.verify_fix.run_verify_fix``)
    call the function directly — no subprocess hop.
    """
    import json

    result = apply_and_build(
        args.name, args.origin,
        diff_path=args.diff,
        intent_log_path=getattr(args, "intent_log", None),
    )
    if args.json:
        # Include stderr_tail when a stage failed — the verify-fix
        # orchestrator reads it to populate the activity log so the
        # operator sees *why* apply or reapply died without opening
        # the build log.
        print(json.dumps(result))
    else:
        parts = [f"ok={result['ok']}",
                 f"apply={result['apply_exit']}",
                 f"reapply={result['reapply_exit']}",
                 f"dsynth={result['dsynth_exit']}"]
        if result["log_path"]:
            parts.append(f"log={result['log_path']}")
        print(" ".join(parts))
    return 0 if result["ok"] else 1


def cmd_cleanup_mounts(args: argparse.Namespace) -> int:
    require_root()
    config = load_config()
    validate_cache_root(config.cache_root)
    store = EnvironmentStore(config)

    targets = ordered_mounts_under(config.cache_root)
    if not targets:
        info(f"no dports-dev mounts under {config.cache_root}")
        return 0

    creating = [env_info.name for _, env_info in store.list_infos() if env_info.status == "creating"]
    if creating:
        error(f"refusing to clean mounts; create is in progress for: {', '.join(creating)}")
        error("wait for it to finish or destroy the partial environment first")
        return 1

    info(f"the following mounts under {config.cache_root} will be unmounted:")
    for mount in targets:
        print(str(mount.target), file=sys.stderr)

    if not args.yes:
        error("re-run with --yes to confirm tearing down the listed mounts")
        return 1

    unmount_under(config.cache_root)
    survivors = mounts_under(config.cache_root)
    if survivors:
        error("some dports-dev mounts remain:")
        for mount in survivors:
            print(str(mount.target), file=sys.stderr)
        error("if these paths no longer exist, a reboot may be required to clear orphaned mounts")
        return 1
    info(f"all dports-dev mounts under {config.cache_root} have been released")
    return 0


def cmd_destroy(args: argparse.Namespace) -> int:
    require_root()
    config = load_config()
    validate_cache_root(config.cache_root)
    store = EnvironmentStore(config)
    env_dir = store.env_dir(args.name)
    if not env_dir.is_dir():
        raise UsageError(f"environment not found: {args.name}")

    env_name = args.name

    try:
        state = store.load(args.name)
        env_name = state.name
    except DevEnvError:
        warn(f"environment {args.name} has no valid env.json; cleaning partial environment")

    if not args.yes:
        answer = input(f"Destroy environment {env_name}? [y/N] ")
        if answer.strip().lower() not in {"y", "yes"}:
            info("destroy cancelled")
            return 1

    info(f"destroying environment {env_name}")
    unmount_under(env_dir)
    survivors = mounts_under(env_dir)
    if survivors:
        error(f"refusing to remove {env_dir}; the following mounts are still present:")
        for mount in survivors:
            print(str(mount.target), file=sys.stderr)
        raise UsageError("unmount the listed paths and re-run destroy")
    safe_remove_tree(config, env_dir)
    info(f"destroyed environment {env_name}")
    return 0


def cmd_sync_dirty(args: argparse.Namespace) -> int:
    require_root()
    config = load_config()
    validate_cache_root(config.cache_root)
    store = EnvironmentStore(config)
    DirtySyncer(config, store).sync(args.name)
    return 0


def cmd_shell(args: argparse.Namespace) -> int:
    require_root()
    config = load_config()
    validate_cache_root(config.cache_root)
    store = EnvironmentStore(config)
    EnvironmentSession(config, store).enter(args.name, refresh=args.refresh)
    return 0


def cmd_exec(args: argparse.Namespace) -> int:
    import os
    require_root()
    if args.quiet:
        os.environ["DPORTS_DEV_ENV_QUIET"] = "1"
    config = load_config()
    validate_cache_root(config.cache_root)
    store = EnvironmentStore(config)
    argv = list(args.argv)
    if argv and argv[0] == "--":
        argv = argv[1:]
    if not argv:
        raise UsageError("dev-env exec requires a command after '--'")
    session = EnvironmentSession(config, store)
    state = session.prepare(args.name)
    return session.exec_command(state, argv, cwd=args.cwd)


def cmd_create(args: argparse.Namespace) -> int:
    require_root()
    config = load_config()
    store = EnvironmentStore(config)
    options = CreateOptions(
        name=args.name,
        target=args.target,
        origin=args.origin,
        delta_root=Path(args.delta_root) if args.delta_root else default_delta_root(),
        backend=args.backend,
        freebsd_branch=args.freebsd_branch,
        dports_branch=args.dports_branch or config.dports_branch,
        allow_dirty=args.allow_dirty,
        no_initial_compose=args.no_initial_compose,
        oracle_profile=args.oracle_profile,
    )
    builder = EnvironmentBuilder(config, store, options)

    # Per-invocation log file. Lives outside env_dir so it survives env
    # destruction; one file per attempt so retries don't clobber.
    ts = time.strftime("%Y%m%d-%H%M%SZ", time.gmtime())
    log_path = config.cache_root / ".logs" / "dev-env" / f"create-{builder.env_name}-{ts}.log"
    to_user(f"==> creating dev-env {builder.env_name} (log: {log_path})")
    started = time.monotonic()

    with run_log_context(log_path):
        result = builder.create()

    elapsed = int(time.monotonic() - started)
    if result.exit_code == 0:
        to_user(f"==> created {result.env_name} in {elapsed}s")
        to_user(f"    log: {log_path}")
    else:
        to_user(f"==> create FAILED for {result.env_name} after {elapsed}s "
                f"(exit {result.exit_code})")
        to_user(f"    log: {log_path}")
        to_user(f"    tail it: tail -n 80 {log_path}")

    if args.shell:
        try:
            state = store.load(result.env_name)
        except DevEnvError:
            state = None
        if state is not None and state.status == "ready":
            EnvironmentSession(config, store).enter(result.env_name)
        else:
            warn("not entering shell because create did not leave a ready environment")
    return result.exit_code


def _hooks_resolve_state(args: argparse.Namespace):
    require_root()
    config = load_config()
    validate_cache_root(config.cache_root)
    store = EnvironmentStore(config)
    if not store.env_dir(args.name).is_dir():
        raise UsageError(f"environment not found: {args.name}")
    return store.load(args.name)


def cmd_hooks_install(args: argparse.Namespace) -> int:
    from .hooks import cmd_hooks_install as _impl

    return _impl(args, _hooks_resolve_state(args))


def cmd_hooks_uninstall(args: argparse.Namespace) -> int:
    from .hooks import cmd_hooks_uninstall as _impl

    return _impl(args, _hooks_resolve_state(args))


def cmd_hooks_status(args: argparse.Namespace) -> int:
    from .hooks import cmd_hooks_status as _impl

    return _impl(args, _hooks_resolve_state(args))


def cmd_health(args: argparse.Namespace) -> int:
    """Run dportsv3.agent.health.check(env) and emit JSON.

    Exit codes:
        0 = ready (all checks ok)
        1 = broken (at least one check broken)
        2 = degraded (warn but no broken)

    The agent package lives in scripts/generator/dportsv3/ — outside
    the dev-env venv's site-packages — so we add it to sys.path
    on demand. The health probe itself only uses stdlib + lazy
    imports of dportsv3.agent.worker.
    """
    import json

    generator_dir = Path(__file__).resolve().parents[3] / "generator"
    if generator_dir.is_dir() and str(generator_dir) not in sys.path:
        sys.path.insert(0, str(generator_dir))
    try:
        from dportsv3.agent import health
    except ImportError as exc:
        error(f"could not import dportsv3.agent.health (looked in {generator_dir}): {exc}")
        return 1

    eh = health.check(args.name, only=args.only)
    indent = None if args.no_indent else 2
    print(json.dumps(eh.to_dict(), indent=indent))
    if eh.status == "ready":
        return 0
    if eh.status == "broken":
        return 1
    return 2  # degraded


def dispatch(args: argparse.Namespace) -> int:
    if args.action is None:
        build_parser().print_help()
        return 0
    commands = {
        "create": cmd_create,
        "shell": cmd_shell,
        "exec": cmd_exec,
        "destroy": cmd_destroy,
        "sync-dirty": cmd_sync_dirty,
        "list": cmd_list,
        "cleanup-mounts": cmd_cleanup_mounts,
        "status": cmd_status,
        "update": cmd_update,
        "path": cmd_path,
        "apply-and-build": cmd_apply_and_build,
        "reset-port": cmd_reset_port,
        "hooks-install": cmd_hooks_install,
        "hooks-uninstall": cmd_hooks_uninstall,
        "hooks-status": cmd_hooks_status,
        "health": cmd_health,
    }
    return commands[args.action](args)


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    try:
        args = parser.parse_args(sys.argv[1:] if argv is None else argv)
        raise SystemExit(dispatch(args))
    except DevEnvError as exc:
        error(str(exc))
        raise SystemExit(1) from None
    except KeyboardInterrupt:
        error("interrupted")
        raise SystemExit(130) from None
