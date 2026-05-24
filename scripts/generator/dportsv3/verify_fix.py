"""Top-level fix-verification orchestrator (plan Step 11b Slice 3).

Glues the substrate primitive (``dportsv3 dev-env apply-and-build``,
Slice 1) to the tracker endpoint (``POST /api/bundles/<id>/verification``,
Slice 2). Lives outside ``dportsv3.agent`` on purpose: this command is
operator-facing, not part of the agentic loop.

Flow::

    dportsv3 verify-fix BUNDLE_ID --env ENV [--keep-log]
        |
        v
    GET /api/bundles/<bundle_id>           -> origin, target
    GET /api/bundles/<id>/artifacts/...    -> changes.diff (tmpfile)
        |
        v
    dportsv3 dev-env apply-and-build ENV ORIGIN --diff TMP --json
        |
        v
    POST /api/bundles/<id>/verification    {ok, applied_diff_sha256, ...}

Auto-provisioning a fresh dev-env is intentionally deferred to a
follow-up: today the operator picks a clean env explicitly with
``--env``. The reason is testability — provisioning requires root +
mounts + a DragonFly host, none of which CI has. With ``--env`` we
can drive the orchestrator end-to-end against a fake substrate.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path


DEFAULT_TRACKER_URL = "http://127.0.0.1:8080"
DIFF_RELPATH = "analysis/changes.diff"


def _tracker_url() -> str:
    return os.environ.get("DPORTSV3_TRACKER_URL", DEFAULT_TRACKER_URL).rstrip("/")


def _get_json(url: str, timeout: int = 10) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.load(resp)


def _get_bytes(url: str, timeout: int = 20) -> bytes:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return resp.read()


def _post_json(url: str, body: dict, timeout: int = 10) -> dict:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={"Content-Type": "application/json",
                 "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


@dataclass
class VerifyResult:
    bundle_id: str
    env: str
    origin: str
    ok: bool
    apply_exit: int | None
    reapply_exit: int | None
    dsynth_exit: int | None
    applied_diff_sha256: str | None
    log_path: str | None
    posted: bool


def run_verify_fix(
    *,
    bundle_id: str,
    env: str,
    tracker_url: str | None = None,
    apply_and_build: list[str] | None = None,
    keep_log: bool = False,
    # Injectable hooks for tests; production callers don't pass these.
    _get_json=_get_json,
    _get_bytes=_get_bytes,
    _post_json=_post_json,
    _run=subprocess.run,
) -> VerifyResult:
    """Run the orchestrator end-to-end.

    ``apply_and_build`` is the argv prefix used to invoke the Slice 1
    primitive; defaults to ``["dportsv3", "dev-env", "apply-and-build"]``
    but tests can swap in a stub.
    """
    base = (tracker_url or _tracker_url()).rstrip("/")
    bundle_url = f"{base}/api/bundles/{urllib.parse.quote(bundle_id)}"

    bundle = _get_json(bundle_url)
    origin = bundle.get("origin")
    if not origin:
        raise SystemExit(
            f"bundle {bundle_id!r} has no origin field; cannot verify"
        )

    diff_url = (
        f"{base}/api/bundles/{urllib.parse.quote(bundle_id)}"
        f"/artifacts/{urllib.parse.quote(DIFF_RELPATH, safe='/')}"
    )
    try:
        diff_bytes = _get_bytes(diff_url)
    except urllib.error.HTTPError as exc:
        raise SystemExit(
            f"bundle {bundle_id!r} has no {DIFF_RELPATH} artifact "
            f"({exc.code}); cannot verify"
        )
    if not diff_bytes.strip():
        raise SystemExit(
            f"bundle {bundle_id!r}'s {DIFF_RELPATH} is empty; "
            "nothing to verify"
        )

    diff_sha = hashlib.sha256(diff_bytes).hexdigest()

    argv_prefix = apply_and_build or ["dportsv3", "dev-env", "apply-and-build"]
    with tempfile.NamedTemporaryFile(
        mode="wb", suffix=".diff", prefix=f"verify-{bundle_id}-",
        delete=False,
    ) as tmp:
        tmp.write(diff_bytes)
        diff_path = tmp.name
    try:
        proc = _run(
            [*argv_prefix, env, origin, "--diff", diff_path, "--json"],
            capture_output=True, text=True, check=False,
        )
    finally:
        try:
            os.unlink(diff_path)
        except FileNotFoundError:
            pass

    if proc.stderr:
        sys.stderr.write(proc.stderr)
    try:
        ab = json.loads((proc.stdout or "").strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        raise SystemExit(
            f"apply-and-build produced no JSON on stdout (rc={proc.returncode})"
        )

    ok = bool(ab.get("ok"))
    post_body = {
        "ok": ok,
        "applied_diff_sha256": ab.get("applied_diff_sha256") or diff_sha,
        "dsynth_exit": ab.get("dsynth_exit"),
    }
    posted = False
    try:
        _post_json(f"{base}/api/bundles/{urllib.parse.quote(bundle_id)}"
                   "/verification", post_body)
        posted = True
    except Exception as exc:
        sys.stderr.write(
            f"warning: failed to POST verification result: {exc}\n"
        )

    log_path = ab.get("log_path")
    if log_path and not keep_log:
        # The operator usually only cares about the log on failure.
        # On success it's cheap to drop. --keep-log overrides.
        if ok:
            try:
                Path(log_path).unlink()
                log_path = None
            except FileNotFoundError:
                pass

    return VerifyResult(
        bundle_id=bundle_id,
        env=env,
        origin=origin,
        ok=ok,
        apply_exit=ab.get("apply_exit"),
        reapply_exit=ab.get("reapply_exit"),
        dsynth_exit=ab.get("dsynth_exit"),
        applied_diff_sha256=ab.get("applied_diff_sha256") or diff_sha,
        log_path=log_path,
        posted=posted,
    )


def cmd_verify_fix(args: argparse.Namespace) -> int:
    """CLI entrypoint. See module docstring."""
    result = run_verify_fix(
        bundle_id=args.bundle_id,
        env=args.env,
        tracker_url=args.tracker_url,
        keep_log=args.keep_log,
    )
    if args.json:
        print(json.dumps(result.__dict__))
    else:
        verdict = "verified" if result.ok else "verification_failed"
        parts = [
            f"bundle={result.bundle_id}",
            f"origin={result.origin}",
            f"env={result.env}",
            f"verdict={verdict}",
            f"dsynth_exit={result.dsynth_exit}",
            f"posted={result.posted}",
        ]
        if result.log_path:
            parts.append(f"log={result.log_path}")
        print(" ".join(parts))
    return 0 if result.ok else 1


def register_parser(subparsers: argparse._SubParsersAction) -> None:
    """Wire ``verify-fix`` into ``dportsv3``'s top-level parser."""
    p = subparsers.add_parser(
        "verify-fix",
        help="Independently verify a bundle's proposed fix by replaying "
             "analysis/changes.diff in a fresh env and POSTing the "
             "result back to the tracker (plan Step 11b Slice 3)",
    )
    p.add_argument("bundle_id", help="Bundle ID to verify")
    p.add_argument(
        "--env", required=True,
        help="Dev-env name to verify in. The env should be clean (no "
             "in-flight agent edits) and target-matched to the bundle.",
    )
    p.add_argument(
        "--tracker-url", default=None,
        help="Tracker base URL. Falls back to $DPORTSV3_TRACKER_URL "
             f"or {DEFAULT_TRACKER_URL!r}.",
    )
    p.add_argument(
        "--keep-log", action="store_true",
        help="Preserve apply-and-build's log even on success. The log "
             "is kept on failure unconditionally.",
    )
    p.add_argument(
        "--json", action="store_true",
        help="Emit a single-line JSON result on stdout instead of "
             "the human-readable summary.",
    )
