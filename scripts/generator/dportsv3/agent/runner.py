"""dportsv3.agent.runner — process dsynth failure jobs via the harness.

This module is the agent-queue-runner's home. ``scripts/agent-queue-runner``
is a thin shim that calls ``main()``. The ``dportsv3 agent-queue-runner``
subcommand calls ``main()`` directly. Tests can import and call internal
helpers.

Usage:
    agent-queue-runner --queue-root <path> [--once] [--dry-run]

Required env vars (one of triage or patch model must be set for the
relevant job type):
    DP_HARNESS_TRIAGE_MODEL   litellm model string for triage
    DP_HARNESS_PATCH_MODEL    litellm model string for patch
    DP_HARNESS_*_API_KEY      provider API keys
    DP_HARNESS_*_API_BASE     optional custom endpoint
    DP_HARNESS_*_PROVIDER     optional custom_llm_provider override
    DP_HARNESS_ENV            dev-env name for patch tool dispatch
    DP_HARNESS_POLICY         optional path to agentic-policy.json
    DP_HARNESS_TIMEOUT        triage timeout (default 120)
    DP_HARNESS_PATCH_TIMEOUT  patch timeout (default 600)
    DP_HARNESS_MAX_SNIPPET_ROUNDS  default 5

Job types:
    type=triage (default) — runs dportsv3.agent.triage; classification +
                            confidence drive trust-tier dispatch.
    type=patch            — runs dportsv3.agent.patch with the resolved tier.

Job fields:
    iteration=N           — current fix iteration (1-based)
    max_iterations=N      — max iterations before giving up (default: 3)
    tier=NAME             — pre-resolved trust tier (set by triage step)
    dev_env=NAME          — dev-env to use (set by triage step or DP_HARNESS_ENV)
    previous_bundle=...   — bundle from previous failed attempt
"""

import argparse
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import urllib.error
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path


# Max fix iterations before giving up on a port
DEFAULT_MAX_ITERATIONS = 3

DEFAULT_ARTIFACT_STORE_URL = "http://127.0.0.1:8788"
DEFAULT_TRACKER_URL = "http://127.0.0.1:8080"

# Default location of config/agentic-policy.json. ``runner.py`` lives
# at scripts/generator/dportsv3/agent/runner.py; walk four parents up
# to reach the repo root, then into config/. Operator can override via
# DP_HARNESS_POLICY.
_DEFAULT_POLICY_PATH = str(
    Path(__file__).resolve().parents[4] / "config" / "agentic-policy.json"
)

# Heartbeat interval (seconds)
HEARTBEAT_INTERVAL = 5

# How long to wait between dsynth-lock polls when an env is busy.
DSYNTH_LOCK_POLL_SECONDS = 30


# =============================================================================
# State DB connection (for activity logging and runner status)
# =============================================================================

_state_db_conn: sqlite3.Connection | None = None
_state_db_lock = threading.Lock()
_heartbeat_stop_event = threading.Event()
_heartbeat_thread: threading.Thread | None = None
_current_job_id: str | None = None
_current_stage: str | None = None

# Cached EnvHealth probe result. The runner gate consults this on
# every poll; a fresh probe is taken when (a) the cached entry is
# older than DP_HARNESS_HEALTH_CACHE_SECONDS, or (b)
# invalidate_health_cache() is called explicitly (e.g. when a tool
# result looks env-suspicious mid-job).
_health_cache: dict[str, tuple[float, object]] = {}  # env → (ts, EnvHealth)


def invalidate_health_cache(env: str | None = None) -> None:
    """Drop the cached health probe for ``env`` (or all envs)."""
    if env is None:
        _health_cache.clear()
    else:
        _health_cache.pop(env, None)


def _cached_health_broken(env: str | None = None) -> bool:
    """True iff the cached probe for ``env`` was broken.

    Read directly by ``PatchAttemptStep.run`` (via the
    ``cached_health_broken`` service binding) to route a
    mid-flight job to ENV_BROKEN when the env is known bad.
    Doesn't trigger a probe — purely reads the cache.

    ``env=None`` preserves the legacy "any env" behavior for callers
    that do not have a scoped env, but step code should pass the current
    job env so one broken chroot does not poison another.
    """
    if env is not None:
        cached = _health_cache.get(env)
        if cached is None:
            return False
        return getattr(cached[1], "status", None) == "broken"
    for _ts, eh in _health_cache.values():
        status = getattr(eh, "status", None)
        if status == "broken":
            return True
    return False


def probe_health_cached(env: str, ttl_seconds: int):
    """Return an EnvHealth for ``env``, using the cache when fresh.

    Re-probes when (a) no cached entry exists, (b) the cached entry
    is older than ``ttl_seconds``, or (c) the cache was invalidated
    by ``invalidate_health_cache``. Module-level so tests + the
    runner gate share one implementation.
    """
    from dportsv3.agent import health as health_mod

    now = time.monotonic()
    cached = _health_cache.get(env)
    if cached is not None and (now - cached[0]) < ttl_seconds:
        return cached[1]
    eh = health_mod.check(env)
    _health_cache[env] = (now, eh)
    record_env_health(eh)
    return eh


def record_env_health(env_health) -> None:
    """Persist the latest EnvHealth snapshot for tracker/UI reads."""
    if _state_db_conn is None or env_health is None:
        return
    env = getattr(env_health, "env", None)
    status = getattr(env_health, "status", None)
    if not env or not status:
        return
    probed_at = getattr(env_health, "probed_at", None)
    operator_action = getattr(env_health, "operator_action", None)
    try:
        detail = env_health.to_dict()
    except Exception:
        detail = {"env": env, "status": status}
    ts = datetime.now(timezone.utc).isoformat()
    try:
        with _state_db_lock:
            _state_db_conn.execute(
                """INSERT INTO env_health_status
                   (env, status, probed_at, operator_action, detail_json, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(env) DO UPDATE SET
                     status=excluded.status,
                     probed_at=excluded.probed_at,
                     operator_action=excluded.operator_action,
                     detail_json=excluded.detail_json,
                     updated_at=excluded.updated_at""",
                (env, status, probed_at, operator_action, json.dumps(detail), ts),
            )
            _state_db_conn.commit()
    except Exception as exc:
        print(f"Warning: Failed to record env health: {exc}", file=sys.stderr)


def _looks_env_suspicious(result: dict) -> bool:
    """Heuristic: does this tool result look like an env-level failure?

    Used purely to force a health re-probe. Setting the gate state
    is still the probe's job — we never infer broken-ness from a
    tool error directly anymore.
    """
    if not isinstance(result, dict) or result.get("ok") is True:
        return False
    stderr = (result.get("stderr_tail") or "").lower()
    for needle in (
        "missing dragonfly packages",
        "pyproject.toml not found",
        "venv setup failed",
        "no such file or directory: /work/deltaports",
    ):
        if needle in stderr:
            return True
    return False


def get_state_db_path(queue_root: Path) -> Path:
    """Get path to state.db used for lifecycle/status writes."""
    env_db = os.environ.get("DPORTSV3_STATE_DB")
    if env_db:
        return Path(env_db)
    # Queue is at <logs>/evidence/queue/, state.db is at <logs>/evidence/state.db
    return queue_root.parent / "state.db"


def init_state_db(queue_root: Path) -> sqlite3.Connection | None:
    """Initialize connection to state.db for activity logging."""
    global _state_db_conn
    
    db_path = get_state_db_path(queue_root)
    
    if not db_path.exists():
        print(
            f"Warning: state.db not found at {db_path}; "
            "runner lifecycle/status writes disabled",
            file=sys.stderr,
        )
        return None
    
    try:
        conn = sqlite3.connect(str(db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        _state_db_conn = conn
        return conn
    except Exception as e:
        print(f"Warning: Could not connect to state.db: {e}", file=sys.stderr)
        return None


def _artifact_store_url() -> str:
    return os.environ.get("ARTIFACT_STORE_URL", DEFAULT_ARTIFACT_STORE_URL)


def _tracker_url() -> str:
    return os.environ.get("DPORTSV3_TRACKER_URL", DEFAULT_TRACKER_URL)


def artifact_store_get(bundle_id: str, relpath: str) -> bytes | None:
    url = f"{_artifact_store_url()}/v1/artifacts/get?bundle_id={urllib.parse.quote(bundle_id)}&relpath={urllib.parse.quote(relpath)}"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            return resp.read()
    except Exception:
        return None


def tracker_artifact_get(bundle_id: str, relpath: str) -> bytes | None:
    url = (
        f"{_tracker_url()}/api/bundles/{urllib.parse.quote(bundle_id)}"
        f"/artifacts/{urllib.parse.quote(relpath, safe='/')}"
    )
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            return resp.read()
    except Exception:
        return None


def artifact_store_put(bundle_id: str, relpath: str, data: bytes, kind: str | None = None) -> bool:
    url = f"{_artifact_store_url()}/v1/artifacts/put"
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/octet-stream",
        "X-Bundle-Id": bundle_id,
        "X-Relpath": relpath,
    }
    if kind:
        headers["X-Kind"] = kind
    try:
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=20):
            return True
    except Exception:
        return False


def bundle_artifact_list(bundle_id: str) -> list[str]:
    url = f"{_tracker_url()}/api/bundles/{urllib.parse.quote(bundle_id)}"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.load(resp)
        return [a.get("relpath") for a in data.get("artifacts", []) if a.get("relpath")]
    except Exception:
        return []


def _apply_transition(
    job_id: str,
    event,  # dportsv3.agent.lifecycle.JobEvent
    *,
    actor: str = "runner",
    detail: dict | None = None,
) -> bool:
    """Run a lifecycle transition under the runner's DB lock.

    Best-effort observability write: if the state DB isn't available or
    the transition is illegal (mid-race), log + continue. The
    filesystem queue is the source of truth for what processes next;
    the typed jobs table is the UI's read model.
    """
    from dportsv3.agent import lifecycle  # type: ignore[import-not-found]

    if _state_db_conn is None:
        return False
    try:
        with _state_db_lock:
            lifecycle.apply(_state_db_conn, job_id, event, actor=actor, detail=detail)
        return True
    except lifecycle.IllegalTransition as exc:
        print(f"Warning: illegal transition {event} on {job_id}: {exc}",
              file=sys.stderr)
        return False
    except Exception as exc:
        print(f"Warning: lifecycle.apply({event}) on {job_id} failed: {exc}",
              file=sys.stderr)
        return False


def _register_new_job(
    job_id: str,
    metadata: dict,
    *,
    actor: str = "runner",
) -> bool:
    """Insert a jobs row + fire the initial HOOK_ENQUEUED event.

    Called by hook-driven enqueue paths (triage + auto-enqueued patch).
    Metadata fields (type, origin, flavor, bundle_dir, created_ts_utc,
    path, target) populate the jobs row; the typed state column is
    set by lifecycle.apply.
    """
    from dportsv3.agent import lifecycle  # type: ignore[import-not-found]

    if _state_db_conn is None:
        return False
    ok = _apply_transition(job_id, lifecycle.JobEvent.HOOK_ENQUEUED,
                           actor=actor, detail=metadata)
    if not ok:
        return False
    # Fill in the metadata columns. lifecycle.apply set jobs.state +
    # jobs.last_transition_at; we add the rest here in a second small
    # transaction.
    try:
        now = datetime.now(timezone.utc).isoformat()
        with _state_db_lock:
            _state_db_conn.execute(
                """UPDATE jobs SET
                       type = COALESCE(?, type),
                       origin = COALESCE(?, origin),
                       flavor = COALESCE(?, flavor),
                       bundle_dir = COALESCE(?, bundle_dir),
                       created_ts_utc = COALESCE(?, created_ts_utc),
                       path = COALESCE(?, path),
                       target = COALESCE(?, target),
                       last_seen_at = ?
                   WHERE job_id = ?""",
                (
                    metadata.get("type"),
                    metadata.get("origin"),
                    metadata.get("flavor"),
                    metadata.get("bundle_dir"),
                    metadata.get("created_ts_utc"),
                    metadata.get("path"),
                    metadata.get("target"),
                    now,
                    job_id,
                ),
            )
            _state_db_conn.commit()
        return True
    except Exception as exc:
        print(f"Warning: jobs metadata update for {job_id} failed: {exc}",
              file=sys.stderr)
        return False


def _materialize_bundle(bundle_id: str, dest: Path) -> int:
    """Download all artifacts for ``bundle_id`` into ``dest``.

    Bundles delivered via the artifact-store HTTP path live in state.db,
    not on the filesystem; the harness triage flow (and snippet-extractor)
    need a real directory. Materialize on demand into a tempdir.

    Returns the number of artifacts written. Skips relpaths the store
    can't return (silently — the caller decides whether 0 is an error).
    """
    relpaths = bundle_artifact_list(bundle_id)
    written = 0
    for rel in relpaths:
        data = artifact_store_get(bundle_id, rel)
        if data is None:
            data = tracker_artifact_get(bundle_id, rel)
        if data is None:
            continue
        out = dest / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(data)
        written += 1
    return written


def _load_port_history(target: str, origin: str, window_hours: int):
    """Thin lock-wrapper over PortHistory.load using the runner DB.

    The decision engine's ``PortHistory.load`` does the SQL; this
    helper just supplies ``_state_db_conn`` under ``_state_db_lock``
    so callers don't have to import sqlite3 or know about the lock.
    """
    from dportsv3.agent.decision import PortHistory

    if _state_db_conn is None or not origin:
        return PortHistory.empty(target=target or "", origin=origin or "")
    with _state_db_lock:
        return PortHistory.load(_state_db_conn, target or "", origin, window_hours)


def port_bundle_history(origin: str) -> list[dict]:
    # Tracker /api/ports/<origin> returns a flat list (vs. legacy
    # state-server's {"origin", "bundles", "jobs"} shape).
    url = f"{_tracker_url()}/api/ports/{urllib.parse.quote(origin)}"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.load(resp)
        return data if isinstance(data, list) else data.get("bundles", [])
    except Exception:
        return []


def read_bundle_text(bundle_dir: Path | None, bundle_id: str | None, relpath: str) -> str | None:
    if bundle_dir:
        path = bundle_dir / relpath
        if path.exists():
            return read_file_if_exists(path)
    if bundle_id:
        data = artifact_store_get(bundle_id, relpath)
        if data is None:
            data = tracker_artifact_get(bundle_id, relpath)
        if data is not None:
            return data.decode("utf-8", errors="replace")
    return None


def bundle_artifact_exists(bundle_dir: Path | None, bundle_id: str | None, relpath: str) -> bool:
    if bundle_dir:
        if (bundle_dir / relpath).exists():
            return True
    if bundle_id:
        return relpath in bundle_artifact_list(bundle_id)
    return False


def run_cmd(cmd: list[str], cwd: Path | None = None) -> str:
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    result = subprocess.run(
        cmd,
        cwd=cwd,
        text=True,
        capture_output=True,
        env=env,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip()
        stdout = result.stdout.strip()
        detail = stderr or stdout or "unknown error"
        raise RuntimeError(f"command failed ({result.returncode}): {' '.join(cmd)}: {detail}")
    return result.stdout



def parse_meta_kv(bundle_dir: Path) -> dict:
    """Parse bundle meta.txt into dict (legacy filesystem mode)."""
    data = {}
    meta_path = bundle_dir / "meta.txt"
    if not meta_path.exists():
        return data
    try:
        for line in meta_path.read_text().splitlines():
            if "=" in line:
                key, _, value = line.partition("=")
                data[key.strip()] = value.strip()
    except OSError:
        pass
    return data


def get_run_profile(run_id: str) -> str:
    if _state_db_conn is None:
        return "unknown"
    try:
        with _state_db_lock:
            row = _state_db_conn.execute(
                "SELECT profile FROM runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        if row and row["profile"]:
            return row["profile"]
    except Exception:
        pass
    return "unknown"


def get_bundle_flavor(bundle_id: str) -> str:
    if _state_db_conn is None:
        return ""
    try:
        with _state_db_lock:
            row = _state_db_conn.execute(
                "SELECT flavor FROM bundles WHERE bundle_id = ?",
                (bundle_id,),
            ).fetchone()
        if row and row["flavor"]:
            return row["flavor"]
    except Exception:
        pass
    return ""


def get_user_context(run_id: str | None, origin: str | None) -> tuple[str | None, int]:
    """Fetch user context for run_id+origin from state.db."""
    if _state_db_conn is None or not run_id or not origin:
        return None, 0
    try:
        with _state_db_lock:
            row = _state_db_conn.execute(
                """SELECT context_text, context_rev FROM user_context
                   WHERE run_id = ? AND origin = ?""",
                (run_id, origin)
            ).fetchone()
        if not row:
            return None, 0
        return row["context_text"], int(row["context_rev"])
    except Exception:
        return None, 0


def upsert_user_context_request(
    queue_root: Path,
    run_id: str,
    origin: str,
    bundle_id: str,
    classification: str,
    confidence: str,
    iteration: int,
    max_iterations: int,
):
    """Record a request for user context in state.db."""
    if _state_db_conn is None:
        return
    now = datetime.now(timezone.utc).isoformat()
    _, context_rev = get_user_context(run_id, origin)
    try:
        with _state_db_lock:
            row = _state_db_conn.execute(
                """SELECT last_context_rev_handled FROM user_context_requests
                   WHERE run_id = ? AND origin = ? AND bundle_id = ?""",
                (run_id, origin, bundle_id)
            ).fetchone()
            if row:
                _state_db_conn.execute(
                    """UPDATE user_context_requests
                       SET confidence = ?, classification = ?, iteration = ?,
                           max_iterations = ?, requested_at = ?, status = 'pending'
                       WHERE run_id = ? AND origin = ? AND bundle_id = ?""",
                    (confidence, classification, iteration, max_iterations, now, run_id, origin, bundle_id)
                )
            else:
                _state_db_conn.execute(
                    """INSERT INTO user_context_requests
                       (run_id, origin, bundle_id, confidence, classification, iteration,
                        max_iterations, requested_at, status, last_context_rev_handled)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)""",
                    (run_id, origin, bundle_id, confidence, classification, iteration, max_iterations, now, context_rev)
                )
            _state_db_conn.commit()
    except Exception as e:
        print(f"Warning: Failed to write user_context_request: {e}", file=sys.stderr)


def find_latest_bundle_id(run_id: str, origin: str) -> str | None:
    """Find latest bundle_id for run_id+origin from state.db."""
    if _state_db_conn is None:
        return None
    try:
        with _state_db_lock:
            row = _state_db_conn.execute(
                """SELECT bundle_id FROM bundles
                   WHERE run_id = ? AND origin = ?
                   ORDER BY ts_utc DESC LIMIT 1""",
                (run_id, origin),
            ).fetchone()
        if row:
            return row["bundle_id"]
    except Exception:
        return None
    return None


def enqueue_triage_job(
    queue_root: Path,
    bundle_id: str,
    run_id: str,
    origin: str,
    profile: str,
    flavor: str,
    iteration: int,
    max_iterations: int,
    previous_bundle: str | None,
    context_rev: int,
) -> Path:
    """Enqueue a triage job for the given bundle."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%SZ")
    origin_safe = origin.replace("/", "_")
    pid = os.getpid()
    job_name = f"{ts}-{profile}-{origin_safe}-{pid}.job"
    pending_dir = queue_root / "pending"
    job_path = pending_dir / job_name

    content = [
        "type=triage",
        f"created_ts_utc={ts}",
        f"profile={profile}",
        f"origin={origin}",
        f"flavor={flavor}",
        f"bundle_id={bundle_id}",
        f"run_id={run_id}",
        f"iteration={iteration}",
        f"max_iterations={max_iterations}",
        f"user_context_rev={context_rev}",
    ]
    if previous_bundle:
        content.append(f"previous_bundle={previous_bundle}")

    tmp_path = job_path.with_suffix(".tmp")
    with open(tmp_path, "w") as f:
        f.write("\n".join(content) + "\n")
    tmp_path.rename(job_path)
    _register_new_job(
        job_path.name,
        metadata={
            "type": "triage",
            "origin": origin,
            "flavor": flavor,
            "created_ts_utc": ts,
            "path": str(job_path),
            "target": os.environ.get("DPORTSV3_TRACKER_TARGET", ""),
        },
    )
    return job_path


def process_user_context_updates(queue_root: Path):
    """Enqueue triage jobs when new user context is provided."""
    if _state_db_conn is None:
        return
    try:
        with _state_db_lock:
            rows = _state_db_conn.execute(
                """SELECT run_id, origin, bundle_id, iteration, max_iterations,
                          last_context_rev_handled
                   FROM user_context_requests WHERE status = 'pending'
                   ORDER BY requested_at ASC"""
            ).fetchall()
        for row in rows:
            run_id = row["run_id"]
            origin = row["origin"]
            last_handled = int(row["last_context_rev_handled"])
            context_text, context_rev = get_user_context(run_id, origin)
            if not context_text or context_rev <= last_handled:
                continue
            latest_bundle_id = find_latest_bundle_id(run_id, origin)
            if not latest_bundle_id:
                continue
            iteration = int(row["iteration"] or 1)
            max_iterations = int(row["max_iterations"] or DEFAULT_MAX_ITERATIONS)
            previous_bundle = row["bundle_id"]
            profile = get_run_profile(run_id)
            flavor = get_bundle_flavor(latest_bundle_id)
            job_path = enqueue_triage_job(
                queue_root, latest_bundle_id, run_id, origin,
                profile, flavor, iteration, max_iterations, previous_bundle, context_rev,
            )
            activity_log(queue_root, "retriage_enqueued",
                        f"Re-running triage for {origin} after user context",
                        job_id=job_path.name,
                        extra={"run_id": run_id, "origin": origin, "context_rev": context_rev})
            with _state_db_lock:
                _state_db_conn.execute(
                    """UPDATE user_context_requests
                       SET last_context_rev_handled = ?, status = 'retriage_enqueued'
                       WHERE run_id = ? AND origin = ? AND bundle_id = ?""",
                    (context_rev, run_id, origin, row["bundle_id"])
                )
                _state_db_conn.commit()
    except Exception as e:
        print(f"Warning: Failed to process user context updates: {e}", file=sys.stderr)


def activity_log(
    queue_root: Path,
    stage: str,
    message: str,
    job_id: str | None = None,
    duration_ms: int | None = None,
    extra: dict | None = None
):
    """
    Log activity to state.db activity_log table.
    Also updates _current_stage for heartbeat.
    Keeps only last 10 entries.
    """
    global _current_stage
    _current_stage = stage
    
    # Also write to runner.log for backwards compatibility
    log(queue_root, "INFO", f"[{stage}] {message}")
    
    if _state_db_conn is None:
        return
    
    ts = datetime.now(timezone.utc).isoformat()
    extra_json = json.dumps(extra) if extra else None
    
    try:
        with _state_db_lock:
            _state_db_conn.execute(
                """INSERT INTO activity_log (ts, job_id, stage, message, duration_ms, extra_json)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (ts, job_id, stage, message, duration_ms, extra_json)
            )
            
            # Prune to keep only last N entries (N comes from
            # DP_ACTIVITY_LOG_MAX, default 5000). The previous cap of
            # 10 made the UI's activity page useless and dropped
            # tool-call traces before the next page-refresh.
            cap = max(50, int(os.environ.get("DP_ACTIVITY_LOG_MAX", "5000")))
            _state_db_conn.execute(
                """DELETE FROM activity_log WHERE id NOT IN (
                     SELECT id FROM activity_log ORDER BY id DESC LIMIT ?
                   )""",
                (cap,),
            )
            
            _state_db_conn.commit()
    except Exception as e:
        print(f"Warning: Failed to write activity log: {e}", file=sys.stderr)


def update_runner_status(
    status: str,
    job_id: str | None = None,
    stage: str | None = None,
    extra: dict | None = None
):
    """Update runner_status table (singleton row)."""
    global _current_job_id, _current_stage
    
    _current_job_id = job_id
    if stage is not None:
        _current_stage = stage
    
    if _state_db_conn is None:
        return
    
    ts = datetime.now(timezone.utc).isoformat()
    extra_json = json.dumps(extra) if extra else None
    
    try:
        with _state_db_lock:
            # Upsert the singleton row
            _state_db_conn.execute(
                """INSERT INTO runner_status (id, status, job_id, current_stage, started_at, updated_at, extra_json)
                   VALUES (1, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                     status = excluded.status,
                     job_id = excluded.job_id,
                     current_stage = excluded.current_stage,
                     started_at = CASE WHEN excluded.job_id != runner_status.job_id THEN excluded.started_at ELSE runner_status.started_at END,
                     updated_at = excluded.updated_at,
                     extra_json = excluded.extra_json""",
                (status, job_id, stage or _current_stage, ts, ts, extra_json)
            )
            _state_db_conn.commit()
    except Exception as e:
        print(f"Warning: Failed to update runner status: {e}", file=sys.stderr)


def _heartbeat_loop():
    """Background thread that updates runner_status.updated_at every 5 seconds."""
    while not _heartbeat_stop_event.is_set():
        if _state_db_conn is not None:
            try:
                ts = datetime.now(timezone.utc).isoformat()
                with _state_db_lock:
                    _state_db_conn.execute(
                        """UPDATE runner_status SET updated_at = ? WHERE id = 1""",
                        (ts,)
                    )
                    _state_db_conn.commit()
            except Exception:
                pass
        
        _heartbeat_stop_event.wait(HEARTBEAT_INTERVAL)


def start_heartbeat():
    """Start the heartbeat thread."""
    global _heartbeat_thread
    
    if _heartbeat_thread is not None:
        return
    
    _heartbeat_stop_event.clear()
    _heartbeat_thread = threading.Thread(target=_heartbeat_loop, daemon=True)
    _heartbeat_thread.start()


def stop_heartbeat():
    """Stop the heartbeat thread."""
    global _heartbeat_thread
    
    _heartbeat_stop_event.set()
    if _heartbeat_thread is not None:
        _heartbeat_thread.join(timeout=2)
        _heartbeat_thread = None


def log(queue_root: Path, level: str, message: str):
    """Log to both stderr and runner.log."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    line = f"{ts} {level:5} {message}"
    print(line, file=sys.stderr)
    try:
        with open(queue_root / "runner.log", "a") as f:
            f.write(line + "\n")
    except OSError:
        pass


def parse_job_file(path: Path) -> dict:
    """Parse key=value job file into dict."""
    data = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if "=" in line:
                key, _, value = line.partition("=")
                data[key] = value
    return data


def read_file_if_exists(path: Path, max_bytes: int = 200_000) -> str | None:
    """Read file contents if it exists, truncate if too large."""
    if not path.exists():
        return None
    try:
        content = path.read_text(errors="replace")
        if len(content) > max_bytes:
            content = content[:max_bytes] + "\n[...truncated...]\n"
        return content
    except OSError:
        return None


def find_kedb_dir() -> Path | None:
    """Find the KEDB directory relative to this script or in DeltaPorts repo."""
    script_dir = Path(__file__).resolve().parent
    kedb_dir = script_dir.parent / "docs" / "kedb"
    if kedb_dir.exists():
        return kedb_dir
    return None


def load_kedb(kedb_dir: Path | None) -> str:
    """Load all KEDB markdown files into a single context block."""
    if not kedb_dir or not kedb_dir.exists():
        return ""
    
    kedb_files = sorted(kedb_dir.glob("*.md"))
    skip_files = {"readme.md", "template.md"}
    kedb_files = [f for f in kedb_files if f.name.lower() not in skip_files]
    
    if not kedb_files:
        return ""
    
    parts = ["## Known Error Database (KEDB)", ""]
    parts.append("The following are known DragonFlyBSD-specific build issues and their fixes:")
    parts.append("")
    
    for kf in kedb_files:
        content = read_file_if_exists(kf, max_bytes=50_000)
        if content:
            parts.append(f"### {kf.stem}")
            parts.append(content)
            parts.append("")
    
    return "\n".join(parts)


# -----------------------------------------------------------------------------
# Triage parsing
# -----------------------------------------------------------------------------

def parse_triage_output(content: str | None) -> dict:
    """Extract Classification and Confidence from triage.md content."""
    result = {"classification": "", "confidence": "", "raw": ""}
    
    if not content:
        return result
    
    result["raw"] = content
    
    # Extract Classification
    match = re.search(r"^##\s*Classification\s*\n+([^\n#]+)", content, re.MULTILINE | re.IGNORECASE)
    if match:
        result["classification"] = match.group(1).strip().lower()
    
    # Extract Confidence
    match = re.search(r"^##\s*Confidence\s*\n+([^\n#]+)", content, re.MULTILINE | re.IGNORECASE)
    if match:
        result["confidence"] = match.group(1).strip().lower()
    
    return result



def build_snippet_feedback(bundle_dir: Path, round_num: int) -> str:
    """Generate feedback section from snippet manifest for agent context."""
    manifest_path = bundle_dir / "analysis" / "snippets" / "manifest.json"
    if not manifest_path.exists():
        return ""
    
    try:
        with open(manifest_path) as f:
            manifest = json.load(f)
    except Exception:
        return ""
    
    rounds = manifest.get("rounds", [])
    if not rounds:
        return ""
    
    # Find the latest round
    latest_round = None
    for r in rounds:
        if r.get("round") == round_num:
            latest_round = r
            break
    
    if not latest_round:
        # Use the last round
        latest_round = rounds[-1]
    
    parts = ["## Snippet Extraction Results", ""]
    parts.append(f"**Round {latest_round.get('round', '?')}** | Source: `{latest_round.get('source', 'unknown')}` | Budget remaining: {latest_round.get('budget_remaining', 0)} bytes")
    parts.append("")
    
    requests = latest_round.get("requests", [])
    if requests:
        parts.append("| Request | Status | Output | Bytes |")
        parts.append("|---------|--------|--------|-------|")
        for req in requests:
            raw = req.get("raw", "?")[:40]
            status = req.get("status", "?")
            output = req.get("output", "-")
            if output and len(output) > 30:
                output = "..." + output[-27:]
            bytes_ = req.get("bytes", 0)
            note = req.get("note", "")
            
            # Add emoji for status
            status_display = {
                "ok": "ok",
                "not_found": "not_found",
                "budget_exceeded": "budget_exceeded",
                "empty": "empty",
            }.get(status, status)
            
            parts.append(f"| `{raw}` | {status_display} | {output or '-'} | {bytes_} |")
            if note:
                parts.append(f"|  | *{note}* | | |")
    
    parts.append("")
    
    # Add summary
    total_rounds = manifest.get("total_rounds", 0)
    max_rounds = int(os.environ.get("DP_HARNESS_MAX_SNIPPET_ROUNDS", "5"))
    remaining_rounds = max_rounds - total_rounds
    
    parts.append(f"**Snippet rounds used:** {total_rounds}/{max_rounds} (remaining: {remaining_rounds})")
    if remaining_rounds <= 0:
        parts.append("**NOTE:** No more snippet rounds available. Work with the information provided.")
    parts.append("")
    
    return "\n".join(parts)


def load_snippets_content(bundle_dir: Path, round_num: int, max_bytes: int = 200_000) -> str:
    """Load extracted snippet contents for inclusion in payload."""
    round_dir = bundle_dir / "analysis" / "snippets" / f"round_{round_num}"
    if not round_dir.exists():
        return ""
    
    parts = ["## Extracted Snippets", ""]
    total_bytes = 0
    
    # Load round manifest for context
    manifest_path = round_dir / "manifest.json"
    if manifest_path.exists():
        try:
            with open(manifest_path) as f:
                round_manifest = json.load(f)
            source_type = round_manifest.get("source", "unknown")
            distfile = round_manifest.get("distfile")
            if distfile:
                parts.append(f"*Source: distfile `{distfile}`*")
            else:
                parts.append(f"*Source: {source_type}*")
            parts.append("")
        except Exception:
            pass
    
    # Walk through subdirectories (source, buildsystem, configure, log)
    for subdir in sorted(round_dir.iterdir()):
        if not subdir.is_dir() or subdir.name.startswith("."):
            continue
        
        for file_path in sorted(subdir.glob("*.txt")):
            if total_bytes >= max_bytes:
                parts.append(f"*[...truncated, budget exceeded...]*")
                break
            
            try:
                content = file_path.read_text(errors="replace")
                remaining = max_bytes - total_bytes
                if len(content) > remaining:
                    content = content[:remaining] + "\n[...truncated...]\n"
                
                # Infer original filename from safe name
                original_name = file_path.stem.replace("_", "/")
                
                parts.append(f"### {subdir.name}/{original_name}")
                parts.append("```")
                parts.append(content)
                parts.append("```")
                parts.append("")
                
                total_bytes += len(content)
            except Exception:
                continue
        
        if total_bytes >= max_bytes:
            break
    
    if total_bytes == 0:
        return ""
    
    return "\n".join(parts)


def build_triage_payload(
    bundle_dir: Path | None,
    kedb_dir: Path | None = None,
    job: dict | None = None
) -> str:
    """Build the triage prompt from bundle contents.

    Phase 4: assembles via ``dportsv3.agent.context.render_payload``
    over the section roster in ``context.TRIAGE_SECTIONS``. Behavior
    is byte-equivalent to the pre-Phase-4 ``parts.append(...)`` form;
    parity is locked in by ``tests/test_triage_payload_parity.py``.
    """
    from dportsv3.agent.context import ContextCtx, TRIAGE_SECTIONS, render_payload

    job = job or {}
    bundle_id = job.get("bundle_id")
    run_id = job.get("run_id")
    origin = job.get("origin")

    # Pre-load fields sections need without doing I/O at render time.
    sibling_raw = job.get("sibling_bundle_ids", "") or ""
    sibling_ids = [s.strip() for s in sibling_raw.split(",") if s.strip()]

    prior_triage_ids: list[str] = []
    if origin:
        for entry in port_bundle_history(origin):
            bid = entry.get("bundle_id")
            if not bid or bid == bundle_id or bid in sibling_ids:
                continue
            prior_triage_ids.append(bid)
            if len(prior_triage_ids) >= 2:
                break

    user_context_text, _ = get_user_context(run_id, origin)
    kedb_text = load_kedb(kedb_dir)

    ctx = ContextCtx(
        bundle_dir=bundle_dir,
        bundle_id=bundle_id,
        job=job,
        kedb_dir=kedb_dir,
        sibling_bundle_ids=sibling_ids,
        prior_triage_bundle_ids=prior_triage_ids,
        user_context_text=user_context_text or None,
        kedb_text=kedb_text or None,
        read_bundle_text=read_bundle_text,
        bundle_artifact_list=bundle_artifact_list,
        snippet_feedback=build_snippet_feedback,
        snippet_content=load_snippets_content,
    )
    return render_payload(list(TRIAGE_SECTIONS), ctx)


def build_patch_payload(
    bundle_dir: Path | None,
    kedb_dir: Path | None = None,
    job: dict | None = None
) -> str:
    """Build the patch generation prompt including triage output.

    Phase 4: assembles via ``dportsv3.agent.context.render_payload``
    over the section roster in ``context.PATCH_SECTIONS``. Byte-
    equivalent to the pre-Phase-4 ``parts.append(...)`` form; parity
    locked in by ``tests/test_patch_payload_parity.py``.
    """
    from dportsv3.agent.context import ContextCtx, PATCH_SECTIONS, render_payload

    job = job or {}
    bundle_id = job.get("bundle_id")
    run_id = job.get("run_id")
    origin = job.get("origin")
    target = job.get("target", "") or ""

    # Pre-load sibling list + prior-patch bundle list.
    sibling_raw = job.get("sibling_bundle_ids", "") or ""
    sibling_ids = [s.strip() for s in sibling_raw.split(",") if s.strip()]

    prior_patch_ids: list[str] = []
    if origin:
        for entry in port_bundle_history(origin):
            bid = entry.get("bundle_id")
            if not bid or bid == bundle_id or bid in sibling_ids:
                continue
            prior_patch_ids.append(bid)
            if len(prior_patch_ids) >= 3:
                break

    # Automation-context inputs.
    window_hours = int(os.environ.get("DP_HARNESS_ATTEMPT_WINDOW_HOURS", "2"))
    max_attempts_cap = int(os.environ.get("DP_HARNESS_MAX_PATCH_ATTEMPTS", "3"))
    prior_failures = (
        _load_port_history(target, origin, window_hours).recent_failures
        if origin else 0
    )

    user_context_text, _ = get_user_context(run_id, origin)
    kedb_text = load_kedb(kedb_dir)

    ctx = ContextCtx(
        bundle_dir=bundle_dir,
        bundle_id=bundle_id,
        job=job,
        kedb_dir=kedb_dir,
        sibling_bundle_ids=sibling_ids,
        prior_patch_bundle_ids=prior_patch_ids,
        user_context_text=user_context_text or None,
        kedb_text=kedb_text or None,
        prior_failure_count=prior_failures,
        window_hours=window_hours,
        max_attempts_cap=max_attempts_cap,
        read_bundle_text=read_bundle_text,
        bundle_artifact_list=bundle_artifact_list,
        snippet_feedback=build_snippet_feedback,
        snippet_content=load_snippets_content,
    )
    return render_payload(list(PATCH_SECTIONS), ctx)



# -----------------------------------------------------------------------------

def move_job(job_path: Path, dest_dir: str) -> Path:
    """Move job file to destination directory (done/failed).
    Also moves any associated .job.error file.
    """
    dest = job_path.parent.parent / dest_dir / job_path.name
    job_path.rename(dest)
    
    # Also move error file if it exists
    error_file = job_path.with_suffix(".job.error")
    if error_file.exists():
        error_dest = dest.with_suffix(".job.error")
        try:
            error_file.rename(error_dest)
        except OSError:
            pass  # Best effort
    
    return dest


def write_error_note(job_path: Path, error: str):
    """Write error note next to failed job."""
    error_path = job_path.with_suffix(".job.error")
    with open(error_path, "w") as f:
        f.write(f"timestamp={datetime.now(timezone.utc).isoformat()}\n")
        f.write(f"error={error}\n")


def dsynth_active(env_name: str, queue_root: Path) -> tuple[bool, str]:
    """Check if a dsynth process is currently running on this host.

    pgrep is the only authority: it tells us if a dsynth process exists
    right now. Process existence is the only state that actually
    matters for "would a second dsynth invocation collide". The
    tracker's ``build_runs.finished_at`` can be stale (operator ^C'd
    dsynth, hook_run_end never ran, row left at NULL forever) and would
    deadlock the runner if we treated it as a gate.

    Returns ``(True, reason)`` when at least one dsynth process is
    seen, ``(False, "")`` otherwise. ``env_name`` is currently unused
    but kept in the signature for the future case where we'd scope
    pgrep to processes inside a specific chroot.

    On systems without pgrep (none in scope today — dfly has it in
    base), this fails open: returns ``(False, "")`` and the runner
    proceeds without a gate.
    """
    # -x matches process basename exactly so we don't false-positive on
    # cmdlines that merely contain "dsynth" (queue paths, log paths, etc.).
    try:
        result = subprocess.run(
            ["pgrep", "-x", "dsynth"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            pids = result.stdout.strip().splitlines()
            return True, f"dsynth process(es) on host: {','.join(pids)}"
    except (subprocess.SubprocessError, OSError, FileNotFoundError):
        pass
    return False, ""


def _job_dedup_key(meta: dict) -> tuple | None:
    """Key for grouping pending jobs that should be processed together.

    Jobs are siblings when they share (type, profile, origin, flavor).
    Returning ``None`` means "can't group this job" — claim it alone.
    """
    jt = meta.get("type")
    profile = meta.get("profile")
    origin = meta.get("origin")
    if not jt or not origin or not profile:
        return None
    return (jt, profile, origin, meta.get("flavor", ""))


def claim_next_job_batch(queue_root: Path) -> tuple[Path, list[Path]] | None:
    """Claim the oldest pending job and any sibling pending jobs.

    Siblings = pending jobs with the same (type, profile, origin,
    flavor) as the lead. All move to inflight/. The lead is the file
    the runner processes; sibling evidence is folded into the payload
    via ``job["sibling_bundle_ids"]``. All members move together to
    done/failed on completion.

    Returns ``(lead_path, sibling_paths)`` or ``None`` if nothing pending.
    """
    pending_dir = queue_root / "pending"
    inflight_dir = queue_root / "inflight"
    jobs = sorted(pending_dir.glob("*.job"))

    for lead_path in jobs:
        try:
            lead_meta = parse_job_file(lead_path)
        except Exception:
            continue
        lead_key = _job_dedup_key(lead_meta)
        candidate_siblings: list[Path] = []
        if lead_key is not None:
            for other in jobs:
                if other == lead_path:
                    continue
                try:
                    other_meta = parse_job_file(other)
                except Exception:
                    continue
                if _job_dedup_key(other_meta) == lead_key:
                    candidate_siblings.append(other)
        # Move lead first; if that races, try the next pending job.
        try:
            lead_dest = inflight_dir / lead_path.name
            lead_path.rename(lead_dest)
        except OSError:
            continue
        from dportsv3.agent.lifecycle import JobEvent
        _apply_transition(lead_dest.name, JobEvent.CLAIM)
        moved_siblings: list[Path] = []
        for s in candidate_siblings:
            try:
                s_dest = inflight_dir / s.name
                s.rename(s_dest)
                moved_siblings.append(s_dest)
            except OSError:
                # Another runner grabbed it or it vanished — skip.
                continue
            _apply_transition(s_dest.name, JobEvent.CLAIM)
        return lead_dest, moved_siblings
    return None


def enqueue_patch_job(
    queue_root: Path,
    job: dict,
    *,
    tier_name: str | None = None,
    dev_env: str | None = None,
):
    """Enqueue a patch job based on completed triage job.

    ``tier_name`` is the trust tier resolved at triage time (AUTO/ASSIST);
    propagating it lets the patch worker use the right budget without
    re-parsing triage.md. ``dev_env`` is the dev-env name the patch
    flow should operate against; omit to let the patch worker fall
    back to DP_HARNESS_ENV.
    """
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%SZ")
    origin_safe = job.get("origin", "unknown").replace("/", "_")
    pid = os.getpid()

    job_name = f"{ts}-{job.get('profile', 'unknown')}-{origin_safe}-{pid}-patch.job"

    pending_dir = queue_root / "pending"
    job_path = pending_dir / job_name

    # Inherit iteration from parent job, or start at 1
    iteration = int(job.get("iteration", "1"))
    max_iterations = int(job.get("max_iterations", str(DEFAULT_MAX_ITERATIONS)))

    content = [
        f"type=patch",
        f"created_ts_utc={ts}",
        f"profile={job.get('profile', '')}",
        f"origin={job.get('origin', '')}",
        f"flavor={job.get('flavor', '')}",
        f"bundle_id={job.get('bundle_id', '')}",
        f"run_id={job.get('run_id', '')}",
        f"triage_relpath=analysis/triage.md",
        f"iteration={iteration}",
        f"max_iterations={max_iterations}",
    ]
    if tier_name:
        content.append(f"tier={tier_name}")
    if dev_env:
        content.append(f"dev_env={dev_env}")

    # Include previous_bundle if this is a retry
    previous_bundle = job.get("previous_bundle")
    if previous_bundle:
        content.append(f"previous_bundle={previous_bundle}")
    
    # Atomic write
    tmp_path = job_path.with_suffix(".tmp")
    with open(tmp_path, "w") as f:
        f.write("\n".join(content) + "\n")
    tmp_path.rename(job_path)

    _register_new_job(
        job_path.name,
        metadata={
            "type": "patch",
            "origin": job.get("origin", ""),
            "flavor": job.get("flavor", ""),
            "created_ts_utc": ts,
            "path": str(job_path),
            "target": job.get("target")
                or os.environ.get("DPORTSV3_TRACKER_TARGET", ""),
        },
    )
    return job_path



# -----------------------------------------------------------------------------
# Job processing
# -----------------------------------------------------------------------------

def _write_triage_audit_harness(
    bundle_dir: Path | None,
    bundle_id: str | None,
    result,  # dportsv3.agent.triage.TriageResult
    model: str,
) -> None:
    """Write the harness-side audit JSON to the bundle.

    The markdown response is already on disk (triage.run writes
    analysis/triage.md after each LLM round). This adds an
    analysis/triage.json with classification, confidence, usage, and
    provenance.
    """
    audit = {
        "classification": result.classification,
        "confidence": result.confidence,
        "snippet_rounds": result.snippet_rounds,
        "tokens_used": {
            "prompt": result.usage.prompt_tokens,
            "completion": result.usage.completion_tokens,
            "total": result.usage.total_tokens,
        },
        "model": model,
        "via": "dportsv3.agent.triage",
    }
    data = (json.dumps(audit, indent=2) + "\n").encode("utf-8")
    if bundle_id:
        if not artifact_store_put(bundle_id, "analysis/triage.json", data, "json"):
            raise RuntimeError("failed to write triage.json to artifact store")
        return
    if bundle_dir is None:
        raise RuntimeError("bundle_dir or bundle_id required")
    out = bundle_dir / "analysis" / "triage.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)


def _finish_orchestrator_run(
    result,                         # OrchestratorResult
    *,
    step_name: str,
    sibling_paths: list[Path],
    failure_event: str,             # JobEvent value to fire on halt
) -> tuple[bool, str]:
    """Extract (success, status_str) from an OrchestratorResult and
    fan the step's lifecycle events out to sibling jobs.

    The orchestrator already fired the step's outcome events for the
    lead job_id. For siblings, we replay the same events here. When
    the orchestrator halted (precheck-fail, run-exception), no events
    fired for the lead either — synthesize ``failure_event`` and fire
    it for lead + siblings.
    """
    from dportsv3.agent.lifecycle import JobEvent
    from dportsv3.agent.step import outcome_events

    if result.halted:
        # Normal orchestrator halts happen before outcome events are
        # fired. If a future step shape returns an outcome while halting,
        # mirror those events instead of synthesizing a second failure.
        step_result = result.step_by_name(step_name)
        events = outcome_events(step_result.outcome if step_result else None)
        if events:
            detail = (step_result.outcome.detail if step_result and step_result.outcome
                      else {"reason": result.halt_reason})
            for s in sibling_paths:
                for evt in events:
                    _apply_transition(s.name, evt, detail=detail)
            status_str = detail.get("status_str", result.halt_reason)
            return False, status_str or f"{step_name} halted"

        # Lead got no events from the orchestrator; synthesize the
        # catchall failure event.
        evt = JobEvent(failure_event)
        _apply_transition(result.job_id, evt,
                          detail={"reason": result.halt_reason})
        for s in sibling_paths:
            _apply_transition(s.name, evt,
                              detail={"reason": result.halt_reason})
        return False, result.halt_reason or f"{step_name} halted"

    step_result = result.step_by_name(step_name)
    if step_result is None or step_result.outcome is None:
        reason = (step_result.readiness.reason
                  if step_result else f"{step_name} skipped")
        evt = JobEvent(failure_event)
        _apply_transition(result.job_id, evt, detail={"reason": reason})
        for s in sibling_paths:
            _apply_transition(s.name, evt, detail={"reason": reason})
        return False, reason

    outcome = step_result.outcome
    # Sibling fan-out: same events the orchestrator fired for the lead.
    sibling_events = outcome_events(outcome)
    detail = outcome.detail or {}
    for s in sibling_paths:
        for evt in sibling_events:
            _apply_transition(s.name, evt, detail=detail)

    status_str = outcome.detail.get("status_str", "unknown")
    if outcome.status == "failed":
        return False, status_str
    return True, status_str


def process_triage_job(
    queue_root: Path,
    job_path: Path,
    sibling_paths: list[Path],
    job: dict,
    bundle_dir: Path | None,
    kedb_dir: Path | None,
) -> tuple[bool, str]:
    """Process a triage job by driving TriageStep through the orchestrator.

    Phase 5: TriageStep's ``StepOutcome.next_event`` + ``extra_events``
    encode the lifecycle events to fire on completion. The
    orchestrator fires them for the lead; this wrapper fans the
    same events out to siblings. ``_completion_events_for`` retires.
    """
    from dportsv3.agent.step import Orchestrator, StepCtx
    from dportsv3.agent.steps import TriageServices, TriageStep

    payload = build_triage_payload(bundle_dir, kedb_dir, job)
    origin = job.get("origin", "unknown")
    job_id = job_path.name

    ctx = StepCtx(
        job_id=job_id,
        job=job,
        queue_root=queue_root,
        apply_transition=_apply_transition,
        activity_log=activity_log,
        db_conn=_state_db_conn,
        env_name=os.environ.get("DP_HARNESS_ENV") or None,
        bundle_dir=bundle_dir,
        bundle_id=job.get("bundle_id"),
        kedb_dir=kedb_dir,
    )
    ctx.state["job_path"] = job_path
    ctx.state["payload"] = payload
    ctx.state["origin"] = origin
    ctx.state["policy_path"] = os.environ.get(
        "DP_HARNESS_POLICY", _DEFAULT_POLICY_PATH,
    )
    ctx.state["services"] = TriageServices(
        materialize_bundle=_materialize_bundle,
        artifact_store_put=artifact_store_put,
        write_error_note=write_error_note,
        write_triage_audit=_write_triage_audit_harness,
        enqueue_patch_job=enqueue_patch_job,
        upsert_user_context_request=upsert_user_context_request,
        update_runner_status=update_runner_status,
        probe_health_cached=probe_health_cached,
        cached_health_broken=_cached_health_broken,
        load_port_history=_load_port_history,
        log=log,
        activity_log=activity_log,
    )

    result = Orchestrator().run(ctx, [TriageStep()])
    return _finish_orchestrator_run(
        result, step_name="triage",
        sibling_paths=sibling_paths,
        failure_event="triage_fail",
    )
def _summarize_tool_call(tool: str, args: dict, result: dict) -> str:
    """One-line summary of a tool invocation for the activity log.

    The activity_log's ``message`` column drives the UI's at-a-glance
    view. Keep it short and informative — favor the args/result fields
    a human would look for first.
    """
    args = args or {}
    result = result if isinstance(result, dict) else {"value": result}
    ok = result.get("ok")
    ok_tag = "" if ok is None else (" ok" if ok else " FAIL")
    if tool == "env_verify":
        return f"status={result.get('status', '?')}{ok_tag}"
    if tool in ("get_file", "list_dir"):
        return f"{args.get('path', '')}{ok_tag}"
    if tool == "put_file":
        return f"{args.get('path', '')} ({len((args.get('content') or '') )} bytes){ok_tag}"
    if tool == "grep":
        return (
            f"pattern={args.get('pattern', '')!r} path={args.get('path', '')} "
            f"matches={len(result.get('matches') or [])}{ok_tag}"
        )
    if tool == "materialize_dports":
        return f"origin={args.get('origin', '')}{ok_tag}"
    if tool == "extract":
        return f"origin={args.get('origin', '')}{ok_tag}"
    if tool in ("dupe", "genpatch"):
        return f"path={args.get('path', '')}{ok_tag}"
    if tool == "install_patches":
        n = len(result.get("installed") or [])
        return f"origin={args.get('origin', '')} installed={n}{ok_tag}"
    if tool == "emit_diff":
        diff_len = len(result.get('diff') or '')
        return (
            f"origin={args.get('origin', '')} relpath={args.get('relpath', '')} "
            f"diff_bytes={diff_len}{ok_tag}"
        )
    if tool == "dsynth_build":
        rb = result.get("rebuild_ok")
        return f"origin={args.get('origin', '')} rebuild_ok={rb}{ok_tag}"
    if tool == "dsynth_log":
        return f"origin={args.get('origin', '')} lines={len((result.get('text') or '').splitlines())}{ok_tag}"
    # Fallback: show first arg key=value pair
    if args:
        k, v = next(iter(args.items()))
        return f"{k}={str(v)[:80]}{ok_tag}"
    return f"(no args){ok_tag}"


def _write_tool_trace(
    bundle_dir: Path | None,
    bundle_id: str | None,
    trace_events: list[dict],
) -> None:
    """Persist the structured per-tool trace to the bundle.

    JSONL so it appends naturally and is grep-friendly. One line per
    event (attempt_start / tool_call / attempt_end).
    """
    if not trace_events:
        return
    lines = [json.dumps(ev, default=str) for ev in trace_events]
    data = ("\n".join(lines) + "\n").encode("utf-8")
    if bundle_id:
        artifact_store_put(bundle_id, "analysis/tool_trace.jsonl", data, "text")
    elif bundle_dir is not None:
        out = bundle_dir / "analysis" / "tool_trace.jsonl"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(data)


def _write_patch_audit_harness(
    bundle_dir: Path | None,
    bundle_id: str | None,
    result,  # dportsv3.agent.attempt_loop.PatchResult
    model: str,
) -> None:
    """Write harness-side outputs to the bundle: patch.md, rebuild_proof.json,
    changes.diff (host-side git diff in the env), and patch_audit.json."""
    text = (result.final_text or "").rstrip() + "\n"
    md_bytes = text.encode("utf-8")
    if bundle_id:
        artifact_store_put(bundle_id, "analysis/patch.md", md_bytes, "text")
    else:
        analysis = bundle_dir / "analysis"
        analysis.mkdir(parents=True, exist_ok=True)
        (analysis / "patch.md").write_bytes(md_bytes)

    if result.proof is not None:
        proof_bytes = (json.dumps(result.proof, indent=2) + "\n").encode("utf-8")
        if bundle_id:
            artifact_store_put(bundle_id, "analysis/rebuild_proof.json", proof_bytes, "json")
        else:
            (bundle_dir / "analysis" / "rebuild_proof.json").write_bytes(proof_bytes)

    audit = {
        "status": result.status,
        "model": model,
        "tokens_used": {
            "prompt": result.usage.prompt_tokens,
            "completion": result.usage.completion_tokens,
            "total": result.usage.total_tokens,
        },
        "attempts": [
            {"attempt": a.attempt, "tokens": a.tokens, "rebuild_ok": a.rebuild_ok}
            for a in result.attempts
        ],
        "via": "dportsv3.agent.patch",
    }
    audit_bytes = (json.dumps(audit, indent=2) + "\n").encode("utf-8")
    if bundle_id:
        artifact_store_put(bundle_id, "analysis/patch_audit.json", audit_bytes, "json")
    else:
        (bundle_dir / "analysis" / "patch_audit.json").write_bytes(audit_bytes)


def _write_changes_diff(bundle_dir: Path | None, bundle_id: str | None, env: str, origin: str) -> None:
    """Capture host-side `git diff` against the env's DeltaPorts overlay HEAD
    and write to analysis/changes.diff. Best-effort: failures are logged but
    not fatal — the agent's reasoning is in patch.md regardless."""
    try:
        from dportsv3.agent import worker  # type: ignore[import-not-found]
        paths = worker.env_paths(env)
        delta = paths.deltaports
        import subprocess
        rel = f"ports/{origin}"
        p = subprocess.run(
            ["git", "-C", str(delta), "diff", "--", rel],
            capture_output=True, text=True, check=False,
        )
        diff_bytes = p.stdout.encode("utf-8")
    except Exception as exc:
        diff_bytes = f"# failed to capture diff: {exc}\n".encode("utf-8")

    if bundle_id:
        artifact_store_put(bundle_id, "analysis/changes.diff", diff_bytes, "text")
    elif bundle_dir:
        out = bundle_dir / "analysis" / "changes.diff"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(diff_bytes)


def process_patch_job(
    queue_root: Path,
    job_path: Path,
    sibling_paths: list[Path],
    job: dict,
    bundle_dir: Path | None,
    kedb_dir: Path | None,
) -> tuple[bool, str]:
    """Process a patch job by driving PatchAttemptStep through the orchestrator.

    Phase 5: PatchAttemptStep's ``StepOutcome.next_event`` +
    ``extra_events`` encode the lifecycle events to fire on
    completion. The orchestrator fires them for the lead; this
    wrapper fans the same events out to siblings.
    """
    from dportsv3.agent.step import Orchestrator, StepCtx
    from dportsv3.agent.steps import PatchAttemptStep, PatchServices

    payload = build_patch_payload(bundle_dir, kedb_dir, job)
    origin = job.get("origin", "unknown")
    job_id = job_path.name

    ctx = StepCtx(
        job_id=job_id,
        job=job,
        queue_root=queue_root,
        apply_transition=_apply_transition,
        activity_log=activity_log,
        db_conn=_state_db_conn,
        env_name=job.get("dev_env") or os.environ.get("DP_HARNESS_ENV") or None,
        bundle_dir=bundle_dir,
        bundle_id=job.get("bundle_id"),
        kedb_dir=kedb_dir,
    )
    ctx.state["job_path"] = job_path
    ctx.state["payload"] = payload
    ctx.state["origin"] = origin
    ctx.state["policy_path"] = os.environ.get(
        "DP_HARNESS_POLICY", _DEFAULT_POLICY_PATH,
    )
    ctx.state["services"] = PatchServices(
        read_bundle_text=read_bundle_text,
        parse_triage_output=parse_triage_output,
        write_error_note=write_error_note,
        write_patch_audit=_write_patch_audit_harness,
        write_tool_trace=_write_tool_trace,
        write_changes_diff=_write_changes_diff,
        looks_env_suspicious=_looks_env_suspicious,
        invalidate_health_cache=invalidate_health_cache,
        cached_health_broken=_cached_health_broken,
        summarize_tool_call=_summarize_tool_call,
        activity_log=activity_log,
        log=log,
        load_port_history=_load_port_history,
    )

    result = Orchestrator().run(ctx, [PatchAttemptStep()])
    return _finish_orchestrator_run(
        result, step_name="patch",
        sibling_paths=sibling_paths,
        failure_event="patch_gave_up",
    )
def process_job(
    queue_root: Path,
    job_path: Path,
    sibling_paths: list[Path],
    dry_run: bool,
    kedb_dir: Path | None,
):
    """Process a single job (dispatch based on type).

    Phase 5 Step 4: lifecycle event firing moved into the steps
    themselves (via ``StepOutcome.next_event`` / ``extra_events``).
    The orchestrator fires those for the lead; the step's wrapper
    (``process_triage_job`` / ``process_patch_job``) fans them out
    to siblings. This function is now a pure dispatcher: job-file
    parsing, dry-run handling, START-event firing, filesystem move.

    ``sibling_paths`` are inflight job files claimed alongside the lead
    by ``claim_next_job_batch``.
    """
    from dportsv3.agent.lifecycle import JobEvent

    job = parse_job_file(job_path)
    # Fold sibling bundles into the payload context.
    sibling_bundles: list[str] = []
    for s_path in sibling_paths:
        try:
            s_meta = parse_job_file(s_path)
        except Exception:
            continue
        bid = s_meta.get("bundle_id")
        if bid and bid != job.get("bundle_id"):
            sibling_bundles.append(bid)
    if sibling_bundles:
        job["sibling_bundle_ids"] = ",".join(sibling_bundles)
        log(queue_root, "INFO",
            f"batched {len(sibling_bundles)} sibling job(s) with {job_path.name}: "
            f"{','.join(sibling_bundles)}")
    job_type = job.get("type", "triage")
    bundle_dir_value = job.get("bundle_dir")
    bundle_dir = Path(bundle_dir_value) if bundle_dir_value else None
    bundle_id = job.get("bundle_id")
    origin = job.get("origin", "unknown")
    job_id = job_path.name

    log(queue_root, "INFO", f"processing job {job_path.name}")
    update_runner_status("processing", job_id=job_id, stage=f"{job_type}_start",
                         extra={"origin": origin, "type": job_type})

    if bundle_dir is None and not bundle_id:
        log(queue_root, "ERROR", "missing bundle_id/bundle_dir in job")
        write_error_note(job_path, "missing bundle_id/bundle_dir in job")
        move_job(job_path, "failed")
        update_runner_status("idle", job_id=None, stage=None)
        return
    if bundle_dir is not None and not bundle_dir.exists():
        log(queue_root, "ERROR", f"bundle_dir does not exist: {bundle_dir}")
        write_error_note(job_path, f"bundle_dir does not exist: {bundle_dir}")
        move_job(job_path, "failed")
        update_runner_status("idle", job_id=None, stage=None)
        return

    if dry_run:
        if job_type == "patch":
            payload = build_patch_payload(bundle_dir, kedb_dir, job)
        else:
            payload = build_triage_payload(bundle_dir, kedb_dir, job)

        log(queue_root, "INFO",
            f"[dry-run] type={job_type}, would send payload ({len(payload)} bytes)")
        print("=" * 60)
        print(f"JOB TYPE: {job_type}")
        print("=" * 60)
        print(payload)
        print("=" * 60)
        move_job(job_path, "pending")
        update_runner_status("idle", job_id=None, stage=None)
        return

    # Fire the START lifecycle event for the lead + siblings before
    # invoking the step (which fires its own completion events).
    if job_type == "patch":
        start_event = JobEvent.PATCH_START
        _apply_transition(job_path.name, start_event)
        for s in sibling_paths:
            _apply_transition(s.name, start_event)
        success, status = process_patch_job(
            queue_root, job_path, sibling_paths, job, bundle_dir, kedb_dir,
        )
    elif job_type == "triage":
        start_event = JobEvent.TRIAGE_START
        _apply_transition(job_path.name, start_event)
        for s in sibling_paths:
            _apply_transition(s.name, start_event)
        success, status = process_triage_job(
            queue_root, job_path, sibling_paths, job, bundle_dir, kedb_dir,
        )
    else:
        # Unknown job type — fire the catchall failure event for lead +
        # siblings, write the error note, move everything to failed/.
        success, status = False, f"unknown job type: {job_type}"
        _apply_transition(job_path.name, JobEvent.PATCH_GAVE_UP,
                          detail={"reason": status})
        for s in sibling_paths:
            _apply_transition(s.name, JobEvent.PATCH_GAVE_UP,
                              detail={"reason": status})

    error_msg = (status or "Unknown error")[:500] if not success else None

    if success:
        move_job(job_path, "done")
        for s in sibling_paths:
            try:
                move_job(s, "done")
            except OSError:
                continue
        log(queue_root, "INFO",
            f"moved job + {len(sibling_paths)} sibling(s) to done/")
    else:
        # Write error file for visibility in UI.
        error_file = job_path.with_suffix(".job.error")
        try:
            error_file.write_text(error_msg)
            log(queue_root, "DEBUG", f"wrote error file: {error_file}")
        except OSError as e:
            log(queue_root, "WARN", f"failed to write error file: {e}")

        move_job(job_path, "failed")
        for s in sibling_paths:
            try:
                move_job(s, "failed")
            except OSError:
                continue
        log(queue_root, "ERROR",
            f"moved job + {len(sibling_paths)} sibling(s) to failed/ ({status})")

    update_runner_status("idle", job_id=None, stage=None)


# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Process dsynth failure jobs via the dportsv3.agent harness")
    parser.add_argument("--queue-root", required=True, help="Path to queue directory")
    parser.add_argument("--once", action="store_true", help="Process one job and exit")
    parser.add_argument("--dry-run", action="store_true", help="Print payload without calling the LLM")
    parser.add_argument("--kedb-dir", help="Path to KEDB directory (default: auto-detect)")
    args = parser.parse_args(argv)

    queue_root = Path(args.queue_root)

    for subdir in ["pending", "inflight", "done", "failed"]:
        d = queue_root / subdir
        if not d.exists():
            print(f"error: queue directory missing: {d}", file=sys.stderr)
            return 1

    init_state_db(queue_root)
    start_heartbeat()

    # Reap any inflight-ish jobs left over from a previous runner
    # instance — they're DEAD-with-runner_restart by definition.
    # Operator can re-enqueue via the bundle if a retry is warranted.
    if _state_db_conn is not None:
        try:
            from dportsv3.agent import lifecycle  # type: ignore[import-not-found]
            with _state_db_lock:
                n = lifecycle.reap_orphans(_state_db_conn, actor=f"runner-{os.getpid()}")
            if n > 0:
                log(queue_root, "INFO",
                    f"reaped {n} orphan job(s) from previous runner instance")
        except Exception as exc:
            log(queue_root, "WARN", f"reap_orphans failed: {exc}")

    if args.kedb_dir:
        kedb_dir = Path(args.kedb_dir)
        if not kedb_dir.exists():
            print(f"warning: KEDB directory not found: {kedb_dir}", file=sys.stderr)
            kedb_dir = None
    else:
        kedb_dir = find_kedb_dir()

    triage_model = os.environ.get("DP_HARNESS_TRIAGE_MODEL") or "<unset>"
    patch_model_env = os.environ.get("DP_HARNESS_PATCH_MODEL")
    if patch_model_env:
        patch_model = patch_model_env
    elif triage_model != "<unset>":
        patch_model = f"{triage_model} (fallback from triage)"
    else:
        patch_model = "<unset>"
    kedb_info = str(kedb_dir) if kedb_dir else "none"
    log(queue_root, "INFO",
        f"starting runner (once={args.once}, dry_run={args.dry_run}, "
        f"triage_model={triage_model}, patch_model={patch_model}, kedb={kedb_info})")
    activity_log(queue_root, "runner_start",
                 f"Runner started (triage={triage_model}, patch={patch_model})")
    update_runner_status("idle", job_id=None, stage=None)

    # Runner-level dev-env. When set, the runner refuses to claim jobs
    # while dsynth is active in / on that env, to avoid two dsynth
    # invocations against the same buildbase. Unset = no gate (operator
    # accepts the risk).
    runner_env = os.environ.get("DP_HARNESS_ENV") or ""
    if not runner_env:
        log(queue_root, "WARN",
            "DP_HARNESS_ENV is unset; dsynth-busy gating disabled. "
            "Concurrent dsynth runs in the same env may corrupt buildbase.")

    _last_busy_reason = ""
    _last_health_reason = ""
    health_cache_seconds = int(
        os.environ.get("DP_HARNESS_HEALTH_CACHE_SECONDS", "60")
    )

    def _gate_blocked() -> bool:
        nonlocal _last_busy_reason, _last_health_reason
        # Health probe first. If broken, we pause regardless of
        # dsynth-busy state — there's no point running anything until
        # the env is repaired. Cache keeps this cheap (default 60s);
        # tool errors that look env-suspicious invalidate the cache
        # so a freshly-broken env is detected on the next gate.
        if runner_env:
            eh = probe_health_cached(runner_env, health_cache_seconds)
            if eh.status == "broken":
                reason = (eh.operator_action
                          or f"env {runner_env} status=broken")
                if reason != _last_health_reason:
                    log(queue_root, "INFO",
                        f"runner paused: health broken: {reason}")
                    activity_log(queue_root, "health_broken",
                                 f"env {runner_env} broken; pausing runner",
                                 extra={"operator_action": reason[:500]})
                    _last_health_reason = reason
                update_runner_status(
                    "paused", job_id=None,
                    stage=f"health_broken: {reason[:120]}",
                )
                return True
            if _last_health_reason and eh.status == "ready":
                log(queue_root, "INFO", "runner resumed: health ready")
                activity_log(queue_root, "health_ready",
                             f"env {runner_env} healthy; resuming")
                _last_health_reason = ""

        if not runner_env:
            return False
        busy, reason = dsynth_active(runner_env, queue_root)
        if busy and reason != _last_busy_reason:
            log(queue_root, "INFO", f"runner paused: {reason}")
            update_runner_status("paused", job_id=None, stage=f"waiting: {reason}")
            _last_busy_reason = reason
        elif not busy and _last_busy_reason:
            log(queue_root, "INFO", "runner resumed: dsynth idle")
            update_runner_status("idle", job_id=None, stage="waiting")
            _last_busy_reason = ""
        return busy

    try:
        if args.once:
            if _gate_blocked():
                log(queue_root, "INFO", "skipping --once: dsynth active")
            else:
                batch = claim_next_job_batch(queue_root)
                if batch:
                    lead, siblings = batch
                    process_job(queue_root, lead, siblings, args.dry_run, kedb_dir)
                else:
                    log(queue_root, "INFO", "no jobs in queue")
        else:
            while True:
                if _gate_blocked():
                    if _last_health_reason:
                        # Don't hammer the chroot probing while the env
                        # is known broken; the cache (default 60s) is
                        # what limits the rate. Sleep aligns with that.
                        time.sleep(health_cache_seconds)
                    else:
                        time.sleep(DSYNTH_LOCK_POLL_SECONDS)
                    continue
                process_user_context_updates(queue_root)
                batch = claim_next_job_batch(queue_root)
                if batch:
                    lead, siblings = batch
                    process_job(queue_root, lead, siblings, args.dry_run, kedb_dir)
                else:
                    update_runner_status("idle", job_id=None, stage="waiting")
                    time.sleep(5)
    except KeyboardInterrupt:
        log(queue_root, "INFO", "shutting down (keyboard interrupt)")
        activity_log(queue_root, "runner_stop", "Runner stopped (keyboard interrupt)")
    finally:
        stop_heartbeat()
        update_runner_status("stopped", job_id=None, stage=None)

    return 0


if __name__ == "__main__":
    sys.exit(main())
