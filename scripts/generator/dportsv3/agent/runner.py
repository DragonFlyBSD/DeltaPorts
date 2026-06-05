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
    dev_env=NAME          — dev-env for this job; resolved by
                            env_resolver if absent (tracker active
                            env → --env CLI flag → auto-pick if
                            exactly one env exists → refuse)
    previous_bundle=...   — bundle from previous failed attempt
"""

import argparse
import hashlib
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

# lifecycle imports only stdlib (no cycle back to runner), so the
# canonical "actively working" state set is safe to bind at module
# load. Other lifecycle uses stay function-local per this file's
# convention, but this one backs a module-level constant.
from dportsv3.agent.lifecycle import ACTIVE_WORK_STATE_VALUES


class _VerifyBranchUnavailable(Exception):
    """Raised inside the verify dispatch when the fresh
    ``bundle/<id>-verify`` branch couldn't be checked out. Aborts the
    verify run before replay — verify's verdict only means anything if
    the diff was applied against a known-clean base."""


# Max fix iterations before giving up on a port
DEFAULT_MAX_ITERATIONS = 3

DEFAULT_ARTIFACT_STORE_URL = "http://127.0.0.1:8788"
DEFAULT_TRACKER_URL = "http://127.0.0.1:8080"

# Default location of config/agentic-policy.json. ``runner.py`` lives
# at scripts/generator/dportsv3/agent/runner.py; walk four parents up
# to reach the repo root, then into config/. Operator can override via
# DP_HARNESS_POLICY.
#
# Sample/local split: ``config/agentic-policy.json.sample`` is the
# tracked template; operators copy it to ``config/agentic-policy.json``
# and edit locally. .gitignore covers the local copy. Resolver prefers
# the local copy if present, falls back to the sample so fresh
# checkouts work out of the box.
def _resolve_default_policy_path() -> str:
    config_dir = Path(__file__).resolve().parents[4] / "config"
    local = config_dir / "agentic-policy.json"
    sample = config_dir / "agentic-policy.json.sample"
    if local.is_file():
        return str(local)
    return str(sample)


_DEFAULT_POLICY_PATH = _resolve_default_policy_path()

# Heartbeat interval (seconds)
HEARTBEAT_INTERVAL = 5

# How long to wait between dsynth-lock polls when an env is busy.
DSYNTH_LOCK_POLL_SECONDS = 30


# =============================================================================
# State DB connection (for activity logging and runner status)
# =============================================================================

_state_db_conn: sqlite3.Connection | None = None
_state_db_lock = threading.Lock()

# --env NAME from the runner CLI. Trackerless / step-3-of-precedence
# fallback consulted by resolve_env(). main() sets this before any
# job is dispatched.
_CLI_ENV_DEFAULT: str | None = None


def resolve_env(job: dict | None) -> str | None:
    """Per-job env resolution. Wraps env_resolver.resolve_env_for_job
    with the runner's DB connection + CLI flag default.

    Returns the env name, or None if no env could be resolved (the
    caller surfaces "needs_env_selection" to the operator).
    """
    from dportsv3.agent.env_resolver import resolve_env_for_job  # noqa: PLC0415
    r = resolve_env_for_job(job, _state_db_conn, cli_env=_CLI_ENV_DEFAULT)
    return r.env


# Gate-cycle cache for resolve_env(None). The gate runs every poll
# (sub-second) and re-resolves to pick up tracker UI changes
# without a restart. The DB read is cheap but it adds up; a 1-second
# TTL keeps UI changes effectively-immediate while collapsing
# burst-rate polls onto a single read.
_GATE_RESOLVE_CACHE: tuple[float, str | None] | None = None
_GATE_RESOLVE_TTL_SECONDS: float = 1.0


def resolve_env_for_gate() -> str | None:
    """Cached resolve_env(None) for the runner's per-poll gate.

    Cache TTL is :data:`_GATE_RESOLVE_TTL_SECONDS`; UI changes take
    effect within that window. Job-dispatch paths still use the
    uncached :func:`resolve_env` so per-job semantics are exact.
    """
    global _GATE_RESOLVE_CACHE
    import time  # noqa: PLC0415
    now = time.monotonic()
    if _GATE_RESOLVE_CACHE is not None:
        cached_at, cached_val = _GATE_RESOLVE_CACHE
        if now - cached_at < _GATE_RESOLVE_TTL_SECONDS:
            return cached_val
    val = resolve_env(None)
    _GATE_RESOLVE_CACHE = (now, val)
    return val


def resolve_env_or_reason(job: dict | None):
    """Like resolve_env but returns the full EnvResolution so callers
    can surface refusal_reason in their error path."""
    from dportsv3.agent.env_resolver import resolve_env_for_job  # noqa: PLC0415
    return resolve_env_for_job(job, _state_db_conn, cli_env=_CLI_ENV_DEFAULT)
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


def stub_unprobed_envs() -> int:
    """Insert a placeholder ``env_health_status`` row for every env
    on disk that isn't already in the table.

    Without this, brand-new envs are invisible to the tracker UI
    (the dropdown sources from this table) until the runner's first
    health probe touches them. Stubbing on runner start closes the
    UI ↔ runner truth gap: any env the runner could auto-pick is
    also visible to the operator. Real probes overwrite the stub.

    Returns the number of stub rows inserted (0 if all envs were
    already in the table or no envs exist).
    """
    if _state_db_conn is None:
        return 0
    from dportsv3.agent.env_resolver import list_available_envs  # noqa: PLC0415
    envs = list_available_envs()
    if not envs:
        return 0
    ts = datetime.now(timezone.utc).isoformat()
    inserted = 0
    try:
        with _state_db_lock:
            for env in envs:
                # INSERT OR IGNORE: real probe rows (with status=ready /
                # degraded / broken) must not be overwritten by stubs.
                cur = _state_db_conn.execute(
                    """INSERT OR IGNORE INTO env_health_status
                       (env, status, probed_at, operator_action,
                        detail_json, updated_at)
                       VALUES (?, 'unprobed', NULL, NULL,
                               '{"checks":[]}', ?)""",
                    (env, ts),
                )
                if cur.rowcount:
                    inserted += 1
            _state_db_conn.commit()
    except Exception as exc:
        print(f"Warning: stub_unprobed_envs failed: {exc}", file=sys.stderr)
    return inserted


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
    """Initialize connection to state.db, creating + schema-initing the
    file if it doesn't exist yet.

    A first-time runner on a clean host (or after a wipe) must not
    silently disable all DB writes — that produces a runner.log full
    of activity that the tracker UI never sees. Auto-create matches
    artifact-store's behaviour: the schema is idempotent
    (``init_db`` uses CREATE TABLE IF NOT EXISTS + ADD COLUMN
    migrations), so it's safe to run on every startup.

    Returns ``None`` only when the parent dir is missing or
    ``sqlite3.connect`` itself raises — those are real misconfigs the
    operator must fix.
    """
    global _state_db_conn

    db_path = get_state_db_path(queue_root)

    parent = db_path.parent
    if not parent.exists():
        print(
            f"Warning: state.db parent dir {parent} does not exist; "
            "runner lifecycle/status writes disabled. Create the "
            "directory or set DPORTSV3_STATE_DB to a valid path.",
            file=sys.stderr,
        )
        return None

    created = not db_path.exists()
    try:
        conn = sqlite3.connect(str(db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
    except Exception as exc:
        print(f"Warning: could not connect to state.db at {db_path}: "
              f"{exc}", file=sys.stderr)
        return None

    # Always run init_db — it's idempotent and applies any pending
    # ALTER TABLE migrations. Cheap, and protects against running
    # against a partially-migrated DB.
    try:
        from dportsv3.db.schema import init_db as _init_schema  # noqa: PLC0415
        _init_schema(conn)
    except Exception as exc:
        print(f"Warning: schema init on state.db at {db_path} failed: "
              f"{exc}; runner lifecycle/status writes disabled",
              file=sys.stderr)
        try:
            conn.close()
        except Exception:
            pass
        return None

    if created:
        print(f"Initialized new state.db at {db_path}", file=sys.stderr)

    _state_db_conn = conn
    return conn


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


def _lookup_bundle_target(bundle_id: str | None) -> str:
    """Return ``bundles.target`` for ``bundle_id`` or ``""``.

    Used as the canonical fallback when an enqueue path doesn't carry
    its own target: a triage/patch job for a known bundle should
    inherit the bundle's target. Without this, jobs created under a
    runner whose ``DPORTSV3_TRACKER_TARGET`` env var is unset land
    with ``target=NULL`` while the bundle has the real value, and
    the tracker UI's per-port aggregates (``token_usage_for_port``)
    join-out to zero rows.
    """
    if not bundle_id or _state_db_conn is None:
        return ""
    try:
        with _state_db_lock:
            row = _state_db_conn.execute(
                "SELECT target FROM bundles WHERE bundle_id = ?",
                (bundle_id,),
            ).fetchone()
    except Exception:
        return ""
    if row is None:
        return ""
    val = row["target"] if hasattr(row, "keys") else row[0]
    return val or ""


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

    If ``metadata["target"]`` is empty/missing but ``metadata["bundle_id"]``
    is set, resolves the target via ``_lookup_bundle_target`` so the
    job inherits the bundle's target instead of landing NULL.
    """
    from dportsv3.agent import lifecycle  # type: ignore[import-not-found]

    if _state_db_conn is None:
        return False
    target = metadata.get("target") or ""
    if not target:
        target = _lookup_bundle_target(metadata.get("bundle_id"))
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
                       bundle_id = COALESCE(?, bundle_id),
                       created_ts_utc = COALESCE(?, created_ts_utc),
                       path = COALESCE(?, path),
                       target = COALESCE(NULLIF(?, ''), target),
                       last_seen_at = ?
                   WHERE job_id = ?""",
                (
                    metadata.get("type"),
                    metadata.get("origin"),
                    metadata.get("flavor"),
                    metadata.get("bundle_dir"),
                    metadata.get("bundle_id"),
                    metadata.get("created_ts_utc"),
                    metadata.get("path"),
                    target,
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


def _compute_error_signature(text: str | None) -> str | None:
    """Return a stable short hex digest for the first non-empty line of
    ``logs/errors.txt``. Used by Step 6's sticky-signature retry-cap
    check. ``None`` if the text is missing/empty.

    Stable across runs because the hook truncates errors.txt at 200KB
    and writes deterministic content. The first non-empty line is
    typically ``cc: error: ...`` or ``[hook] ports/...: build failed``;
    same root cause → same line → same signature.
    """
    if not text:
        return None
    import hashlib  # noqa: PLC0415 — local import, only on first triage
    for raw in text.splitlines():
        line = raw.strip()
        if line:
            return hashlib.sha256(line.encode("utf-8", errors="replace"))\
                .hexdigest()[:16]
    return None


def _ensure_recent_signatures(target: str, origin: str, window_hours: int) -> None:
    """Backfill ``bundles.error_signature`` for recent same-origin
    failure bundles whose signature is still NULL.

    Lazy population so that Step 6's sticky-signature check has data
    to work with. Idempotent (UPDATE only runs against NULL rows).
    Failures are swallowed — sticky-signature simply doesn't fire if
    we couldn't read the artifact.
    """
    if _state_db_conn is None or not origin:
        return
    from datetime import datetime, timedelta, timezone  # noqa: PLC0415
    cutoff = (
        datetime.now(timezone.utc) - timedelta(hours=max(0, int(window_hours)))
    ).isoformat()
    try:
        with _state_db_lock:
            rows = _state_db_conn.execute(
                """SELECT bundle_id FROM bundles
                   WHERE origin = ? AND result IN ('failure', 'fail')
                     AND error_signature IS NULL
                     AND last_seen_at >= ?
                     AND (target = ?
                          OR (? = '' AND (target IS NULL OR target = '')))""",
                (origin, cutoff, target, target),
            ).fetchall()
    except Exception:
        return
    for row in rows:
        bundle_id = row["bundle_id"] if hasattr(row, "keys") else row[0]
        text = read_bundle_text(None, bundle_id, "logs/errors.txt")
        sig = _compute_error_signature(text)
        if not sig:
            continue
        try:
            with _state_db_lock:
                _state_db_conn.execute(
                    "UPDATE bundles SET error_signature = ? WHERE bundle_id = ?",
                    (sig, bundle_id),
                )
                _state_db_conn.commit()
        except Exception:
            continue


def _load_port_history(target: str, origin: str, window_hours: int):
    """Thin lock-wrapper over PortHistory.load using the runner DB.

    The decision engine's ``PortHistory.load`` does the SQL; this
    helper just supplies ``_state_db_conn`` under ``_state_db_lock``
    so callers don't have to import sqlite3 or know about the lock.

    Backfills error_signature for recent failure bundles first so the
    sticky-signature retry-cap check has data to operate on.
    """
    from dportsv3.agent.decision import PortHistory

    if _state_db_conn is None or not origin:
        return PortHistory.empty(target=target or "", origin=origin or "")
    _ensure_recent_signatures(target or "", origin, window_hours)
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

    target = (
        _lookup_bundle_target(bundle_id)
        or os.environ.get("DPORTSV3_TRACKER_TARGET", "")
    )

    content = [
        "type=triage",
        f"created_ts_utc={ts}",
        f"profile={profile}",
        f"target={target}",
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
            "target": target,
            "bundle_id": bundle_id,
        },
    )
    return job_path


def process_verify_requests(queue_root: Path) -> None:
    """Reconcile operator-triggered verify requests (Step 11c
    layer-violation cleanup).

    The tracker's ``POST /api/bundles/{id}/verify`` writes a row to
    ``verify_requests`` and returns immediately. This loop scans for
    ``status='pending'`` rows, fetches the bundle metadata from the
    shared state.db, calls :func:`enqueue_verify_job`, and marks the
    request ``enqueued`` (or ``failed`` if enqueue raised). Mirrors
    the ``process_user_context_updates`` shape.

    The runner is the only process that touches the queue
    filesystem, restoring the tracker-is-read-only-for-queue
    invariant. Remote runners (Step 17) can serve this poll over
    the shared state.db without any tracker changes.
    """
    if _state_db_conn is None:
        return
    try:
        with _state_db_lock:
            rows = _state_db_conn.execute(
                """SELECT id, bundle_id, env, requested_by
                   FROM verify_requests
                   WHERE status = 'pending'
                   ORDER BY requested_at ASC"""
            ).fetchall()
    except sqlite3.Error as exc:
        log(queue_root, "WARN",
            f"verify_requests scan failed: {exc}")
        return
    for row in rows:
        req_id = row["id"] if hasattr(row, "keys") else row[0]
        bundle_id = row["bundle_id"] if hasattr(row, "keys") else row[1]
        env = row["env"] if hasattr(row, "keys") else row[2]
        requested_by = (row["requested_by"]
                        if hasattr(row, "keys") else row[3]) or "operator"
        # Look up bundle to get origin + target.
        try:
            with _state_db_lock:
                brow = _state_db_conn.execute(
                    "SELECT origin, target FROM bundles WHERE bundle_id = ?",
                    (bundle_id,),
                ).fetchone()
        except sqlite3.Error as exc:
            log(queue_root, "WARN",
                f"verify request {req_id}: bundle lookup failed: {exc}")
            continue
        if brow is None:
            _mark_verify_request(req_id, "failed",
                                 error=f"bundle {bundle_id} not found")
            continue
        origin = brow["origin"] if hasattr(brow, "keys") else brow[0]
        target = (brow["target"] if hasattr(brow, "keys") else brow[1]) or ""
        try:
            job_path = enqueue_verify_job(
                queue_root, bundle_id=bundle_id, origin=origin,
                target=target, env=env, requested_by=requested_by,
            )
        except Exception as exc:
            log(queue_root, "WARN",
                f"verify request {req_id} enqueue failed: {exc}")
            _mark_verify_request(req_id, "failed", error=str(exc)[:500])
            continue
        _mark_verify_request(req_id, "enqueued", job_id=job_path.name)
        activity_log(
            queue_root, "verify_enqueued_from_request",
            f"verify request {req_id} for {bundle_id} → {job_path.name}",
            job_id=job_path.name,
            extra={"request_id": req_id, "bundle_id": bundle_id,
                   "env": env},
        )


def _mark_verify_request(
    req_id: int, status: str, *,
    job_id: str | None = None, error: str | None = None,
) -> None:
    if _state_db_conn is None:
        return
    try:
        with _state_db_lock:
            _state_db_conn.execute(
                """UPDATE verify_requests
                   SET status = ?, job_id = COALESCE(?, job_id),
                       error = COALESCE(?, error)
                   WHERE id = ?""",
                (status, job_id, error, req_id),
            )
            _state_db_conn.commit()
    except sqlite3.Error:
        pass


# Job states that count as "active work in progress for this origin"
# for the operator-retriage duplicate-enqueue guard
# (`_has_active_same_origin_job`). Canonical definition + the full
# rationale for why TRIAGED is excluded (and how this differs from
# the reap-orphans set) live in lifecycle.ACTIVE_WORK_STATES.
_ACTIVE_JOB_STATES = ACTIVE_WORK_STATE_VALUES


def _has_active_same_origin_job(run_id: str, origin: str) -> str | None:
    """Return the job_id of an active same-(run_id, origin) job, or None.

    "Active" matches the JobState values that aren't terminal — anything
    upstream of DONE/DEAD/ESCALATED. Used as a duplicate-enqueue guard
    on operator-triggered retries: we should not enqueue a new triage
    while one is already in flight for the same port in the same run.
    """
    if _state_db_conn is None:
        return None
    placeholders = ",".join("?" for _ in _ACTIVE_JOB_STATES)
    try:
        with _state_db_lock:
            row = _state_db_conn.execute(
                f"""SELECT job_id FROM jobs
                    WHERE origin = ? AND state IN ({placeholders})
                      AND (
                        ? = '' OR target IS NULL OR target = ''
                        OR target = (SELECT target FROM runs WHERE run_id = ?)
                      )
                    ORDER BY last_seen_at DESC LIMIT 1""",
                (origin, *_ACTIVE_JOB_STATES, run_id, run_id),
            ).fetchone()
    except Exception:
        return None
    return row["job_id"] if row else None


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
            # Guard: don't enqueue a duplicate while same-origin work is
            # already in flight. The next sweep will re-evaluate; the
            # request row stays 'pending' so it isn't lost.
            blocker = _has_active_same_origin_job(run_id, origin)
            if blocker:
                activity_log(
                    queue_root, "retriage_blocked",
                    f"Skipping retriage for {origin}: active job {blocker}",
                    extra={"run_id": run_id, "origin": origin,
                           "blocking_job_id": blocker,
                           "context_rev": context_rev},
                )
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
                # Step 28c: clear the transient retry_requested
                # resolution on the originating bundle. Without this,
                # a bundle whose retry was honored would stay at
                # retry_requested forever — the new triage runs on
                # the same bundle and will set the resolution via
                # _EVENT_TO_RESOLUTION on its own terminal events,
                # but only if it reaches one. Clearing here also
                # serves as the observability signal "the runner
                # picked up the operator's request."
                _state_db_conn.execute(
                    """UPDATE bundles
                       SET resolution = NULL
                       WHERE bundle_id = ?
                         AND resolution = 'retry_requested'""",
                    (row["bundle_id"],),
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


# find_kedb_dir / load_kedb retired in Step 27b. The replacements
# live in dportsv3.agent.playbooks (find_playbooks_dir,
# load_playbooks) and are imported lazily at call sites to keep
# this module's import surface stable.
#
# Alpha-mode hard cutover: no compatibility shims. The 4 existing
# entries (error-*.md) were retrofitted with explicit triggers; the
# selector's flows-default ([triage, patch]) covers them anyway.


def queue_root_for_log(job: dict | None) -> Path | None:
    """Best-effort queue_root extraction for telemetry from any
    payload-building context. Returns None silently if absent (the
    activity_log call accepts None and downgrades to a no-op)."""
    if not job:
        return None
    qr = job.get("queue_root")
    return Path(qr) if qr else None


def _log_playbook_selection(queue_root, role, origin, selection,
                            job_id: str | None = None):
    """Emit a `playbooks_selected` activity row with included +
    skipped counts so operators see WHY their entry didn't fire.

    ``job_id`` is required for the row to appear under the per-job
    activity query (`tracker get-activity --job ID`). Rows written
    with ``job_id=None`` land in the table with a NULL job_id and
    are invisible to that query — silently swallowing the Step-27
    telemetry signal.

    Best-effort: any failure (no queue_root, write error) silently
    no-ops — telemetry must not break payload assembly.
    """
    if queue_root is None:
        return
    try:
        activity_log(
            queue_root, "playbooks_selected",
            (
                f"{origin or '?'}: role={role} "
                f"included={len(selection.included)} "
                f"skipped={len(selection.skipped)}"
            ),
            job_id=job_id,
            extra={
                "role": role,
                "origin": origin,
                "included": list(selection.included),
                "skipped_count": len(selection.skipped),
                # Cap skipped reasons to avoid bloating the log.
                "skipped_sample": [
                    {"file": f, "reason": r}
                    for f, r in selection.skipped[:8]
                ],
            },
        )
    except Exception:
        pass


# -----------------------------------------------------------------------------
# Triage parsing
# -----------------------------------------------------------------------------
#
# Step 36-7 cutover: ``parse_triage_output`` (regex extraction of
# Classification / Confidence from ``analysis/triage.md``) is gone.
# The patch consumers (``build_patch_payload`` and
# ``steps.PatchAttemptStep.precheck``) now load the typed
# ``TriageResult`` via :func:`dportsv3.agent.phase_result.load_phase_result`
# directly from ``analysis/triage_result.json`` instead of regex-fishing
# fields out of prose.



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
    playbooks_dir: Path | None = None,
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
    # Step 29e: load every operator round so UserContextSection
    # can render history, not just the latest overwrite. The
    # current ``user_context_text`` is kept for the empty-history
    # fallback path (pre-29b submissions, test seeds).
    user_context_history = _load_operator_context_history(run_id, origin)
    # Triage runs BEFORE classification is known — we attach
    # entries that don't require a classification (the entry's
    # `triggers.classifications` is empty / wildcard) or whose
    # triggers don't depend on patch-flow context. Toolchain
    # detection (Step 19a / 27f) feeds `toolchains` so
    # toolchain-*.md playbooks fire for ports whose framework
    # Makefile carries recognizable signals (USES=, GNU_CONFIGURE=).
    from dportsv3.agent.playbooks import (  # noqa: PLC0415
        detect_toolchains, load_playbooks,
    )
    detected_toolchains = detect_toolchains(
        bundle_dir / "port" if bundle_dir else None,
    )
    playbook_selection = load_playbooks(
        playbooks_dir, role="triage", classification=None,
        toolchains=detected_toolchains,
    )
    _log_playbook_selection(queue_root_for_log(job), "triage", origin,
                            playbook_selection,
                            job_id=job.get("job_id"))

    ctx = ContextCtx(
        bundle_dir=bundle_dir,
        bundle_id=bundle_id,
        job=job,
        playbooks_dir=playbooks_dir,
        sibling_bundle_ids=sibling_ids,
        prior_triage_bundle_ids=prior_triage_ids,
        user_context_text=user_context_text or None,
        user_context_history=user_context_history,
        playbooks_text=playbook_selection.text or None,
        read_bundle_text=read_bundle_text,
        bundle_artifact_list=bundle_artifact_list,
        snippet_feedback=build_snippet_feedback,
        snippet_content=load_snippets_content,
    )
    return render_payload(list(TRIAGE_SECTIONS), ctx)


def build_patch_payload(
    bundle_dir: Path | None,
    playbooks_dir: Path | None = None,
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
    # Step 29e: same history wiring as triage — patch flow also
    # benefits from seeing every operator round, not just the
    # latest overwrite.
    user_context_history = _load_operator_context_history(run_id, origin)
    # Patch flow: classification is known from the prior triage in
    # this bundle. Step 36-5 reads it from the typed
    # ``TriageResult`` written by ``_write_triage_audit_harness``
    # rather than regex-parsing ``analysis/triage.md``.
    triage_classification: str | None = None
    try:
        from dportsv3.agent.phase_result import (  # noqa: PLC0415
            TriageResult, load_phase_result,
        )
        triage = load_phase_result(
            bundle_dir, bundle_id, "triage", TriageResult,
        )
        if triage is not None:
            triage_classification = triage.classification or None
    except Exception:
        # Best-effort: a stale on-disk shape (PhaseResultVersionMismatch)
        # or a missing artifact degrades to "no classification" — the
        # playbook selector falls back to its default set.
        triage_classification = None
    from dportsv3.agent.playbooks import (  # noqa: PLC0415
        detect_toolchains, load_playbooks,
    )
    detected_toolchains = detect_toolchains(
        bundle_dir / "port" if bundle_dir else None,
    )
    playbook_selection = load_playbooks(
        playbooks_dir, role="patch", classification=triage_classification,
        toolchains=detected_toolchains,
    )
    _log_playbook_selection(queue_root_for_log(job), "patch", origin,
                            playbook_selection,
                            job_id=job.get("job_id"))

    ctx = ContextCtx(
        bundle_dir=bundle_dir,
        bundle_id=bundle_id,
        job=job,
        playbooks_dir=playbooks_dir,
        sibling_bundle_ids=sibling_ids,
        prior_patch_bundle_ids=prior_patch_ids,
        user_context_text=user_context_text or None,
        user_context_history=user_context_history,
        playbooks_text=playbook_selection.text or None,
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
    flow should operate against; omit to let env_resolver pick
    (tracker active env → --env CLI flag → auto-pick).
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

    bundle_id = job.get("bundle_id", "")
    target = (
        job.get("target")
        or _lookup_bundle_target(bundle_id)
        or os.environ.get("DPORTSV3_TRACKER_TARGET", "")
    )

    content = [
        f"type=patch",
        f"created_ts_utc={ts}",
        f"profile={job.get('profile', '')}",
        f"target={target}",
        f"origin={job.get('origin', '')}",
        f"flavor={job.get('flavor', '')}",
        f"bundle_id={bundle_id}",
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
            "target": target,
            "bundle_id": bundle_id,
        },
    )
    return job_path


def _resume_deferred_triage(
    queue_root: Path, convert_job_id: str, origin: str, target: str,
) -> str | None:
    """After a convert job succeeds, re-enqueue the triage that was
    deferred for it (Step 20d auto-resume).

    Finds the most-recent dead triage with retire_reason
    ``deferred_for_convert`` for the same (origin, target), reads
    its original .job file from ``done/`` for the metadata
    (bundle_id, run_id, profile, flavor, iteration, ...), and
    enqueues a fresh triage. The new triage runs against the
    now-converted port, so its own ``_maybe_defer_to_convert``
    classify check returns ``converted`` and triage proceeds
    normally.

    Returns the new triage's job_id, or None if no deferred
    triage was found (or its job file is missing).
    """
    global _state_db_conn
    if _state_db_conn is None:
        return None
    try:
        row = _state_db_conn.execute(
            """SELECT job_id FROM jobs
               WHERE type = 'triage'
                 AND state = 'dead'
                 AND retire_reason = 'deferred_for_convert'
                 AND origin = ?
                 AND (target = ?
                      OR (? = '' AND (target IS NULL OR target = '')))
               ORDER BY last_seen_at DESC LIMIT 1""",
            (origin, target, target),
        ).fetchone()
    except sqlite3.Error as exc:
        log(queue_root, "WARN", f"resume_deferred_triage db query failed: {exc}")
        return None
    if row is None:
        return None
    dead_triage_id = row[0] if not hasattr(row, "keys") else row["job_id"]

    # The original .job file was moved to done/ by the dispatcher's
    # success path (defer returned (True, ...) so the file was treated
    # as a normal successful triage).
    done_path = queue_root / "done" / dead_triage_id
    if not done_path.exists():
        log(queue_root, "WARN",
            f"deferred triage {dead_triage_id} job file missing under "
            f"{queue_root / 'done'}; cannot auto-resume")
        return None
    try:
        meta = parse_job_file(done_path)
    except Exception as exc:
        log(queue_root, "WARN",
            f"could not parse deferred triage {dead_triage_id}: {exc}")
        return None

    try:
        new_path = enqueue_triage_job(
            queue_root,
            bundle_id=meta.get("bundle_id", ""),
            run_id=meta.get("run_id", ""),
            origin=meta.get("origin", origin),
            profile=meta.get("profile", ""),
            flavor=meta.get("flavor", ""),
            iteration=int(meta.get("iteration", "1") or "1"),
            max_iterations=int(
                meta.get("max_iterations", str(DEFAULT_MAX_ITERATIONS))
                or str(DEFAULT_MAX_ITERATIONS)
            ),
            previous_bundle=meta.get("previous_bundle") or None,
            context_rev=int(meta.get("user_context_rev", "0") or "0"),
        )
    except Exception as exc:
        log(queue_root, "WARN", f"failed to enqueue resumed triage: {exc}")
        return None

    log(queue_root, "INFO",
        f"auto-resumed triage as {new_path.name} after convert "
        f"{convert_job_id} (origin={origin})")
    try:
        activity_log(
            queue_root, "triage_resumed_after_convert",
            f"resumed as {new_path.name} after convert {convert_job_id}",
            job_id=new_path.name,
            extra={
                "original_triage_id": dead_triage_id,
                "convert_job_id": convert_job_id,
            },
        )
    except Exception as exc:
        log(queue_root, "WARN", f"activity_log failed in resume: {exc}")
    return new_path.name


def _bundle_convert_succeeded(bundle_id: str | None) -> str | None:
    """Return job_id of a convert for THIS bundle that reached DONE, else None.

    Circuit breaker for `_maybe_defer_to_convert`: if this bundle already
    had a convert succeed yet classify still says the substrate needs
    conversion, the defer→convert→resume cycle is stuck for this episode
    (convert can't move the port out of `auto_safe_pending`). Refusing to
    re-defer breaks the loop.

    Scoped to ``bundle_id``, NOT to (origin, target) within a time window:
    the loop the breaker guards is intra-bundle. A port-scoped time window
    false-positives when a *second* failure bundle for the same port lands
    inside the window — it matches the neighbor bundle's convert and
    suppresses a convert this bundle legitimately needs, sending an
    unconverted port straight to a patch flow whose every edit is
    substrate-gated. A bundle has one origin and one target, so bundle_id
    alone identifies the episode.
    """
    global _state_db_conn
    if _state_db_conn is None or not bundle_id:
        return None
    try:
        row = _state_db_conn.execute(
            """SELECT job_id FROM jobs
               WHERE type = 'convert'
                 AND state = 'done'
                 AND bundle_id = ?
               ORDER BY last_seen_at DESC LIMIT 1""",
            (bundle_id,),
        ).fetchone()
    except sqlite3.Error:
        return None
    if row is None:
        return None
    return row[0] if not hasattr(row, "keys") else row["job_id"]


def _find_active_convert_job(origin: str, target: str) -> str | None:
    """Return job_id of an open convert job for (origin, target), if any.

    Step 20d: triage consults this before enqueuing a new convert
    job, so two triage attempts on the same failing port don't each
    spin up their own convert and double-bill tokens.
    """
    global _state_db_conn
    if _state_db_conn is None:
        return None
    try:
        row = _state_db_conn.execute(
            """SELECT job_id FROM jobs
               WHERE type = 'convert'
                 AND origin = ?
                 AND (target = ? OR (? = '' AND (target IS NULL OR target = '')))
                 AND state IN ('queued','claimed','converting')
               ORDER BY created_ts_utc DESC LIMIT 1""",
            (origin, target, target),
        ).fetchone()
    except sqlite3.Error:
        return None
    if row is None:
        return None
    return row[0] if not hasattr(row, "keys") else row["job_id"]


def enqueue_convert_job(
    queue_root: Path,
    *,
    origin: str,
    target: str,
    profile: str = "",
    requested_by: str = "operator",
    dev_env: str | None = None,
    bundle_dir: str | None = None,
    bundle_id: str | None = None,
) -> Path:
    """Enqueue a dops-conversion job for one port (Step 20c).

    Convert jobs can be port-level (operator-fired against an origin
    with no failure) or bundle-tied (triage enqueues one when a
    failure bundle's port has dops_state=needs_judgment). The
    bundle_id is propagated only in the latter case so the
    bundles↔jobs FK reflects the actual relation; operator-fired
    converts leave it NULL.

    Step 36-6: no triage-context fields are added to the .job file.
    When the convert is bundle-tied, the originating triage's typed
    ``TriageResult`` is already addressable via
    ``analysis/triage_result.json`` on the same bundle. The convert
    flow reads it directly inside ``_run_llm_conversion`` via
    ``load_phase_result(bundle_dir, bundle_id, "triage", TriageResult)``;
    operator-fired converts (no bundle_id) skip the lookup and the
    payload renders without the "Original build failure" section.
    """
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%SZ")
    origin_safe = origin.replace("/", "_")
    pid = os.getpid()
    job_name = f"{ts}-{profile or 'any'}-{origin_safe}-{pid}-convert.job"

    pending_dir = queue_root / "pending"
    job_path = pending_dir / job_name

    content = [
        "type=convert",
        f"created_ts_utc={ts}",
        f"profile={profile}",
        f"origin={origin}",
        f"target={target}",
        f"requested_by={requested_by}",
    ]
    if dev_env:
        content.append(f"dev_env={dev_env}")
    if bundle_dir:
        # Propagate from the triage job that enqueued us so the
        # convert job's audit (e.g. the commit_port_changes
        # message) can reference the originating bundle.
        content.append(f"bundle_dir={bundle_dir}")
    if bundle_id:
        content.append(f"bundle_id={bundle_id}")

    tmp_path = job_path.with_suffix(".tmp")
    with open(tmp_path, "w") as f:
        f.write("\n".join(content) + "\n")
    tmp_path.rename(job_path)

    _register_new_job(
        job_path.name,
        metadata={
            "type": "convert",
            "origin": origin,
            "flavor": "",
            "created_ts_utc": ts,
            "path": str(job_path),
            "target": target
                or os.environ.get("DPORTSV3_TRACKER_TARGET", ""),
            "bundle_dir": bundle_dir,
            "bundle_id": bundle_id,
        },
    )
    return job_path


def enqueue_verify_job(
    queue_root: Path,
    *,
    bundle_id: str,
    origin: str,
    target: str,
    env: str,
    requested_by: str = "operator",
) -> Path:
    """Enqueue a fix-verification job (Step 11c).

    Verify jobs are operator-triggered: the bundle UI's Verify
    button POSTs to a tracker endpoint which calls this. The
    runner's dispatch arm picks the job up and calls
    ``dportsv3.verify_fix.run_verify_fix`` in-process — no
    subprocess, no shell-out.

    Carries ``bundle_id`` so the in-process call has what it
    needs; ``env`` is the operator-chosen dev-env name.
    """
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%SZ")
    origin_safe = origin.replace("/", "_")
    pid = os.getpid()
    job_name = f"{ts}-{origin_safe}-{pid}-verify.job"

    pending_dir = queue_root / "pending"
    job_path = pending_dir / job_name

    content = [
        "type=verify",
        f"created_ts_utc={ts}",
        f"bundle_id={bundle_id}",
        f"origin={origin}",
        f"target={target}",
        f"dev_env={env}",
        f"requested_by={requested_by}",
    ]

    tmp_path = job_path.with_suffix(".tmp")
    with open(tmp_path, "w") as f:
        f.write("\n".join(content) + "\n")
    tmp_path.rename(job_path)

    _register_new_job(
        job_path.name,
        metadata={
            "type": "verify",
            "origin": origin,
            "flavor": "",
            "created_ts_utc": ts,
            "path": str(job_path),
            "target": target,
            "bundle_id": bundle_id,
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
    """Step 36-2: write the typed ``TriageResult`` to the bundle.

    The markdown response is already on disk (``triage.run`` writes
    ``analysis/triage.md`` after each LLM round). This adds the
    canonical typed result at ``analysis/triage_result.json`` —
    classification + confidence + root_cause + evidence_excerpt +
    error_signature + tier + token spend + model.

    Replaces the pre-Step-36 ``analysis/triage.json`` audit shape; the
    one downstream consumer (``proposed_fix.build_proposed_fix_ctx``)
    is updated to read the new relpath in the same step.
    """
    from dataclasses import asdict  # noqa: PLC0415
    from dportsv3.agent.markdown import md_section  # noqa: PLC0415
    from dportsv3.agent.phase_result import (  # noqa: PLC0415
        TriageResult, write_phase_result,
    )
    from dportsv3.agent.policy import (  # noqa: PLC0415
        load_policy, tier_for as policy_tier_for,
    )

    # Re-read the markdown the agent just wrote to lift Root Cause +
    # Evidence into the typed result. Same source-of-truth the
    # delivery PR-body builder uses (and the same md_section helper),
    # so they stay in lockstep on prose-section conventions.
    triage_md = read_bundle_text(
        bundle_dir, bundle_id, "analysis/triage.md",
    ) or ""
    errors_text = read_bundle_text(
        bundle_dir, bundle_id, "logs/errors.txt",
    )

    tier_name = "MANUAL"
    try:
        policy_path = os.environ.get(
            "DP_HARNESS_POLICY", _DEFAULT_POLICY_PATH,
        )
        pol = load_policy(policy_path)
        tier_name = policy_tier_for(
            pol, result.classification, result.confidence,
        ).name
    except Exception:
        # Tier resolution is a derived field; persisting MANUAL on
        # failure is a safe default (operator-only) and keeps the
        # write path side-effect-free.
        pass

    triage_result = TriageResult(
        classification=result.classification,
        confidence=result.confidence,
        root_cause=md_section(triage_md, "Root Cause", max_chars=2000),
        evidence_excerpt=md_section(
            triage_md, "Evidence", max_chars=2000,
        ),
        error_signature=_compute_error_signature(errors_text),
        tier=tier_name,
        classifier_version="triage-v1",
        tokens_prompt=result.usage.prompt_tokens,
        tokens_completion=result.usage.completion_tokens,
        tokens_total=result.usage.total_tokens,
        model=model,
    )

    if bundle_id:
        write_phase_result(bundle_id, "triage", triage_result)
        return
    # bundle_dir-only fallback (legacy / offline-test path). The
    # phase_result write helper only routes through the artifact
    # store; for the dir-only mode we serialize directly to disk
    # using the same shape so future loads off the dir would match.
    if bundle_dir is None:
        raise RuntimeError("bundle_dir or bundle_id required")
    data = (
        json.dumps(asdict(triage_result), indent=2) + "\n"
    ).encode("utf-8")
    out = bundle_dir / "analysis" / "triage_result.json"
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

    def _sibling_detail(s: Path, base_detail: dict) -> dict:
        """Per-sibling detail dict carrying its OWN bundle_id so the
        resolution propagates to the right bundles row."""
        out = dict(base_detail) if base_detail else {}
        try:
            out["bundle_id"] = parse_job_file(s).get("bundle_id", "") or None
        except Exception:
            pass
        return out

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
                sd = _sibling_detail(s, detail)
                for evt in events:
                    _apply_transition(s.name, evt, detail=sd)
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
            _apply_transition(s.name, evt,
                              detail=_sibling_detail(s, {"reason": reason}))
        return False, reason

    outcome = step_result.outcome
    # Sibling fan-out: same events the orchestrator fired for the lead.
    sibling_events = outcome_events(outcome)
    detail = outcome.detail or {}
    for s in sibling_paths:
        sd = _sibling_detail(s, detail)
        for evt in sibling_events:
            _apply_transition(s.name, evt, detail=sd)

    status_str = outcome.detail.get("status_str", "unknown")
    if outcome.status == "failed":
        return False, status_str
    return True, status_str


def _drop_bundle_branch_for_job(
    *,
    queue_root: Path,
    job_id: str,
    env: str | None,
    bundle_id: str | None,
    job_type: str,
    reason: str,
) -> None:
    """Step 30 slice 4: drop the bundle's branch at terminal job
    end. Called from the dispatch after the job's
    success/failure verdict is known.

    Lifecycle: convert success keeps the branch (the next patch
    job for the same bundle_id will reuse it). Convert failure,
    patch end (either outcome), and verify end all drop. The
    delivery.diff (slice 2) has already been captured by the time
    we reach the drop, so the branch's purpose is done.

    Bundles that never run a follow-up job (operator-fired
    convert, MANUAL-escalated retriage) keep their branch until
    the env itself is rebuilt — the "stale branch" case is
    explicitly out of scope per the design discussion.

    Soft-fail: drop failures are logged but never propagate. The
    next bundle's branch creation will tolerate the existence of
    a leftover branch (Slice 1 ``checkout_bundle_branch`` uses
    ``checkout`` on an existing branch).
    """
    if not env or not bundle_id:
        return
    from dportsv3.agent import worker  # noqa: PLC0415
    try:
        result = worker.drop_bundle_branch(env, bundle_id)
    except Exception as exc:
        try:
            activity_log(
                queue_root, "bundle_branch_drop_failed",
                f"{job_type} {job_id}: drop raised: {exc!s}"[:240],
                job_id=job_id,
                extra={"bundle_id": bundle_id, "env": env,
                       "reason": reason,
                       "exception": str(exc)[:300]},
            )
        except Exception:
            pass
        return
    if not result.get("ok"):
        try:
            activity_log(
                queue_root, "bundle_branch_drop_failed",
                (f"{job_type} {job_id}: "
                 f"{result.get('error') or 'drop failed'}")[:240],
                job_id=job_id,
                extra={"bundle_id": bundle_id, "env": env,
                       "reason": reason,
                       "branch": result.get("branch")},
            )
        except Exception:
            pass
        return
    if result.get("removed"):
        try:
            activity_log(
                queue_root, "bundle_branch_dropped",
                f"{job_type} {job_id}: dropped {result.get('branch')} "
                f"({reason})",
                job_id=job_id,
                extra={"bundle_id": bundle_id, "env": env,
                       "branch": result.get("branch"),
                       "reason": reason},
            )
        except Exception:
            pass


def _checkout_bundle_branch_for_job(
    *,
    queue_root: Path,
    job_id: str,
    env: str | None,
    bundle_id: str | None,
    job_type: str,
) -> None:
    """Step 30 slice 1: ensure the env is checked out on this
    bundle's dedicated branch before any worker.* call touches the
    substrate. Called from process_convert_job, process_patch_job,
    and the verify dispatch.

    Soft-fail by design: if the checkout itself fails (env doesn't
    exist, git unavailable, subprocess raises), the job proceeds
    anyway and falls back to pre-Step-30 behavior (commits land on
    whatever branch is current, changes.diff is HEAD-relative).
    The activity row makes the lost-isolation visible so an operator
    can spot it; failing the job hard would regress a class of
    bundles that have already passed triage and would otherwise
    run fine.

    No-op when env or bundle_id is missing (e.g. triage jobs that
    don't touch the substrate).
    """
    if not env or not bundle_id:
        return
    from dportsv3.agent import worker  # noqa: PLC0415
    try:
        result = worker.checkout_bundle_branch(env, bundle_id)
    except Exception as exc:
        try:
            activity_log(
                queue_root, "bundle_branch_checkout_failed",
                f"{job_type} {job_id}: checkout raised: {exc!s}"[:240],
                job_id=job_id,
                extra={"bundle_id": bundle_id, "env": env,
                       "exception": str(exc)[:300]},
            )
        except Exception:
            pass
        return
    if not result.get("ok"):
        try:
            activity_log(
                queue_root, "bundle_branch_checkout_failed",
                (f"{job_type} {job_id}: "
                 f"{result.get('error') or 'checkout failed'}")[:240],
                job_id=job_id,
                extra={"bundle_id": bundle_id, "env": env,
                       "branch": result.get("branch"),
                       "base": result.get("base"),
                       "stderr_tail": result.get("stderr_tail", "")[:300]},
            )
        except Exception:
            pass
        return
    # Success row. Only emit when we actually did work (created
    # the branch, or switched onto an existing one) — the "already
    # current" no-op path would otherwise flood the activity log on
    # the convert → retriage → patch chain.
    if result.get("created") or not result.get("reused"):
        try:
            activity_log(
                queue_root, "bundle_branch_checkout",
                (f"{job_type} {job_id}: "
                 f"{'created' if result.get('created') else 'switched to'} "
                 f"{result.get('branch')} (base={result.get('base')})"),
                job_id=job_id,
                extra={"bundle_id": bundle_id, "env": env,
                       "branch": result.get("branch"),
                       "base": result.get("base"),
                       "created": bool(result.get("created"))},
            )
        except Exception:
            pass


def _checkout_verify_branch_for_job(
    *,
    queue_root: Path,
    job_id: str,
    env: str | None,
    bundle_id: str | None,
) -> tuple[bool, str | None]:
    """Put the env on a throwaway ``bundle/<id>-verify`` branch cut
    from base for the verify run.

    Verify uses its own branch rather than the patch agent's
    ``bundle/<id>``: changes.diff is the complete canonical artifact,
    and the patch branch may already have been dropped by Slice 4's
    terminal sweep.

    Returns ``(ok, previous_ref)``. Unlike the convert/patch checkout
    helper this is NOT soft-fail: a failed checkout returns
    ``ok=False`` and the caller MUST abort verify rather than replay.
    Verify's entire contract is "does the diff apply cleanly on a
    fresh base" — if we couldn't establish that base, any verdict is
    meaningless and replaying onto whatever branch is current would
    both lie and let cleanup reset the wrong tree. ``previous_ref`` is
    the ref the env was on before (for the end-of-run drop), valid
    only when ``ok``.
    """
    if not env or not bundle_id:
        # A real verify job always carries both; missing them means a
        # malformed job we can't verify — treat as checkout failure.
        return (False, None)
    from dportsv3.agent import worker  # noqa: PLC0415
    try:
        result = worker.checkout_verify_branch(env, bundle_id)
    except Exception as exc:
        try:
            activity_log(
                queue_root, "verify_branch_checkout_failed",
                f"verify {job_id}: checkout raised: {exc!s}"[:240],
                job_id=job_id,
                extra={"bundle_id": bundle_id, "env": env,
                       "exception": str(exc)[:300]},
            )
        except Exception:
            pass
        return (False, None)
    if not result.get("ok"):
        try:
            activity_log(
                queue_root, "verify_branch_checkout_failed",
                (f"verify {job_id}: "
                 f"{result.get('error') or 'checkout failed'}")[:240],
                job_id=job_id,
                extra={"bundle_id": bundle_id, "env": env,
                       "branch": result.get("branch"),
                       "base": result.get("base"),
                       "stderr_tail": result.get("stderr_tail", "")[:300]},
            )
        except Exception:
            pass
        return (False, None)
    try:
        activity_log(
            queue_root, "verify_branch_checkout",
            (f"verify {job_id}: created {result.get('branch')} "
             f"(base={result.get('base')}, "
             f"prev={result.get('previous_ref')})"),
            job_id=job_id,
            extra={"bundle_id": bundle_id, "env": env,
                   "branch": result.get("branch"),
                   "base": result.get("base"),
                   "previous_ref": result.get("previous_ref")},
        )
    except Exception:
        pass
    return (True, result.get("previous_ref"))


def _drop_verify_branch_for_job(
    *,
    queue_root: Path,
    job_id: str,
    env: str | None,
    bundle_id: str | None,
    restore_ref: str | None,
    reason: str,
) -> None:
    """Delete the verify run's ``bundle/<id>-verify`` branch and
    restore the ref the env was on before verify started
    (``restore_ref`` from :func:`_checkout_verify_branch_for_job`).
    Soft-fail; mirrors :func:`_drop_bundle_branch_for_job`."""
    if not env or not bundle_id:
        return
    from dportsv3.agent import worker  # noqa: PLC0415
    try:
        result = worker.drop_verify_branch(env, bundle_id, restore_ref)
    except Exception as exc:
        try:
            activity_log(
                queue_root, "verify_branch_drop_failed",
                f"verify {job_id}: drop raised: {exc!s}"[:240],
                job_id=job_id,
                extra={"bundle_id": bundle_id, "env": env,
                       "reason": reason, "exception": str(exc)[:300]},
            )
        except Exception:
            pass
        return
    if not result.get("ok"):
        try:
            activity_log(
                queue_root, "verify_branch_drop_failed",
                (f"verify {job_id}: "
                 f"{result.get('error') or 'drop failed'}")[:240],
                job_id=job_id,
                extra={"bundle_id": bundle_id, "env": env,
                       "reason": reason,
                       "branch": result.get("branch")},
            )
        except Exception:
            pass
        return
    if result.get("removed"):
        try:
            activity_log(
                queue_root, "verify_branch_dropped",
                (f"verify {job_id}: dropped {result.get('branch')} "
                 f"(restored {result.get('restored_to')}, {reason})"),
                job_id=job_id,
                extra={"bundle_id": bundle_id, "env": env,
                       "branch": result.get("branch"),
                       "restored_to": result.get("restored_to"),
                       "reason": reason},
            )
        except Exception:
            pass


def _maybe_skip_locked_origin(
    *,
    queue_root: Path,
    job: dict,
    job_id: str,
    sibling_paths: list[Path] | None,
    origin: str,
    job_type: str = "triage",
) -> tuple[bool, str] | None:
    """Step 28a / 28-extra: short-circuit a job when the operator
    has staked (target, origin) via take-over.

    Returns None when the origin is not locked (job proceeds
    normally), or a ``(True, status)`` tuple to retire the job
    immediately. Idempotent: a second job for a still-locked
    origin produces the same short-circuit + a fresh activity row
    so each bypass is observable. Best-effort lookup — DB errors
    don't block the job, just log and proceed.

    ``job_type`` ("triage" / "patch" / "convert") selects the
    activity-log stage name so a per-job-type filter on the UI
    can distinguish the three bypass paths. The lifecycle event
    (``SKIP_ORIGIN_LOCKED``) is shared across all three.
    """
    from dportsv3.agent.lifecycle import JobEvent

    if _state_db_conn is None:
        return None
    target = job.get("target") or os.environ.get(
        "DPORTSV3_TRACKER_TARGET", "",
    )
    if not target:
        return None

    try:
        with _state_db_lock:
            from dportsv3.tracker.agentic_queries import (  # noqa: PLC0415
                is_origin_skipped,
            )
            lock = is_origin_skipped(_state_db_conn, target, origin)
    except sqlite3.Error as exc:
        log(queue_root, "WARN",
            f"skip-lock lookup failed for {origin}: {exc}; proceeding")
        return None

    if lock is None:
        return None

    locked_bundle = lock.get("bundle_id") or "?"
    set_by = lock.get("set_by") or "?"
    reason = lock.get("reason") or ""
    activity_log(
        queue_root, f"{job_type}_skipped_origin_locked",
        (
            f"{origin}: origin locked by bundle {locked_bundle} "
            f"({set_by}); {job_type} skipped"
        ),
        job_id=job_id,
        extra={
            "origin": origin,
            "target": target,
            "locking_bundle_id": locked_bundle,
            "locked_by": set_by,
            "lock_reason": reason,
            "job_type": job_type,
        },
    )
    # Retire the job DEAD with retire_reason='origin_locked'. The
    # dedicated SKIP_ORIGIN_LOCKED event keeps this case separable
    # from Step 10b's ABANDON (operator hand-killed) in lineage
    # views and the manual queue. Fan out to siblings so a multi-
    # job fanout doesn't leave parallel jobs stuck after this lead
    # bypasses.
    detail = {
        "skipped_because": "origin_locked",
        "locking_bundle_id": locked_bundle,
        "locked_by": set_by,
        "lock_reason": reason,
        "origin": origin,
        "target": target,
        "job_type": job_type,
    }
    _apply_transition(job_id, JobEvent.SKIP_ORIGIN_LOCKED, detail=detail)
    for s in sibling_paths or ():
        _apply_transition(s.name, JobEvent.SKIP_ORIGIN_LOCKED, detail=detail)
    return True, f"origin_locked_by:{locked_bundle}"


def _maybe_defer_to_convert(
    *,
    queue_root: Path,
    job: dict,
    job_path: Path,
    origin: str,
    apply_lifecycle: bool = True,
) -> tuple[bool, str] | None:
    """Step 20d: defer this triage if the port still has legacy
    overlay artifacts.

    Returns ``None`` to let the triage proceed normally, or a
    ``(success, status)`` tuple to short-circuit it. On defer:
    enqueue a convert job (or attach to an in-flight one) and
    park this triage at ESCALATED so the manual queue surfaces
    the chain.

    ``apply_lifecycle``: when True (legacy default), walks the
    triage lifecycle TRIAGING → DEAD via TRIAGE_DEFER inside this
    function — used by the historical call-from-top-of-process_triage_job
    code path and by direct-invocation tests. When False, skips the
    lifecycle walk so the caller (post-Step-36-followup: TriageStep,
    which now calls this AFTER classification so convert sees a
    written triage_result.json) can emit its own TRIAGE_DEFER outcome
    through the orchestrator and avoid double-transitions.

    Triage's classification is intentionally NOT an input here: the
    patch agent edits ``overlay.dops`` directly, so convert (which
    produces that substrate) is a prerequisite for any patch flow to
    function, regardless of what kind of bug triage saw. See
    [[project-convert-is-substrate-prerequisite]] in memory.
    """
    from dportsv3.agent.lifecycle import JobEvent

    target = job.get("target") or os.environ.get(
        "DPORTSV3_TRACKER_TARGET", "",
    )

    # Classification MUST run inside the dev-env (the chroot) — that's
    # the substrate the convert agent writes into via put_file. Reading
    # the host clone (or even the env's writable overlay from outside
    # the chroot) bypasses the substrate and produces stale results.
    # Goes through `dportsv3 dev-env exec ENV -- dportsv3 agent
    # classify-dops <origin>` like every other tool-surface call.
    env_resolution = resolve_env_or_reason(job)
    env_name = env_resolution.env
    if not env_name:
        log(queue_root, "WARN",
            f"no dev-env resolved for {origin!r} "
            f"({env_resolution.refusal_reason}); cannot classify, "
            f"proceeding with triage")
        try:
            activity_log(
                queue_root,
                "triage_dops_assessment_skipped",
                "dops assessment skipped: missing dev-env; proceeding with triage",
                job_id=job_path.name,
                extra={
                    "origin": origin,
                    "target": target,
                    "reason": "missing_dev_env",
                    "job_has_dev_env": bool(job.get("dev_env")),
                    "refusal_reason": env_resolution.refusal_reason,
                },
            )
        except Exception as exc:
            log(queue_root, "WARN", f"activity_log failed in dops-skip: {exc}")
        return None

    from dportsv3.agent import worker
    try:
        assessment = worker.assess_dops(env_name, origin)
    except Exception as exc:
        log(queue_root, "WARN",
            f"assess_dops({origin!r}) failed: {exc}; proceeding with triage")
        try:
            activity_log(
                queue_root,
                "triage_dops_assessment_failed",
                "dops assessment failed; proceeding with triage",
                job_id=job_path.name,
                extra={
                    "origin": origin,
                    "target": target,
                    "env": env_name,
                    "error": str(exc)[:500],
                },
            )
        except Exception as log_exc:
            log(queue_root, "WARN", f"activity_log failed in dops-fail: {log_exc}")
        return None

    state = assessment.state
    # Step 11c layer-violation cleanup: persist dops_state on the
    # bundle row so the tracker can show it without reaching into
    # the host filesystem at render time. Best-effort: a missing
    # bundle_id or write error is non-fatal.
    bundle_id = job.get("bundle_id")
    if bundle_id and _state_db_conn is not None:
        try:
            with _state_db_lock:
                _state_db_conn.execute(
                    "UPDATE bundles SET dops_state = ?, last_seen_at = ? "
                    "WHERE bundle_id = ?",
                    (state, datetime.now(timezone.utc).isoformat(),
                     bundle_id),
                )
                _state_db_conn.commit()
        except sqlite3.Error as exc:
            log(queue_root, "WARN",
                f"failed to persist dops_state for {bundle_id}: {exc}")

    if assessment.action == "surface_invariant":
        log(queue_root, "WARN",
            f"refusing to defer triage for {origin!r}: overlay assessment "
            f"found invariant violations {assessment.invariant_violations!r}; "
            f"state={state!r} reasons={assessment.reasons!r}")
        try:
            activity_log(
                queue_root,
                "triage_defer_invariant_break",
                (
                    f"overlay invariant violation; proceeding with triage "
                    f"(state={state})"
                ),
                job_id=job_path.name,
                extra=assessment.to_log_dict(),
            )
        except Exception as exc:
            log(queue_root, "WARN", f"activity_log failed in invariant-break: {exc}")
        return None

    if assessment.action != "defer_to_convert":
        # converted / stale / not_in_scope all mean "no conversion
        # needed". Let triage run.
        return None

    prior_convert = _bundle_convert_succeeded(bundle_id)
    if prior_convert is not None:
        log(queue_root, "WARN",
            f"refusing to re-defer triage for {origin!r}: convert "
            f"{prior_convert} already succeeded for this bundle but classify "
            f"still says {state!r}; proceeding with triage to surface the bug")
        try:
            activity_log(
                queue_root,
                "triage_defer_circuit_break",
                (
                    f"convert {prior_convert} succeeded for this bundle but "
                    f"classify still {state}; proceeding with triage"
                ),
                job_id=job_path.name,
                extra={
                    "recent_convert_job_id": prior_convert,
                    "dops_state": state,
                    "assessment": assessment.to_log_dict(),
                },
            )
        except Exception as exc:
            log(queue_root, "WARN", f"activity_log failed in circuit-break: {exc}")
        return None

    existing = _find_active_convert_job(origin, target)
    if existing:
        convert_job_id = existing
        log(queue_root, "INFO",
            f"triage for {origin!r} deferring to in-flight convert job "
            f"{convert_job_id}")
    else:
        convert_path = enqueue_convert_job(
            queue_root,
            origin=origin,
            target=target,
            profile=job.get("profile", ""),
            requested_by="triage",
            dev_env=job.get("dev_env"),
            bundle_dir=job.get("bundle_dir"),
            bundle_id=job.get("bundle_id"),
        )
        convert_job_id = convert_path.name
        log(queue_root, "INFO",
            f"triage for {origin!r} enqueued convert job {convert_job_id} "
            f"(dops state={state!r})")

    detail = {
        "deferred_for_convert": True,
        "convert_job_id": convert_job_id,
        "dops_state": state,
        "assessment": assessment.to_log_dict(),
    }
    try:
        activity_log(
            queue_root,
            "triage_deferred_for_convert",
            (
                f"deferred to convert job {convert_job_id} "
                f"(dops_state={state})"
            ),
            job_id=job_path.name,
            extra=detail,
        )
    except Exception as exc:
        log(queue_root, "WARN", f"activity_log failed in defer: {exc}")

    # Walk the lifecycle: TRIAGING -> DEAD via TRIAGE_DEFER. The
    # retire_reason 'deferred_for_convert' tells the manual queue
    # to skip this triage — operator action is on the convert job,
    # not here. activity_log row above carries the convert_job_id
    # so the chain is navigable. Skipped when the caller will emit
    # TRIAGE_DEFER itself via the orchestrator (see ``apply_lifecycle``
    # docstring).
    if apply_lifecycle:
        try:
            _apply_transition(
                job_path.name, JobEvent.TRIAGE_DEFER, detail=detail,
            )
        except Exception as exc:
            log(queue_root, "WARN",
                f"failed to defer triage lifecycle: {exc}")

    return True, f"deferred_for_convert:convert_job_id={convert_job_id}"


def process_triage_job(
    queue_root: Path,
    job_path: Path,
    sibling_paths: list[Path],
    job: dict,
    bundle_dir: Path | None,
    playbooks_dir: Path | None,
) -> tuple[bool, str]:
    """Process a triage job by driving TriageStep through the orchestrator.

    Phase 5: TriageStep's ``StepOutcome.next_event`` + ``extra_events``
    encode the lifecycle events to fire on completion. The
    orchestrator fires them for the lead; this wrapper fans the
    same events out to siblings. ``_completion_events_for`` retires.

    Step 20d: before invoking the triage step, check whether the
    port still has legacy overlay artifacts. If so, enqueue a
    convert job and defer this triage to ESCALATED with a clear
    detail — the agent should not spend tokens triaging a port
    whose framework patches are about to be rewritten anyway.
    """
    from dportsv3.agent.step import Orchestrator, StepCtx
    from dportsv3.agent.steps import TriageServices, TriageStep

    origin = job.get("origin", "unknown")
    job_id = job_path.name

    # ---- Step 28a: operator-owned origin short-circuit ------------
    # If the operator has staked (target, origin) via take-over,
    # don't burn cycles on this triage. Mark the job DEAD with a
    # distinct retire_reason so the manual queue and lineage
    # queries can tell "skipped because operator owns this" apart
    # from "actually failed." Emit an activity row so the bypass
    # is observable; reference the bundle that triggered the lock
    # for forensics. The bundle row itself is left alone — the
    # hook's failure record is unchanged; only the triage path
    # short-circuits.
    skipped = _maybe_skip_locked_origin(
        queue_root=queue_root, job=job, job_id=job_id,
        sibling_paths=sibling_paths, origin=origin,
    )
    if skipped is not None:
        return skipped
    # ---------------------------------------------------------------

    # ---- Step 20d / Step 36 follow-up: lazy convert hook ----------
    # The substrate defer used to short-circuit triage *before* the
    # LLM ran — convert was then dispatched blind, with no view of
    # what triage would have classified. Post-Step-36 the check
    # moves INTO TriageStep, AFTER the LLM call and the typed
    # ``TriageResult`` (analysis/triage_result.json) write, so
    # convert reads triage's classification + root_cause + evidence
    # via load_phase_result. Wired into services below; nothing to
    # do here at the top of the dispatcher.
    # ---------------------------------------------------------------

    # Seed queue_root + job_id into job so payload-build telemetry
    # (`playbooks_selected` activity row) can find them. parse_job_file
    # doesn't populate either; only the runner's per-job dispatch
    # knows the live queue_root and the dispatcher knows the job_id.
    # Without job_id the activity row lands with NULL job_id and is
    # invisible to `tracker get-activity --job ID`.
    job["queue_root"] = str(queue_root)
    job["job_id"] = job_id
    payload = build_triage_payload(bundle_dir, playbooks_dir, job)

    ctx = StepCtx(
        job_id=job_id,
        job=job,
        queue_root=queue_root,
        apply_transition=_apply_transition,
        activity_log=activity_log,
        db_conn=_state_db_conn,
        env_name=resolve_env(job),
        bundle_dir=bundle_dir,
        bundle_id=job.get("bundle_id"),
        playbooks_dir=playbooks_dir,
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
        write_manual_handoff=_write_manual_handoff,
        # Step 36 follow-up: substrate defer runs INSIDE TriageStep,
        # after the LLM + triage_result.json write. apply_lifecycle=False
        # so TriageStep emits TRIAGE_DEFER through its StepOutcome and
        # the orchestrator wrapper walks lifecycle once.
        maybe_defer_to_convert=lambda *, queue_root, job, job_path, origin: (
            _maybe_defer_to_convert(
                queue_root=queue_root, job=job, job_path=job_path,
                origin=origin, apply_lifecycle=False,
            )
        ),
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
    if tool == "grep":
        return (
            f"pattern={args.get('pattern', '')!r} path={args.get('path', '')} "
            f"matches={len(result.get('matches') or [])}{ok_tag}"
        )
    if tool == "materialize_dports":
        def _last_nonempty_line(text: str) -> str:
            for ln in reversed((text or "").splitlines()):
                ln = ln.strip()
                if ln:
                    return ln
            return ""
        # Compose tends to print the canonical error last with
        # context above; the tail line is the right one.
        msg_line = (
            _last_nonempty_line(result.get("stderr_tail") or "")
            or _last_nonempty_line(result.get("stdout_tail") or "")
        )
        suffix = f" — {msg_line[:160]}" if (not ok and msg_line) else ""
        return f"origin={args.get('origin', '')}{ok_tag}{suffix}"
    if tool == "validate_dops":
        # Surface the first non-empty line of stderr (or stdout
        # as a fallback) so a failing validate_dops is debuggable
        # from the tracker without spelunking artifacts. The
        # diagnostic format ("ERROR E_*: ... [path:line:col]") is
        # the common case but not the only failure mode — runtime
        # errors before diagnostics flow are also possible.
        def _first_nonempty_line(text: str) -> str:
            for ln in (text or "").splitlines():
                ln = ln.strip()
                if ln:
                    return ln
            return ""
        msg_line = (
            _first_nonempty_line(result.get("stderr_tail") or "")
            or _first_nonempty_line(result.get("stdout_tail") or "")
        )
        suffix = f" — {msg_line[:160]}" if (not ok and msg_line) else ""
        return f"origin={args.get('origin', '')}{ok_tag}{suffix}"
    if tool == "put_file":
        # On failure (sha mismatch / permission), the worker's
        # ``error`` field carries the reason — show it.
        err = (result.get("error") or result.get("stderr_tail") or "")
        err = err.strip().splitlines()
        suffix = f" — {err[0][:120]}" if (not ok and err) else ""
        path = args.get('path', '')
        size = len((args.get('content') or ''))
        return f"{path} ({size} bytes){ok_tag}{suffix}"
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


# Step 37-3: extract the `## Patch Plan (JSON)` block from the
# agent's final text. Mirrors attempt_loop._parse_rebuild_proof's
# heading-based regex. Returns None when the block is absent or
# the JSON doesn't parse cleanly — caller treats as "no plan, no
# deferred_verdicts" (graceful degrade).
_PATCH_PLAN_BLOCK_RE = re.compile(
    r"##\s+Patch\s+Plan\s*\(JSON\).*?```(?:json)?\s*(.*?)```",
    re.DOTALL | re.IGNORECASE,
)


def _parse_patch_plan(text: str) -> dict | None:
    if not text:
        return None
    m = _PATCH_PLAN_BLOCK_RE.search(text)
    if not m:
        return None
    try:
        obj = json.loads(m.group(1).strip())
    except (json.JSONDecodeError, TypeError):
        return None
    return obj if isinstance(obj, dict) else None


_VALID_VERDICTS = frozenset({"regenerated", "dropped", "escalated"})
# Step 37 #4-fix: verdicts whose resolution means the original
# framework diff file is dead weight on disk. ``escalated`` is
# excluded — operator may want to see/restore the original.
_CLEANUP_VERDICTS = frozenset({"regenerated", "dropped"})


def cleanup_resolved_deferred_patches(
    *,
    env: str,
    origin: str,
    verdicts: list,
    queue_root: Path,
    job_id: str | None,
) -> list[str]:
    """Delete the framework ``diffs/*.diff`` files corresponding to
    ``regenerated`` / ``dropped`` verdicts. Returns the list of
    paths actually deleted. ``escalated`` paths are left in place so
    the operator can inspect / restore them.

    Path safety: only files under ``ports/<origin>/diffs/`` are
    eligible. Anything else (absolute paths, ``..`` segments, paths
    outside diffs/) is skipped with a warning — defends against a
    malformed verdict that tries to escape the port subtree.

    Best-effort: missing files / IO failures log a warning and
    continue. The agent's overlay.dops edits already happened; this
    is post-hoc tree hygiene, not load-bearing.
    """
    if not verdicts:
        return []
    try:
        from dportsv3.agent import worker  # noqa: PLC0415
        paths = worker.env_paths(env)
    except Exception as exc:
        log(queue_root, "WARN",
            f"cleanup_resolved_deferred_patches: env_paths({env!r}) "
            f"failed: {exc}")
        return []
    port_dir = paths.deltaports / "ports" / origin
    diffs_dir = (port_dir / "diffs").resolve()

    deleted: list[str] = []
    for v in verdicts:
        verdict = getattr(v, "verdict", None)
        rel = getattr(v, "path", None)
        if not isinstance(rel, str) or not isinstance(verdict, str):
            continue
        if verdict not in _CLEANUP_VERDICTS:
            continue
        # Path-safety: must be a relative path under diffs/ that
        # resolves inside the port's diffs/ subtree.
        if rel.startswith("/") or ".." in Path(rel).parts:
            log(queue_root, "WARN",
                f"cleanup_resolved_deferred_patches: refusing "
                f"unsafe path {rel!r}")
            continue
        if not rel.startswith("diffs/"):
            # Convert only ever defers diffs/*.diff today. A verdict
            # for some other path is suspicious — skip rather than
            # delete random files.
            log(queue_root, "WARN",
                f"cleanup_resolved_deferred_patches: ignoring "
                f"non-diffs/ path {rel!r}")
            continue
        candidate = (port_dir / rel).resolve()
        try:
            candidate.relative_to(diffs_dir)
        except ValueError:
            log(queue_root, "WARN",
                f"cleanup_resolved_deferred_patches: {rel!r} resolved "
                f"outside diffs/; skip")
            continue
        if not candidate.is_file():
            # Already gone (operator cleaned up, or convert never
            # wrote it). Not an error; nothing to do.
            continue
        try:
            candidate.unlink()
        except OSError as exc:
            log(queue_root, "WARN",
                f"cleanup_resolved_deferred_patches: unlink {rel} "
                f"failed: {exc}")
            continue
        deleted.append(rel)
        try:
            activity_log(
                queue_root, "convert_deferred_cleanup",
                f"removed orphan framework patch {rel} for {origin} "
                f"(verdict={verdict})",
                job_id=job_id,
                extra={
                    "origin": origin,
                    "path": rel,
                    "verdict": verdict,
                },
            )
        except Exception as exc:
            log(queue_root, "WARN",
                f"activity_log failed in deferred_cleanup: {exc}")
    return deleted


def _resolve_deferred_verdicts_for_patch(
    bundle_dir: Path | None,
    bundle_id: str | None,
    plan_text: str,
) -> list:
    """Canonical per-deferred-patch verdict list, computed from the
    originating convert bundle's ``ConvertResult.deferred_patches``
    cross-referenced against the agent's ``Patch Plan (JSON)``'s
    ``deferred_verdicts`` field.

    For each path convert deferred:
    - If the agent emitted a valid verdict (one of regenerated /
      dropped / escalated) for that path: use it.
    - Otherwise: synthesize an ``escalated`` verdict with rationale
      ``"no verdict provided by patch agent"`` — closes the gap where
      the agent ignored a deferred patch entirely, which would
      otherwise let the bundle route to ``agent_fixed`` silently.

    Plan entries for paths NOT in convert's deferred list are dropped
    silently — the agent isn't allowed to invent verdicts for patches
    it wasn't handed.

    Returns ``[]`` when convert didn't defer anything (the normal
    case for ports that compose cleanly). Callers should treat that
    as "no verdict layer applies."
    """
    from dportsv3.agent.phase_result import (  # noqa: PLC0415
        ConvertResult, DeferredVerdict, load_phase_result,
    )

    try:
        cr = load_phase_result(
            bundle_dir, bundle_id, "convert", ConvertResult,
        )
    except Exception:
        # Schema mismatch / parse error → degrade as if no convert
        # context existed. The patch agent never saw deferred
        # patches in its payload either (DeferredFromConvertSection
        # uses the same load + same except).
        cr = None
    if cr is None or not cr.deferred_patches:
        return []

    expected_paths = [dp.path for dp in cr.deferred_patches]
    plan = _parse_patch_plan(plan_text or "") or {}
    raw_entries = plan.get("deferred_verdicts")
    if not isinstance(raw_entries, list):
        raw_entries = []

    # Index by path (first valid entry wins; agent isn't supposed to
    # repeat paths but if it does, take the first sensible one).
    by_path: dict[str, dict] = {}
    for entry in raw_entries:
        if not isinstance(entry, dict):
            continue
        path = str(entry.get("path") or "").strip()
        verdict = str(entry.get("verdict") or "").strip().lower()
        if not path or verdict not in _VALID_VERDICTS:
            continue
        by_path.setdefault(path, entry)

    out: list = []
    for expected in expected_paths:
        entry = by_path.get(expected)
        if entry is None:
            out.append(DeferredVerdict(
                path=expected,
                verdict="escalated",
                rationale="no verdict provided by patch agent",
            ))
            continue
        out.append(DeferredVerdict(
            path=expected,
            verdict=str(entry["verdict"]).strip().lower(),
            rationale=str(entry.get("rationale") or ""),
        ))
    return out


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

    # Always emit rebuild_proof.json on terminal states. Without
    # this, budget-exhausted / needs-help bundles produced
    # patch_audit.json but no proof artifact, leaving operators
    # unable to tell "agent gave up cleanly" from "agent crashed
    # mid-attempt" by skimming the artifact list. When the LLM
    # parsed a Rebuild Proof block we write it verbatim; otherwise
    # we synthesize one carrying the terminal status + attempt
    # count so the contract holds.
    if result.proof is not None:
        proof_payload = dict(result.proof)
    else:
        proof_payload = {
            "rebuild_ok": False,
            "status": result.status,
            "reason": (
                "agent gave up: " + result.status
                if result.status != "success"
                else "no rebuild proof parsed despite success status"
            ),
            "attempts": len(result.attempts),
            "synthetic": True,
        }
    proof_bytes = (json.dumps(proof_payload, indent=2) + "\n").encode("utf-8")
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

    # Step 36-3: typed PatchResult for downstream phases / future
    # tracker UI. patch_audit.json + rebuild_proof.json stay (verify
    # and existing UI consume them); this writes the typed contract
    # alongside.
    from dataclasses import asdict  # noqa: PLC0415
    from dportsv3.agent.phase_result import (  # noqa: PLC0415
        PatchResult as _PatchResultTyped, write_phase_result,
    )
    # Step 37-3/37-4 fix-up: canonical verdicts come from the
    # cross-reference resolver. Missing per-deferred-patch verdicts
    # are synthesized as "escalated: no verdict provided" so the
    # bundle never silently routes to agent_fixed when the agent
    # ignored deferred work.
    deferred_verdicts = _resolve_deferred_verdicts_for_patch(
        bundle_dir, bundle_id, text,
    )
    typed = _PatchResultTyped(
        rebuild_ok=bool(proof_payload.get("rebuild_ok")),
        status=result.status,
        attempts=len(result.attempts),
        tokens_prompt=result.usage.prompt_tokens,
        tokens_completion=result.usage.completion_tokens,
        tokens_total=result.usage.total_tokens,
        deferred_verdicts=deferred_verdicts,
    )
    if bundle_id:
        write_phase_result(bundle_id, "patch", typed)
    elif bundle_dir is not None:
        out = bundle_dir / "analysis" / "patch_result.json"
        out.write_bytes(
            (json.dumps(asdict(typed), indent=2) + "\n").encode("utf-8")
        )


def _write_changes_diff(bundle_dir: Path | None, bundle_id: str | None, env: str, origin: str) -> None:
    """Capture the bundle branch's full diff vs the env's base branch
    and write to ``analysis/changes.diff``.

    Step 30 slice 5: this is the *canonical* diff artifact — single
    source of truth for delivery, verify-fix replay, the
    proposed_fix recipe, and the agent's prior-attempt context
    sections. Pre-slice-5 ``changes.diff`` was HEAD-relative and
    a sibling ``delivery.diff`` carried the branch-vs-base shape;
    the dual artifact split was wrong (no reader had a legitimate
    need for the HEAD-relative form, and converted bundles lost
    the convert commit's deltas in the HEAD-relative diff).

    Includes both convert commits on the bundle branch and the
    patch agent's uncommitted working-tree edits. Uses
    ``worker._git_diff_against_base`` (with the ``--intent-to-add``
    dance) so freshly-created files surface as additions.

    Best-effort: failures emit a tombstone diff body so the
    operator sees the failure shape rather than getting silent
    delivery breakage downstream.
    """
    try:
        from dportsv3.agent import worker  # type: ignore[import-not-found]
        paths = worker.env_paths(env)
        rel = f"ports/{origin}"
        base = worker._resolve_bundle_base_branch(env)
        p = worker._git_diff_against_base(paths.deltaports, base, rel)
        diff_bytes = p.stdout.encode("utf-8")
    except Exception as exc:
        diff_bytes = f"# failed to capture diff: {exc}\n".encode("utf-8")

    if bundle_id:
        artifact_store_put(bundle_id, "analysis/changes.diff", diff_bytes, "text")
    elif bundle_dir:
        out = bundle_dir / "analysis" / "changes.diff"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(diff_bytes)


def _load_operator_context_history(
    run_id: str | None, origin: str | None,
) -> list[dict]:
    """Step 29c: read every operator-submitted context round for
    ``(run_id, origin)`` so ``manual_handoff.md`` can render the
    full operator-side narrative. Returns ``[]`` if the DB isn't
    available, the lookup is unscoped, or no rounds exist."""
    if _state_db_conn is None or not run_id or not origin:
        return []
    try:
        with _state_db_lock:
            from dportsv3.tracker.agentic_queries import (  # noqa: PLC0415
                list_user_context_history,
            )
            return list_user_context_history(
                _state_db_conn, run_id, origin,
            )
    except Exception:
        return []


def _write_manual_handoff(
    bundle_dir: Path | None,
    bundle_id: str | None,
    *,
    origin: str,
    target: str,
    reason: str,
    reason_detail: str = "",
    decision_extra: dict | None = None,
    patch_result: object | None = None,
    run_id: str | None = None,
) -> None:
    """Render and persist ``analysis/manual_handoff.md`` for an
    escalated job. Best-effort: failures are swallowed so escalation
    bookkeeping (lifecycle event, user-context-request row) never gets
    blocked by an artifact write."""
    try:
        from dportsv3.agent import manual_handoff  # noqa: PLC0415

        history = _load_operator_context_history(run_id, origin)
        ctx = manual_handoff.build_handoff_ctx(
            origin=origin,
            target=target,
            reason=reason,
            reason_detail=reason_detail,
            bundle_id=bundle_id or "",
            bundle_dir=bundle_dir,
            read_bundle_text=read_bundle_text,
            decision_extra=decision_extra,
            patch_result=patch_result,
            operator_context_history=history,
        )
        body = manual_handoff.render_handoff(ctx).encode("utf-8")
    except Exception as exc:
        print(f"Warning: manual_handoff render failed for {origin}: {exc}",
              file=sys.stderr)
        return

    if bundle_id:
        artifact_store_put(bundle_id, "analysis/manual_handoff.md", body, "text")
    elif bundle_dir is not None:
        out = bundle_dir / "analysis" / "manual_handoff.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(body)


def _write_proposed_fix(
    bundle_dir: Path | None,
    bundle_id: str | None,
    *,
    origin: str,
    target: str,
    model: str,
    classification: str = "",
    confidence: str = "",
    attempts_max: int = 0,
    patch_result: object | None = None,
) -> None:
    """Render and persist ``analysis/proposed_fix.md`` after a
    successful patch attempt. Best-effort: failures are swallowed so
    the artifact write never blocks the lifecycle event.

    Operator-facing one-pager: what the agent did, the cost, and the
    recipe to land the fix in the operator's own DeltaPorts clone.
    Surfaces as the default bundle preview when
    ``resolution='agent_fixed'``.
    """
    try:
        from dportsv3.agent import proposed_fix  # noqa: PLC0415

        ctx = proposed_fix.build_proposed_fix_ctx(
            origin=origin,
            target=target,
            bundle_id=bundle_id or "",
            bundle_dir=bundle_dir,
            read_bundle_text=read_bundle_text,
            patch_result=patch_result,
            model=model,
            classification=classification,
            confidence=confidence,
            attempts_max=attempts_max,
            tracker_url=_tracker_url(),
        )
        body = proposed_fix.render_proposed_fix(ctx).encode("utf-8")
    except Exception as exc:
        print(f"Warning: proposed_fix render failed for {origin}: {exc}",
              file=sys.stderr)
        return

    if bundle_id:
        artifact_store_put(bundle_id, "analysis/proposed_fix.md", body, "text")
    elif bundle_dir is not None:
        out = bundle_dir / "analysis" / "proposed_fix.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(body)


def process_patch_job(
    queue_root: Path,
    job_path: Path,
    sibling_paths: list[Path],
    job: dict,
    bundle_dir: Path | None,
    playbooks_dir: Path | None,
) -> tuple[bool, str]:
    """Process a patch job by driving PatchAttemptStep through the orchestrator.

    Phase 5: PatchAttemptStep's ``StepOutcome.next_event`` +
    ``extra_events`` encode the lifecycle events to fire on
    completion. The orchestrator fires them for the lead; this
    wrapper fans the same events out to siblings.
    """
    from dportsv3.agent.step import Orchestrator, StepCtx
    from dportsv3.agent.steps import PatchAttemptStep, PatchServices

    origin = job.get("origin", "unknown")
    job_id = job_path.name

    # ---- Step 28-extra: operator-owned origin short-circuit -------
    # If the operator has staked (target, origin) between when this
    # patch job was enqueued and when the runner picked it up, don't
    # burn LLM tokens — retire the job DEAD with
    # retire_reason='origin_locked'. Mirrors the triage-side check.
    skipped = _maybe_skip_locked_origin(
        queue_root=queue_root, job=job, job_id=job_id,
        sibling_paths=sibling_paths, origin=origin,
        job_type="patch",
    )
    if skipped is not None:
        return skipped
    # ---------------------------------------------------------------

    # Step 30 slice 1: pin the patch's work to the same per-bundle
    # branch the convert (if any) wrote to. Reuses an existing
    # bundle/<id> branch; creates a fresh one off the env's base
    # when this is a patch on an un-converted port. Soft-fail —
    # see process_convert_job for the trade-off rationale.
    _checkout_bundle_branch_for_job(
        queue_root=queue_root, job_id=job_path.name,
        env=resolve_env(job), bundle_id=job.get("bundle_id") or None,
        job_type="patch",
    )

    # Step 38a: record the env's compose target so get_effective_overlay
    # can scope-filter overlay.dops by the build line the env targets.
    # Guard env=None so an unresolvable env doesn't stash a junk entry
    # under the None key (the cache miss-fallback is the same `None`,
    # so behavior is unchanged either way — but a clean cache makes
    # debugging easier).
    from dportsv3.agent import worker as _worker  # noqa: PLC0415
    _38a_env = resolve_env(job)
    if _38a_env:
        _worker.set_env_target(_38a_env, job.get("target") or None)

    # Seed queue_root + job_id into job so payload-build telemetry
    # (`playbooks_selected` activity row) can find them. See the same
    # comment in process_triage_job.
    job["queue_root"] = str(queue_root)
    job["job_id"] = job_path.name
    payload = build_patch_payload(bundle_dir, playbooks_dir, job)

    ctx = StepCtx(
        job_id=job_id,
        job=job,
        queue_root=queue_root,
        apply_transition=_apply_transition,
        activity_log=activity_log,
        db_conn=_state_db_conn,
        env_name=resolve_env(job),
        bundle_dir=bundle_dir,
        bundle_id=job.get("bundle_id"),
        playbooks_dir=playbooks_dir,
    )
    ctx.state["job_path"] = job_path
    ctx.state["payload"] = payload
    ctx.state["origin"] = origin
    ctx.state["policy_path"] = os.environ.get(
        "DP_HARNESS_POLICY", _DEFAULT_POLICY_PATH,
    )
    ctx.state["services"] = PatchServices(
        read_bundle_text=read_bundle_text,
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
        write_manual_handoff=_write_manual_handoff,
        write_proposed_fix=_write_proposed_fix,
    )

    result = Orchestrator().run(ctx, [PatchAttemptStep()])
    return _finish_orchestrator_run(
        result, step_name="patch",
        sibling_paths=sibling_paths,
        failure_event="patch_gave_up",
    )


def process_convert_job(
    queue_root: Path,
    job_path: Path,
    sibling_paths: list[Path],
    job: dict,
) -> tuple[bool, str]:
    """Process a dops-conversion job (Step 20c).

    First-cut handler: runs the deterministic
    :func:`dportsv3.migration.convert.convert_record` translator
    against the port. Auto-safe ports get converted; everything
    else (review-needed / fallback-only) parks at
    ``CONVERT_GAVE_UP`` with a ``needs_llm`` reason, waiting for
    20b's LLM loop to be wired up.

    Verification via ``dsynth_build`` is Step 20e and not done
    here yet.
    """
    from dportsv3.migration.classify import classify_inventory
    from dportsv3.agent.dops import _scan_one_port
    from dportsv3.migration.convert import convert_record
    from dportsv3.agent import worker

    origin = job.get("origin", "")
    if not origin:
        return False, "convert job missing origin"

    # ---- Step 28-extra: operator-owned origin short-circuit -------
    # A locked (target, origin) shouldn't be auto-converted either —
    # the operator may be hand-converting it. Same shape as the
    # triage / patch checks.
    skipped = _maybe_skip_locked_origin(
        queue_root=queue_root, job=job, job_id=job_path.name,
        sibling_paths=sibling_paths, origin=origin,
        job_type="convert",
    )
    if skipped is not None:
        return skipped
    # ---------------------------------------------------------------

    # Seed queue_root + job_id into job so downstream activity-log
    # writers (`_verify_conversion._fail`, `_run_llm_conversion`'s
    # rollback path) can route rows to the live tracker queue. Without
    # this both helpers read `job.get("queue_root")` and fall back to
    # `Path(".")` — meaning `convert_verify_failed` rows land in the
    # runner's CWD and never reach the tracker DB. Symptom was a
    # silent gap between `attempt_end rebuild_ok=True` and
    # `bundle_branch_dropped convert_failure` with zero rows in
    # between. Same pattern as `process_triage_job` (~2951) and
    # `process_patch_job` (~3465); only this processor was missing it.
    job["queue_root"] = str(queue_root)
    job["job_id"] = job_path.name

    env_resolution = resolve_env_or_reason(job)
    env_name = env_resolution.env
    if not env_name:
        return False, (
            f"no dev-env resolved; convert needs an env "
            f"({env_resolution.refusal_reason})"
        )

    # Step 30 slice 1: pin the convert's work to a per-bundle branch
    # so commits (overlay.dops creation, etc.) don't accumulate on
    # the env's base branch across bundles. Soft-fail: if the
    # checkout doesn't take, the convert falls back to pre-Step-30
    # behavior (commit lands on whatever branch is current). The
    # activity row makes the regression visible.
    _checkout_bundle_branch_for_job(
        queue_root=queue_root, job_id=job_path.name,
        env=env_name, bundle_id=job.get("bundle_id") or None,
        job_type="convert",
    )

    # Step 38a: record the env's compose target so get_effective_overlay
    # can scope-filter overlay.dops by the build line the env targets.
    worker.set_env_target(env_name, job.get("target") or None)

    # Classification (and everything downstream) reads the dev-env's
    # writable overlay via worker.classify_dops → dev-env exec →
    # dportsv3 agent classify-dops. Host filesystem is never the
    # substrate for tree contents.
    try:
        state = worker.classify_dops(env_name, origin)
    except Exception as exc:
        return False, f"classify_dops failed: {exc}"

    # The deterministic converter still operates on a Path (it
    # rewrites files), so we need the host-side path to the env's
    # writable overlay for that downstream call. Resolved via
    # worker.env_paths; this is the bridge between "decide via the
    # env's view" and "act on the env's writable overlay."
    env_paths = worker.env_paths(env_name)
    repo_root = (env_paths.writable / "work" / "DeltaPorts").resolve()
    if state == "converted":
        # No work to do. CONVERT_OK is appropriate — the goal state
        # is already reached.
        return True, "already converted"
    if state == "not_in_scope":
        return False, "port not in dops scope (no overlay artifacts)"
    if state == "stale":
        return False, "port marked stale"

    port_dir = repo_root / "ports" / origin
    record = _scan_one_port(port_dir, origin)
    if record is None:
        return False, "port directory missing"
    classified = classify_inventory([record])[0]

    if state == "needs_judgment":
        return _run_llm_conversion(
            job=job, job_path=job_path, origin=origin,
            repo_root=repo_root, classified=classified,
        )

    # state == "auto_safe_pending" → run the deterministic translator.
    result = convert_record(classified, repo_root=repo_root, dry_run=False)
    status = result.get("status", "")
    if status != "converted":
        return False, (
            f"convert_record status={status!r} errors={result.get('errors')!r}"
        )
    ok = (
        result.get("parse_ok")
        and result.get("check_ok")
        and result.get("plan_ok")
        and result.get("deterministic_ok")
    )
    if not ok:
        return False, (
            f"deterministic conversion failed validation: "
            f"errors={result.get('errors')!r}"
        )

    # Step 20e: verify the rewrite with dsynth_build inside the env.
    # No env → skip; env_resolver handles the auto/manual selection.
    return _verify_conversion(job, origin)


def _run_llm_conversion(
    *,
    job: dict,
    job_path: Path,
    origin: str,
    repo_root: Path,
    classified: dict,
) -> tuple[bool, str]:
    """Drive the convert agent (CONVERT_SYSTEM + attempt_loop) for a
    ``needs_judgment`` port. The deterministic translator can't
    handle it (conditional blocks / raw diffs / newport), so the
    LLM has to make the framework/source-simple/source-complex call.

    Returns ``(success, status)`` for the dispatcher. Verification
    (Step 20e) runs after the proof parses successfully.
    """
    from dportsv3.agent import convert as convert_mod
    from dportsv3.migration.convert import convert_record

    env_resolution = resolve_env_or_reason(job)
    env = env_resolution.env
    if not env:
        return False, (
            f"needs_llm but no dev-env resolved — convert agent "
            f"needs a dev-env for the tool surface "
            f"({env_resolution.refusal_reason})"
        )

    # Model resolution mirrors patch flow: dedicated CONVERT_MODEL
    # env var with fallback to PATCH_MODEL so single-model deployments
    # work out of the box.
    model = (
        os.environ.get("DP_HARNESS_CONVERT_MODEL")
        or os.environ.get("DP_HARNESS_PATCH_MODEL")
        or os.environ.get("DP_HARNESS_TRIAGE_MODEL")
    )
    if not model:
        return False, (
            "no model configured (set DP_HARNESS_CONVERT_MODEL or "
            "DP_HARNESS_PATCH_MODEL)"
        )
    api_base = (
        os.environ.get("DP_HARNESS_CONVERT_API_BASE")
        or os.environ.get("DP_HARNESS_PATCH_API_BASE")
        or None
    )
    api_key = (
        os.environ.get("DP_HARNESS_CONVERT_API_KEY")
        or os.environ.get("DP_HARNESS_PATCH_API_KEY")
        or None
    )
    provider = (
        os.environ.get("DP_HARNESS_CONVERT_PROVIDER")
        or os.environ.get("DP_HARNESS_PATCH_PROVIDER")
        or None
    )

    # CONVERT tier lives in config/agentic-policy.json next to
    # AUTO/ASSIST/MANUAL. DP_HARNESS_CONVERT_ITERATIONS /
    # DP_HARNESS_CONVERT_BUDGET still override at runtime for
    # one-off experiments without editing the config file.
    from dportsv3.agent.policy import Tier, load_policy
    policy_path = os.environ.get("DP_HARNESS_POLICY", _DEFAULT_POLICY_PATH)
    convert_tier_from_policy: Tier | None = None
    try:
        convert_tier_from_policy = load_policy(policy_path).tiers.get("CONVERT")
    except Exception as exc:
        log(Path("."), "WARN",
            f"failed to load policy from {policy_path!r}: {exc}; "
            f"using built-in CONVERT defaults")
    base_iterations = (
        convert_tier_from_policy.max_iterations
        if convert_tier_from_policy and convert_tier_from_policy.max_iterations
        else 2
    )
    base_tokens = (
        convert_tier_from_policy.max_tokens
        if convert_tier_from_policy and convert_tier_from_policy.max_tokens
        else 150000
    )
    tier = Tier(
        name="CONVERT",
        max_iterations=int(
            os.environ.get("DP_HARNESS_CONVERT_ITERATIONS", str(base_iterations))
        ),
        max_tokens=int(
            os.environ.get("DP_HARNESS_CONVERT_BUDGET", str(base_tokens))
        ),
    )

    # Build the payload — deterministic_result reuses convert_record
    # in dry_run mode just to get a status snapshot for the prompt.
    det_result = convert_record(classified, repo_root=repo_root, dry_run=True)
    quickref_path = (
        Path(__file__).resolve().parent / "dops_quickref.md"
    )
    try:
        quickref = quickref_path.read_text()
    except OSError:
        quickref = ""

    queue_root = Path(job.get("queue_root") or ".")

    # Load convert-flow playbooks (Step 27e). Convert agent reads
    # the whole payload up front, so we attach every entry that
    # declares flows=[convert] regardless of which phase it's about
    # (convert_phases triggers can refine attachment later if the
    # catalog grows large; today we have two convert entries and
    # bulk-attach is fine).
    from dportsv3.agent.playbooks import find_playbooks_dir, load_playbooks  # noqa: PLC0415
    playbooks_dir = find_playbooks_dir()
    convert_playbooks = load_playbooks(playbooks_dir, role="convert")
    _log_playbook_selection(
        queue_root, "convert", origin, convert_playbooks,
        job_id=job_path.name,
    )

    # Step 36-6: load the originating bundle's typed TriageResult so
    # the convert agent can see the actual build failure it's being
    # dispatched in response to. Without this, convert only sees
    # substrate signals and can't tell whether the dsynth failure
    # is a substrate problem (which it can address) or e.g. plist
    # drift / compile error (which it can't — but produces a
    # speculative overlay that fails reapply on the unrelated layer
    # anyway). best-effort: missing / version-mismatched results
    # degrade to "no triage context" — the agent sees the same
    # payload it saw before Step 36-6.
    triage_for_convert = None
    try:
        from dportsv3.agent.phase_result import (  # noqa: PLC0415
            TriageResult, load_phase_result,
        )
        triage_for_convert = load_phase_result(
            job.get("bundle_dir"), job.get("bundle_id"),
            "triage", TriageResult,
        )
    except Exception:
        triage_for_convert = None

    payload = convert_mod.build_convert_payload(
        origin=origin,
        repo_root=repo_root,
        classified_record=classified,
        deterministic_result=det_result,
        dops_quickref_text=quickref,
        playbooks_text=convert_playbooks.text,
        triage_result=triage_for_convert,
    )

    # Reuse the rich PatchEventDispatcher for the convert flow so
    # the activity log gets the same A1.T7 in=... → tool format the
    # patch agent uses. ``looks_env_suspicious`` and
    # ``invalidate_health_cache`` are no-ops here (convert is a
    # rewrite, not a build-driven loop), but the dispatcher's
    # contract requires callables.
    from dportsv3.agent.steps import PatchEventDispatcher
    dispatcher = PatchEventDispatcher(
        queue_root=queue_root,
        job_id=job_path.name,
        origin=origin,
        activity_log=activity_log,
        looks_env_suspicious=lambda _res: False,
        invalidate_health_cache=lambda: None,
        summarize_tool_call=_summarize_tool_call,
    )

    from dportsv3.agent import session_dump as _sd  # noqa: PLC0415
    result = convert_mod.run(
        payload,
        tier=tier, env=env, model=model,
        api_base=api_base, api_key=api_key,
        custom_llm_provider=provider,
        on_event=dispatcher,
        session_dump=_sd.make_dumper(
            bundle_id=job.get("bundle_id"),
            job_id=job_path.name,
            put_artifact=artifact_store_put,
        ),
    )
    if not result.success:
        # Rollback any port-subtree dirt the agent left behind. Without
        # this, an orphaned put_file'd overlay.dops persists in the
        # env's writable layer — the next convert retry sees both the
        # stale overlay AND the legacy Makefile.DragonFly (half-migrated
        # substrate), the patch agent then hits substrate_invariant and
        # burns its budget on a state convert never closed. The
        # symmetric rollback in _verify_conversion covers verify-time
        # failures; this branch covers convert-loop failures (budget
        # out, no proof block, proof missing required fields).
        _rollback_env_after_convert_failure(
            queue_root, env, origin, reason_code="llm_convert_failed",
            status=f"{result.status} ({result.raw_result.status})",
            job_id=job_path.name,
            bundle_id=job.get("bundle_id"),
            tokens_prompt=result.raw_result.usage.prompt_tokens,
            tokens_completion=result.raw_result.usage.completion_tokens,
            tokens_total=result.raw_result.usage.total_tokens,
        )
        return False, (
            f"llm_convert_failed: {result.status} "
            f"({result.raw_result.status})"
        )

    # Handler-side cleanup: act on files_removed from the agent's
    # Conversion Proof. The CONVERT_SYSTEM prompt explicitly delegates
    # legacy-file deletion to the handler (the agent has no tool to
    # remove port-subtree files). Without
    # this loop the LLM convert path produces a half-migrated
    # substrate (overlay.dops + Makefile.DragonFly together) that
    # poisons every subsequent patch job with substrate_invariant.
    _apply_files_removed(
        queue_root=queue_root, env=env, origin=origin,
        proof=result.proof or {},
    )

    # Step 36-4: stash token spend + files_removed on the job dict so
    # _verify_conversion can populate the typed ConvertResult without
    # changing its signature. Deterministic-convert path doesn't go
    # through here (no LLM ran) — these fields stay unset → tokens=0
    # and files_removed=[] in the result.
    job["convert_tokens_prompt"] = result.raw_result.usage.prompt_tokens
    job["convert_tokens_completion"] = result.raw_result.usage.completion_tokens
    job["convert_tokens_total"] = result.raw_result.usage.total_tokens
    proof_fr = (result.proof or {}).get("files_removed") or []
    if isinstance(proof_fr, list):
        job["convert_files_removed"] = [str(x) for x in proof_fr]

    # The agent wrote overlay.dops + the handler finalized legacy
    # cleanup. Verify via reapply.
    return _verify_conversion(job, origin)


# Step 37-1: framework-patch drift recovery -----------------------------------
#
# Compose's `patch.apply` op invokes /usr/bin/patch against the
# upstream framework file. When upstream churn drifts the diff's
# context (typical on big-port pkg-plist), `patch` rejects hunks and
# the dops op returns E_APPLY_PATCH_FAILED. Compose's stdout carries
# enough signal to identify which `diffs/*.diff` failed; we drop the
# corresponding `patch apply <path>` line from overlay.dops and
# retry. The dropped paths are recorded on ConvertResult as
# `deferred_patches` — INTENT, not authority — for the patch agent's
# later relevance pass (Step 37-3).

_HUNK_FAILED_DETAIL_RE = re.compile(
    r"Hunk\s+#(\d+)\s+failed(?:\s+at\s+(\d+))?", re.IGNORECASE,
)
_DEFERRED_PATCH_CONTENT_CAP = 16 * 1024  # bytes


def _failed_patch_diags(
    report: dict | None, origin: str,
) -> list[tuple[str, str]]:
    """Return ``[(diff_path, patch_message), ...]`` for each
    rejecting ``patch.apply`` op in compose's ``--json`` structured
    report. ``diff_path`` is relative to ``ports/<origin>/`` so it
    matches overlay.dops's ``patch apply <path>`` form;
    ``patch_message`` is the failing op's first diagnostic message
    (the patch tool's stdout, used to build a reject summary).

    Returns ``[]`` when the report is missing, malformed, or
    contains no patch.apply failures.
    """
    if not isinstance(report, dict):
        return []
    suffix = f"ports/{origin}/"
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for port in report.get("ports") or []:
        if not isinstance(port, dict) or port.get("origin") != origin:
            continue
        for row in port.get("dops_failed_op_results") or []:
            if not isinstance(row, dict) or row.get("kind") != "patch.apply":
                continue
            diags = row.get("diagnostics") or []
            if not diags or not isinstance(diags[0], dict):
                continue
            src = str(diags[0].get("source_path") or "")
            if not src:
                continue
            idx = src.find(suffix)
            rel = src[idx + len(suffix):] if idx >= 0 else src
            if not rel or rel in seen:
                continue
            seen.add(rel)
            out.append((rel, str(diags[0].get("message") or "")))
    return out


def _drop_patch_apply_from_overlay(text: str, path: str) -> tuple[str, bool]:
    """Remove the ``patch apply <path>`` line from ``overlay.dops``
    contents. Returns ``(new_text, dropped)``; ``dropped`` is False
    iff no matching line was found (caller can stop retrying).

    Conservative match: strips leading whitespace, requires the
    literal ``patch apply`` token followed by the path. Doesn't
    re-parse dops — this is a single-line removal that preserves
    indentation, comments, and other ops.
    """
    needle = f"patch apply {path}"
    out_lines: list[str] = []
    dropped = False
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        if not dropped and stripped == needle:
            dropped = True
            continue
        out_lines.append(line)
    return "".join(out_lines), dropped


def _read_diff_content(env: str, origin: str, path: str) -> str:
    """Read the framework diff file (e.g. ``diffs/pkg-plist.diff``)
    from the env's writable DeltaPorts checkout. Returns empty string
    on any IO error — caller treats empty original_content as "no
    context available."""
    try:
        from dportsv3.agent import worker  # noqa: PLC0415
        paths = worker.env_paths(env)
    except Exception:
        return ""
    diff_path = paths.deltaports / "ports" / origin / path
    if not diff_path.is_file():
        return ""
    try:
        return diff_path.read_text(errors="replace")
    except OSError:
        return ""


def _infer_target_file_from_diff(diff_content: str, fallback: str) -> str:
    """Pull the target filename out of the diff's ``+++ <name>`` line.
    Falls back to ``Path(fallback).stem`` (e.g. ``diffs/pkg-plist.diff``
    → ``pkg-plist``) if the diff doesn't parse."""
    for raw in diff_content.splitlines()[:8]:
        line = raw.strip()
        if line.startswith("+++ "):
            tok = line[4:].split("\t", 1)[0].split(maxsplit=1)[0].strip()
            if tok:
                return tok
    # Fallback: derive from the diff path's stem.
    stem = Path(fallback).name
    if stem.endswith(".diff"):
        stem = stem[: -len(".diff")]
    return stem or fallback


def _extract_reject_summary(diag: str, diff_path: str) -> str:
    """Build a short human-readable summary of which hunks failed for
    a given diff path. Best-effort: scans the diag for ``Hunk #N
    failed [at LLL]`` lines. We can't always attribute a specific
    hunk to a specific diff path (compose's stdout interleaves output
    from the patch tool with op-level framing), so we summarize what's
    present in the whole diag. Returns a single-line string."""
    matches = list(_HUNK_FAILED_DETAIL_RE.finditer(diag or ""))
    if not matches:
        return f"compose rejected {diff_path}"
    pairs: list[tuple[str, str | None]] = []
    seen_pairs: set[tuple[str, str | None]] = set()
    for m in matches:
        key = (m.group(1), m.group(2))
        if key not in seen_pairs:
            seen_pairs.add(key)
            pairs.append(key)
    hunk_ids = [f"#{n}" for n, _ in pairs]
    lines = [pos for _, pos in pairs if pos]
    msg = f"Hunks {' '.join(hunk_ids)} failed"
    if lines:
        msg += " at " + ", ".join(lines)
    return msg


def _drop_patch_apply_from_overlay_file(
    env: str, origin: str, path: str,
) -> bool:
    """Read ``overlay.dops``, drop the ``patch apply <path>`` line,
    write back. Returns True iff the file was found AND a line was
    actually removed."""
    try:
        from dportsv3.agent import worker  # noqa: PLC0415
        paths = worker.env_paths(env)
    except Exception:
        return False
    overlay = paths.deltaports / "ports" / origin / "overlay.dops"
    if not overlay.is_file():
        return False
    try:
        text = overlay.read_text()
    except OSError:
        return False
    new_text, dropped = _drop_patch_apply_from_overlay(text, path)
    if not dropped:
        return False
    try:
        overlay.write_text(new_text)
    except OSError:
        return False
    return True


def _materialize_with_defer_retry(
    env: str,
    origin: str,
    *,
    queue_root: Path,
    job_id: str | None,
    max_drops: int,
) -> tuple[dict, list]:
    """Wrap ``worker.materialize_dports`` with a bounded
    drop-and-retry loop. On compose failure carrying hunk-reject
    shape, identify which ``diffs/*.diff`` failed, capture its
    content + target file + reject summary BEFORE dropping the
    overlay reference, retry compose. Returns ``(final mat dict,
    list[DeferredPatch])``.

    Drops are capped at ``max_drops`` and we never drop the same path
    twice. The first non-hunk-reject failure exits the loop with the
    failure dict — caller's existing `_fail()` path handles the rest.
    """
    from dportsv3.agent import worker  # noqa: PLC0415
    from dportsv3.agent.phase_result import DeferredPatch  # noqa: PLC0415
    deferred: list = []
    seen: set[str] = set()
    mat: dict = {}
    for attempt in range(max_drops + 1):
        mat = worker.materialize_dports_with_report(env, origin)
        if mat.get("ok"):
            return mat, deferred
        diags = _failed_patch_diags(mat.get("report"), origin)
        next_drop = next(((p, m) for p, m in diags if p not in seen), None)
        if next_drop is None:
            _log_defer_skipped(queue_root, job_id, origin, attempt, mat, deferred)
            return mat, deferred
        if attempt >= max_drops:
            return mat, deferred  # cap reached
        path, msg = next_drop
        # Capture context BEFORE the overlay edit so the snapshot is
        # tied to the failure that triggered the drop.
        content = _read_diff_content(env, origin, path)
        dp = DeferredPatch(
            path=path,
            target_file=_infer_target_file_from_diff(content, fallback=path),
            original_content=content[:_DEFERRED_PATCH_CONTENT_CAP],
            reject_summary=_extract_reject_summary(msg, path),
        )
        if not _drop_patch_apply_from_overlay_file(env, origin, path):
            return mat, deferred  # overlay missing / line absent / write failed
        deferred.append(dp)
        seen.add(path)
        _log_defer_dropped(queue_root, job_id, origin, attempt, max_drops,
                           dp, deferred)
    return mat, deferred


def _log_defer_skipped(queue_root, job_id, origin, attempt, mat, deferred):
    """Activity row when the defer loop couldn't find a candidate.
    Carries enough signal for operators to tell "non-patch failure"
    from "report shape unexpected" without re-running compose."""
    try:
        activity_log(
            queue_root, "convert_patch_defer_skipped",
            f"no defer candidate for {origin} (attempt {attempt + 1}): "
            f"deferred_so_far={len(deferred)} rc={mat.get('rc')}",
            job_id=job_id,
            extra={
                "origin": origin,
                "attempt": attempt + 1,
                "rc": mat.get("rc"),
                "report_present": isinstance(mat.get("report"), dict),
                "deferred_so_far": [d.path for d in deferred],
                "stderr_tail": (mat.get("stderr_tail") or "")[-2048:],
                "stdout_tail": (mat.get("stdout_tail") or "")[-2048:],
            },
        )
    except Exception as exc:
        log(queue_root, "WARN",
            f"activity_log failed in convert_patch_defer_skipped: {exc}")


def _log_defer_dropped(queue_root, job_id, origin, attempt, max_drops, dp,
                       deferred):
    try:
        activity_log(
            queue_root, "convert_patch_deferred",
            f"deferred framework patch {dp.path} for {origin} "
            f"(attempt {attempt + 1}/{max_drops}): {dp.reject_summary}",
            job_id=job_id,
            extra={
                "origin": origin,
                "deferred_patch": dp.path,
                "target_file": dp.target_file,
                "reject_summary": dp.reject_summary,
                "deferred_so_far": [d.path for d in deferred],
                "attempt": attempt + 1,
                "max_drops": max_drops,
            },
        )
    except Exception as exc:
        log(queue_root, "WARN",
            f"activity_log failed in convert_patch_deferred: {exc}")


def _overlay_sha256(env: str, origin: str) -> str | None:
    """Best-effort sha256 of the just-written ``overlay.dops`` for
    the typed ``ConvertResult`` audit. Returns ``None`` when the
    overlay file isn't present (no convert wrote one) or can't be
    read; the result field is optional."""
    try:
        from dportsv3.agent import worker  # noqa: PLC0415
        paths = worker.env_paths(env)
    except Exception:
        return None
    overlay = paths.deltaports / "ports" / origin / "overlay.dops"
    if not overlay.is_file():
        return None
    try:
        return hashlib.sha256(overlay.read_bytes()).hexdigest()
    except OSError:
        return None


def _write_convert_phase_result(
    *,
    bundle_id: str | None,
    status: str,
    reapply_ok: bool,
    reason_code: str | None,
    overlay_sha256: str | None,
    files_removed: list[str],
    diag_tail: str | None,
    tokens_prompt: int,
    tokens_completion: int,
    tokens_total: int,
    deferred_patches: list | None = None,
) -> None:
    """Step 36-4: persist the typed ``ConvertResult`` to the bundle.

    ``deferred_patches`` is a list of ``DeferredPatch`` instances
    (Step 37-2). Empty list when no framework patches needed to be
    dropped during the reapply retry loop.

    Best-effort: a missing ``bundle_id`` (operator-fired convert
    against an origin with no failure bundle) means no destination
    to write to; any artifact-store failure is swallowed because the
    convert flow's terminal lifecycle event is the source of truth
    for the bundle's outcome — the typed result is an audit /
    downstream-consumer surface, not a load-bearing gate.
    """
    if not bundle_id:
        return
    try:
        from dportsv3.agent.phase_result import (  # noqa: PLC0415
            ConvertResult as _ConvertResultTyped,
            write_phase_result,
        )
        typed = _ConvertResultTyped(
            status=status,
            reapply_ok=reapply_ok,
            reason_code=reason_code,
            overlay_sha256=overlay_sha256,
            files_removed=list(files_removed),
            diag_tail=diag_tail,
            tokens_prompt=tokens_prompt,
            tokens_completion=tokens_completion,
            tokens_total=tokens_total,
            deferred_patches=list(deferred_patches or []),
        )
        write_phase_result(bundle_id, "convert", typed)
    except Exception:
        # Producer is intentionally best-effort: see docstring.
        pass


def _rollback_env_after_convert_failure(
    queue_root: Path, env: str, origin: str, *,
    reason_code: str, status: str,
    job_id: str | None = None,
    bundle_id: str | None = None,
    tokens_prompt: int = 0,
    tokens_completion: int = 0,
    tokens_total: int = 0,
) -> None:
    """Reset ports/<origin>/ to git HEAD + emit a convert_verify_failed
    activity row. Used when ``_run_llm_conversion`` exits before
    ``_verify_conversion`` runs — keeps the env clean for the next
    attempt and surfaces the reason to operators."""
    from dportsv3.agent import worker
    # Capture overlay_sha256 BEFORE reset_port wipes the agent's
    # overlay.dops back to git HEAD. Reading it after the reset
    # would either miss the file or return the HEAD-version hash —
    # neither audits what the convert agent actually wrote.
    overlay_sha = _overlay_sha256(env, origin)
    reset_extra: dict[str, object] = {
        "origin": origin, "env": env, "reason_code": reason_code,
    }
    try:
        reset = worker.reset_port(env, origin)
        reset_extra["reset_ok"] = bool(reset.get("ok"))
        if not reset.get("ok"):
            reset_extra["reset_error"] = (
                reset.get("error") or reset.get("stderr_tail", "")
            )[:300]
    except Exception as exc:
        reset_extra["reset_ok"] = False
        reset_extra["reset_error"] = f"raised: {exc!s}"[:300]
    try:
        activity_log(
            queue_root, "convert_verify_failed",
            f"{origin}: {status[:240]}",
            job_id=job_id,
            extra=reset_extra,
        )
    except Exception:
        pass
    # Step 36-4: persist the typed ConvertResult alongside the
    # activity row so downstream consumers (next-step retriage,
    # tracker UI) see the LLM-loop failure shape without parsing
    # activity-log extra dicts.
    _write_convert_phase_result(
        bundle_id=bundle_id,
        status=status,
        reapply_ok=False,
        reason_code=reason_code,
        overlay_sha256=overlay_sha,
        files_removed=[],
        diag_tail=None,
        tokens_prompt=tokens_prompt,
        tokens_completion=tokens_completion,
        tokens_total=tokens_total,
    )


def _apply_files_removed(
    *, queue_root: Path, env: str, origin: str, proof: dict,
) -> None:
    """Honor the proof's ``files_removed`` list — delete each port-
    subtree relpath the agent flagged for removal.

    CONVERT_SYSTEM tells the agent: "note the files that should be
    removed in `files_removed` — the handler will finalize the
    cleanup". This is that finalization. The agent has no port-
    subtree delete tool of its own; the handler is the only place
    this can land.

    Path safety: each entry must be a relpath under
    ports/<origin>/. Absolute paths, ``..`` segments, the freshly-
    written ``overlay.dops``, and any path escaping the port subtree
    are skipped with an activity log warning rather than executed.
    """
    from dportsv3.agent import worker
    requested = proof.get("files_removed") or []
    if not isinstance(requested, list):
        return
    if not requested:
        return
    try:
        paths = worker.env_paths(env)
    except Exception as exc:
        try:
            activity_log(
                queue_root, "convert_files_removed_failed",
                f"{origin}: could not resolve env paths: {exc!s}"[:240],
                extra={"origin": origin, "env": env,
                       "requested": requested[:32]},
            )
        except Exception:
            pass
        return
    port_dir = (paths.deltaports / "ports" / origin).resolve()
    if not port_dir.is_dir():
        return
    removed: list[str] = []
    skipped: list[dict] = []
    for raw in requested:
        if not isinstance(raw, str) or not raw.strip():
            skipped.append({"path": str(raw), "reason": "not-a-string"})
            continue
        rel = raw.strip()
        # Tolerate `ports/<origin>/...` prefix in case the agent
        # emitted a fully-qualified relpath; strip it.
        prefix = f"ports/{origin}/"
        if rel.startswith(prefix):
            rel = rel[len(prefix):]
        if rel.startswith("/") or ".." in rel.split("/"):
            skipped.append({"path": raw, "reason": "escapes-port-subtree"})
            continue
        if rel in {"overlay.dops", ""}:
            skipped.append({"path": raw, "reason": "refused-overlay-or-empty"})
            continue
        target = (port_dir / rel).resolve()
        try:
            target.relative_to(port_dir)
        except ValueError:
            skipped.append({"path": raw, "reason": "resolves-outside-port"})
            continue
        # Q2: safety guard for STATUS removal. STATUS encodes the
        # port's role (PORT/MASK/DPORT/LOCK). overlay.dops can carry
        # the same fact via the ``type`` directive, but if the agent's
        # dops doesn't match STATUS's declared type, deleting STATUS
        # would silently switch the port's behavior (e.g. MASK → PORT
        # = "start materializing the upstream we explicitly denied").
        # Refuse the delete and surface the mismatch.
        if rel == "STATUS":
            mismatch = _check_status_dops_type_parity(port_dir)
            if mismatch is not None:
                skipped.append({"path": raw, "reason": mismatch})
                continue
        if not target.exists():
            # Idempotent: missing target is fine; the agent asked for
            # "this should not be present", which it isn't.
            removed.append(rel)
            continue
        try:
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink()
            removed.append(rel)
        except OSError as exc:
            skipped.append({"path": raw, "reason": f"unlink-failed: {exc!s}"[:200]})
    try:
        activity_log(
            queue_root, "convert_files_removed",
            f"{origin}: removed={len(removed)} skipped={len(skipped)}",
            extra={
                "origin": origin, "env": env,
                "removed": removed[:32],
                "skipped": skipped[:32],
            },
        )
    except Exception:
        pass


_DOPS_TYPE_RE = re.compile(
    r"^\s*type\s+(port|mask|dport|lock)\s*(?:#.*)?$",
    re.MULTILINE,
)


def _read_dops_port_type(port_dir: Path) -> str | None:
    """Q2: read the ``type`` directive from ``ports/<origin>/overlay.dops``.

    Returns ``"port"`` / ``"mask"`` / ``"dport"`` / ``"lock"`` when
    the directive is present and well-formed, or ``None`` when the
    overlay.dops file is absent / unreadable / has no ``type``
    directive. Default type for an absent directive is ``"port"``
    in the planner; callers that need the effective type should
    treat ``None`` as ``"port"``.

    Light regex match rather than full parse — the planner does the
    authoritative parse at compose time. This is just for the
    handler-side STATUS-removal safety guard.
    """
    overlay = port_dir / "overlay.dops"
    if not overlay.is_file():
        return None
    try:
        text = overlay.read_text()
    except OSError:
        return None
    m = _DOPS_TYPE_RE.search(text)
    return m.group(1) if m else None


def _check_status_dops_type_parity(port_dir: Path) -> str | None:
    """Q2: safety guard for STATUS deletion during convert.

    Returns ``None`` when it is safe to delete STATUS — either both
    STATUS and overlay.dops agree on the port type, or the type
    encoded in STATUS is the default (``port``) and dops's absent
    directive resolves to the same default.

    Returns a short skip-reason string when the types disagree, so
    the caller can record it in ``skipped[]`` and surface it to the
    operator.

    Treats ``None`` from either side as ``"port"`` (the planner's
    default for an absent directive; the compat fallback for an
    absent or unrecognized STATUS token).
    """
    from dportsv3.agent.convert import read_status_port_type
    status_type = read_status_port_type(port_dir) or "port"
    dops_type = _read_dops_port_type(port_dir) or "port"
    if status_type == dops_type:
        return None
    return (
        f"status-type-mismatch: STATUS declares "
        f"`{status_type}`, overlay.dops carries "
        f"`{dops_type}` (deleting STATUS would switch the port's "
        f"behavior; fix the dops type directive first)"
    )


def _check_overlay_effective_ops(
    env: str, origin: str, env_target: str,
) -> str | None:
    """Re-plan the just-written overlay.dops against ``env_target``
    and return an error message if every op would be filtered out
    by compose's target-mismatch rule.

    Returns ``None`` when at least one op's scope matches
    ``env_target`` (or is ``@any``). Returns a single-line error
    when the overlay parses but is "dead on this env" — every op
    is scoped to a target compose isn't running for. Returns ``None``
    on best-effort failure paths (engine import error, can't read
    file, no env_target known) so the verify step doesn't get
    stricter than the underlying check can support.
    """
    if not env_target:
        # No target context — can't decide. Conservative: don't
        # refuse; the compose-stage diagnostics still surface
        # I_APPLY_TARGET_MISMATCH in stage output.
        return None
    try:
        from dportsv3.agent import worker as _worker  # noqa: PLC0415
        from dportsv3.engine.api import build_plan  # noqa: PLC0415
    except Exception:
        return None
    try:
        paths = _worker.env_paths(env)
    except Exception:
        return None
    overlay = paths.deltaports / "ports" / origin / "overlay.dops"
    if not overlay.is_file():
        # No overlay → convert didn't write one. The reapply step
        # above already accepted that (e.g. an empty / no-op
        # conversion). Nothing for us to check.
        return None
    try:
        text = overlay.read_text()
    except OSError:
        return None
    result = build_plan(text)
    if not result.ok or result.plan is None:
        # Parse / plan failure. Reapply already accepted the file,
        # which means it probably parsed there too — but if our
        # in-process plan disagrees, fail safe (don't claim the
        # overlay is effective when we can't tell).
        diag = next(
            (d.message for d in (result.diagnostics or [])
             if getattr(d, "severity", "") == "error"),
            "unknown plan error",
        )
        return f"overlay.dops failed in-process plan: {diag}"
    ops = result.plan.ops if result.plan else []
    if not ops:
        # Conversion that produced no ops (e.g. only port/type/reason
        # directives). Not necessarily wrong — but compose would
        # have nothing to do either. Surface so the operator
        # notices an empty conversion.
        return (
            f"overlay.dops parsed but produced zero plan ops — "
            f"the conversion did not translate any legacy artifact "
            f"into a dops op. Likely missing `mk` / `file` / "
            f"`patch apply` statements."
        )
    effective = [op for op in ops if op.target in ("@any", env_target)]
    if effective:
        return None
    # Build an informative error: list the distinct targets the
    # overlay's ops are scoped to, so the operator sees the
    # mismatch directly.
    scopes = sorted({op.target for op in ops if op.target})
    return (
        f"every op in overlay.dops is scoped to {scopes!r}; "
        f"compose for this bundle runs target {env_target!r}. "
        f"None of the convert's ops will apply. Convert agent "
        f"likely picked the wrong `target` directive — "
        f"unscoped legacy artifacts (`Makefile.DragonFly` with "
        f"no `.@xxx` suffix) should map to `target @any`, not "
        f"a specific target."
    )


def _verify_conversion(job: dict, origin: str) -> tuple[bool, str]:
    """Step 20e — verify a fresh conversion via compose (``reapply``).

    The convert agent's job is to produce a valid ``overlay.dops``.
    The correct validation is "does compose accept it?" — i.e. can
    ``reapply`` materialize a DPorts tree from the new overlay
    without dops parse/check/plan errors. We do NOT run dsynth_build
    here: build success or failure depends on factors outside the
    overlay (upstream source, dep graph, env health) and isn't a
    proxy for "the conversion is correct."

    Returns ``(True, status)`` when reapply succeeds, or when no
    env is available to verify in (offline / unit-test path).

    On any failure path, rolls back the env's ports/<origin>/ subtree
    to git HEAD via ``worker.reset_port`` and logs a
    ``convert_verify_failed`` activity row. Without rollback, the
    LLM convert agent's ``put_file overlay.dops`` lands in the env's
    writable layer permanently — the next convert retry would see a
    stale broken overlay and the next patch job would die at
    ``patch_preflight_dirty``.
    """
    env = resolve_env(job)
    if not env:
        return True, (
            "conversion succeeded (no dev-env resolved — "
            "verification skipped)"
        )

    from dportsv3.agent import worker

    queue_root = Path(job.get("queue_root") or ".")
    job_id = job.get("job_id")
    bundle_id = job.get("bundle_id")
    # Step 36-4: token spend + files_removed aren't visible from verify
    # itself; the caller (_run_llm_conversion) stashes them on the job
    # dict before invoking verify. Deterministic-convert path doesn't
    # write these — tokens stay at 0 + files_removed empty there (no
    # LLM ran, no proof block).
    tok_p = int(job.get("convert_tokens_prompt") or 0)
    tok_c = int(job.get("convert_tokens_completion") or 0)
    tok_t = int(job.get("convert_tokens_total") or 0)
    files_removed = list(job.get("convert_files_removed") or [])
    # Capture overlay_sha256 ONCE at entry, before any reset_port path
    # can wipe the agent-written overlay. The failure path's reset
    # rolls ports/<origin>/ back to git HEAD, which deletes the
    # overlay.dops the convert agent just put_file'd. Reading the
    # sha256 after reset would either return None (file gone) or the
    # HEAD version's hash — neither audits what we want, which is the
    # overlay the agent actually wrote. The success path also reuses
    # this captured value for symmetry (the file is still there post-
    # commit, but recomputing would add a redundant read).
    overlay_sha = _overlay_sha256(env, origin)
    # Pre-declared so the _fail() closure can reference it before the
    # retry loop (Step 37-1) populates it. Drops happen later in the
    # function; _fail is only called post-loop so the value is final
    # by then. List of DeferredPatch (Step 37-2).
    deferred_patches: list = []

    def _fail(status: str, reason_code: str, extra: dict | None = None) -> tuple[bool, str]:
        """Common failure tail: rollback env state, log to activity,
        return ``(False, status)``."""
        reset_extra: dict[str, object] = {"origin": origin, "env": env,
                                          "reason_code": reason_code}
        if extra:
            reset_extra.update(extra)
        try:
            reset = worker.reset_port(env, origin)
            reset_extra["reset_ok"] = bool(reset.get("ok"))
            if not reset.get("ok"):
                reset_extra["reset_error"] = (
                    reset.get("error") or reset.get("stderr_tail", "")
                )[:300]
        except Exception as exc:
            reset_extra["reset_ok"] = False
            reset_extra["reset_error"] = f"raised: {exc!s}"[:300]
        try:
            activity_log(
                queue_root, "convert_verify_failed",
                f"{origin}: {status[:240]}",
                job_id=job_id,
                extra=reset_extra,
            )
        except Exception:
            pass
        # Step 36-4: typed ConvertResult next to the activity row.
        # overlay_sha was captured pre-reset so it reflects the
        # agent's overlay, not the rolled-back HEAD.
        _write_convert_phase_result(
            bundle_id=bundle_id,
            status=status,
            reapply_ok=False,
            reason_code=reason_code,
            overlay_sha256=overlay_sha,
            files_removed=files_removed,
            diag_tail=(extra or {}).get("diag_tail")
                       if isinstance(extra, dict) else None,
            tokens_prompt=tok_p,
            tokens_completion=tok_c,
            tokens_total=tok_t,
            deferred_patches=deferred_patches,
        )
        return False, status

    # Step 37-1: framework-patch drift recovery. On the typical
    # hunk-reject failure (compose tried to apply a `diffs/*.diff`
    # whose context drifted off upstream), drop that `patch apply`
    # line from overlay.dops and retry compose. Cap at
    # DP_HARNESS_CONVERT_MAX_DROPS (default 3) so a port with many
    # bad patches still bails cleanly. Dropped paths become
    # `deferred_patches` on the typed ConvertResult — intent for the
    # patch agent's later relevance pass.
    max_drops = int(os.environ.get("DP_HARNESS_CONVERT_MAX_DROPS", "3"))
    mat, deferred_patches = _materialize_with_defer_retry(
        env, origin,
        queue_root=queue_root, job_id=job_id, max_drops=max_drops,
    )
    if deferred_patches:
        # The overlay file has been edited; refresh the hash so the
        # typed result reflects what compose actually accepted, not
        # what the agent originally wrote. If the refresh fails
        # (rare IO problem), propagate None — better an honest
        # "we don't know" than a hash that lies about disk content.
        refreshed = _overlay_sha256(env, origin)
        if refreshed is None:
            log(queue_root, "WARN",
                f"overlay_sha refresh failed for {origin} after "
                f"{len(deferred_patches)} deferred drops; audit "
                f"will report overlay_sha256=null")
        overlay_sha = refreshed
    if mat.get("ok"):
        # Effective-ops check (Step-C follow-up).
        # Compose can succeed with ZERO of the convert's ops actually
        # applying — every op gets `I_APPLY_TARGET_MISMATCH` if the
        # overlay declares `target @main` but compose runs for
        # `@2026Q2`. Compose's overall success is "dops parses + plan
        # ran"; it doesn't know we wanted ops to LAND. Re-plan the
        # overlay here against the env's effective target and refuse
        # if every op would be filtered out — convert wrote a dead
        # overlay (observed on archivers/liblz4 post-Step-C: dops
        # declared `target @main`, env was `@2026Q2`, every op
        # silently skipped).
        env_target = job.get("target") or ""
        eff = _check_overlay_effective_ops(env, origin, env_target)
        if eff is not None:  # None means "ok"; non-None is the error
            return _fail(
                f"conversion verified but overlay is dead on this env: {eff}",
                "effective_ops_empty",
                extra={"effective_ops_detail": eff[:300]},
            )
        # Stopgap pre-Step-26: commit the convert output to the env's
        # git so the next patch job's pre-job clean assertion
        # (design §5.1, runner step 25d-1) doesn't refuse on the
        # untracked overlay.dops. Without this the runner spawns a
        # patch job that dies immediately on patch_preflight_dirty
        # — observed thrash on devel/gperf 2026-05-25.
        bundle_dir = job.get("bundle_dir") or ""
        try:
            commit = worker.commit_port_changes(
                env, origin,
                f"convert: {origin} (auto-commit, bundle {bundle_dir or '?'})",
            )
            if not commit.get("ok"):
                try:
                    activity_log(
                        queue_root, "commit_port_changes_failed",
                        f"env commit failed for {origin}: "
                        f"{commit.get('error') or '(no error)'}",
                        job_id=job_id,
                        extra={
                            "origin": origin,
                            "env": env,
                            "error": commit.get("error"),
                            "stderr_tail": commit.get("stderr_tail"),
                        },
                    )
                except Exception:
                    pass
                return _fail(
                    f"conversion verified but env commit failed: "
                    f"{commit.get('error') or commit.get('stderr_tail', '')[:200]}",
                    "env_commit_failed",
                )
        except Exception as exc:
            try:
                activity_log(
                    queue_root, "commit_port_changes_failed",
                    f"env commit raised for {origin}: {exc!s}",
                    job_id=job_id,
                    extra={"origin": origin, "env": env,
                           "exception": str(exc)[:500]},
                )
            except Exception:
                pass
            return _fail(
                f"conversion verified but env commit raised: {exc!s}"[:300],
                "env_commit_raised",
            )
        # Success row so the analyzer / operator can confirm the
        # handoff cleared without scraping git history.
        try:
            activity_log(
                queue_root, "commit_port_changes_ok",
                f"env commit ok for {origin} "
                f"({'committed' if commit.get('committed') else 'nothing-to-commit'})",
                job_id=job_id,
                extra={
                    "origin": origin,
                    "env": env,
                    "committed": bool(commit.get("committed")),
                    "paths_changed": commit.get("paths_changed", []),
                    "bundle_dir": bundle_dir or None,
                },
            )
        except Exception:
            pass
        # Step 36-4: typed ConvertResult for the success path. Uses
        # the same pre-reset overlay_sha capture as the failure path
        # for symmetry; files_removed is what _apply_files_removed
        # actually consumed from the proof (stashed on job by
        # _run_llm_conversion).
        _write_convert_phase_result(
            bundle_id=bundle_id,
            status="verified",
            reapply_ok=True,
            reason_code=None,
            overlay_sha256=overlay_sha,
            files_removed=files_removed,
            diag_tail=None,
            tokens_prompt=tok_p,
            tokens_completion=tok_c,
            tokens_total=tok_t,
            deferred_patches=deferred_patches,
        )
        if deferred_patches:
            return True, (
                f"conversion verified by reapply (with "
                f"{len(deferred_patches)} deferred patch(es)); "
                f"committed to env"
            )
        return True, "conversion verified by reapply (compose accepted overlay.dops); committed to env"
    # reapply is a shell script that often prints errors to stdout
    # rather than stderr; include both so the failure is debuggable
    # from the lifecycle transition detail.
    stderr_tail = (mat.get("stderr_tail") or "").strip()
    stdout_tail = (mat.get("stdout_tail") or "").strip()
    diag = stderr_tail or stdout_tail or "(no output)"
    summary = _summarize_compose_failure(mat.get("report"), diag)
    return _fail(
        f"reapply failed: rc={mat.get('rc')!r} {summary!r}",
        "reapply_failed",
        extra={"rc": mat.get("rc"), "diag_tail": diag[-2048:]},
    )


# `materialize_dports_with_report` parses compose's --json output and
# exposes it at `mat["report"]`. When present, the first error of the
# first failing stage names the actual failure (e.g.
# `E_COMPOSE_APPLY_FAILED: devel/libunistring: 1 op(s) failed
# [op-0001-mk-target-set(mk.target.set)=E_APPLY_PARSE_FAILED]`).
# Without this path we fall back to last-non-empty-line scraping of
# stdout_tail, which on JSON output picks the closing `}` of the
# report document and surfaces it as the failure cause.
_COMPOSE_FOOTER_PREFIXES = ("modes:", "hint:", "stale:")


def _summarize_compose_failure(report, diag: str) -> str:
    if isinstance(report, dict):
        for stage in report.get("stages") or []:
            if stage.get("success"):
                continue
            errors = stage.get("errors") or []
            if errors:
                return str(errors[0])[:300]
        summary = report.get("summary") or {}
        if summary.get("errors"):
            return f"{summary.get('errors')} stage error(s); see diag_tail"
    # No structured report (older compose, JSON parse failed): fall
    # back to text-tail scraping with footer suppression. Keeps
    # behavior on bundles whose build host predates the --json path.
    def _is_footer(ln: str) -> bool:
        s = ln.strip()
        return s == "done" or any(
            s.startswith(p) for p in _COMPOSE_FOOTER_PREFIXES
        )
    lines = [ln.strip() for ln in diag.splitlines() if ln.strip()]
    if not lines:
        return "(no output)"
    last_line = next(
        (ln for ln in reversed(lines) if not _is_footer(ln)),
        lines[-1],
    )
    return last_line[:300]


def process_job(
    queue_root: Path,
    job_path: Path,
    sibling_paths: list[Path],
    dry_run: bool,
    playbooks_dir: Path | None,
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

    # Step 20: convert jobs are port-level — there's no failure
    # bundle attached and they don't need one. Step 11c: verify
    # jobs reference a bundle but don't need bundle_dir
    # materialized (run_verify_fix fetches the diff via tracker).
    if (job_type not in ("convert", "verify")
            and bundle_dir is None and not bundle_id):
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
            payload = build_patch_payload(bundle_dir, playbooks_dir, job)
        elif job_type == "convert":
            log(queue_root, "INFO",
                f"[dry-run] type=convert, origin={origin}, target={job.get('target','')}")
            move_job(job_path, "pending")
            update_runner_status("idle", job_id=None, stage=None)
            return
        else:
            payload = build_triage_payload(bundle_dir, playbooks_dir, job)

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
            queue_root, job_path, sibling_paths, job, bundle_dir, playbooks_dir,
        )
        # Step 30 slice 4: patch is terminal for the bundle branch
        # — delivery.diff was captured (slice 2) on success, and on
        # failure the branch's state is moot. Drop either way.
        _drop_bundle_branch_for_job(
            queue_root=queue_root, job_id=job_path.name,
            env=resolve_env(job),
            bundle_id=job.get("bundle_id") or None,
            job_type="patch",
            reason="patch_success" if success else "patch_failure",
        )
    elif job_type == "triage":
        start_event = JobEvent.TRIAGE_START
        _apply_transition(job_path.name, start_event)
        for s in sibling_paths:
            _apply_transition(s.name, start_event)
        success, status = process_triage_job(
            queue_root, job_path, sibling_paths, job, bundle_dir, playbooks_dir,
        )
    elif job_type == "convert":
        # Step 20c: convert jobs are port-level (no bundle, no
        # siblings to fan out to in practice). Fire CONVERT_START,
        # run the deterministic handler, then fire OK / GAVE_UP
        # based on the result.
        start_event = JobEvent.CONVERT_START
        _apply_transition(job_path.name, start_event)
        for s in sibling_paths:
            _apply_transition(s.name, start_event)
        success, status = process_convert_job(
            queue_root, job_path, sibling_paths, job,
        )
        # Step 30 slice 4: convert SUCCESS keeps the branch — the
        # post-convert retriage's patch job (if any) will reuse it.
        # Convert FAILURE drops it — the partial commits are not
        # useful, and the next attempt should start fresh from
        # base. If retriage routes to MANUAL (no patch follows),
        # the branch persists until the env is rebuilt; that's the
        # explicit "stale branch out of scope" case.
        if not success:
            _drop_bundle_branch_for_job(
                queue_root=queue_root, job_id=job_path.name,
                env=resolve_env(job),
                bundle_id=job.get("bundle_id") or None,
                job_type="convert",
                reason="convert_failure",
            )
        # Step 28-extra: when process_convert_job short-circuits via
        # SKIP_ORIGIN_LOCKED (operator took over the (target, origin)
        # before this convert ran), the job is already at DEAD with
        # retire_reason='origin_locked'. Firing CONVERT_OK on top of
        # that raises IllegalTransition (logged as a warning at
        # _apply_transition); _resume_deferred_triage is also
        # pointless because the resumed triage would itself
        # skip-lock-bypass. Detect the sentinel status string and
        # skip both.
        origin_locked_exit = (
            success and status.startswith("origin_locked_by:")
        )
        if not origin_locked_exit:
            finish_event = (
                JobEvent.CONVERT_OK if success else JobEvent.CONVERT_GAVE_UP
            )
            # Include bundle_id in failure detail so lifecycle's
            # _EVENT_TO_RESOLUTION propagation writes
            # resolution='convert_gave_up' onto the bundle row. Without
            # this, the bundle stays at resolution=NULL and
            # can_retry/can_take_over evaluate False, leaving operators
            # with no UI surface to re-trigger or claim the failure.
            fail_detail = (
                None if success else {
                    "status": status,
                    "bundle_id": job.get("bundle_id"),
                }
            )
            _apply_transition(
                job_path.name, finish_event,
                detail=fail_detail,
            )
            for s in sibling_paths:
                _apply_transition(
                    s.name, finish_event,
                    detail=fail_detail,
                )
            # Step 20d auto-resume: if convert succeeded, re-enqueue
            # the triage that was parked at DEAD with retire_reason
            # 'deferred_for_convert'. The fresh triage runs against
            # the now-converted port and proceeds normally (classify
            # returns 'converted'). Failures here are logged but do
            # not derail the dispatcher; the operator can re-enqueue
            # manually.
            if success:
                convert_target = job.get("target") or os.environ.get(
                    "DPORTSV3_TRACKER_TARGET", "",
                )
                try:
                    _resume_deferred_triage(
                        queue_root, job_path.name, origin, convert_target,
                    )
                except Exception as exc:
                    log(queue_root, "WARN",
                        f"_resume_deferred_triage raised: {exc}")
    elif job_type == "verify":
        # Step 11c: operator-triggered fix verification. Calls
        # dportsv3.verify_fix.run_verify_fix() in-process — no
        # subprocess. The job's terminal state is VERIFY_FIX_OK
        # whenever the orchestrator ran end-to-end (regardless of
        # the underlying dsynth verdict, which lives on
        # bundles.verification_status via the Slice 2 endpoint).
        # VERIFY_FIX_GAVE_UP only fires if the orchestrator itself
        # raised (env gone, tracker unreachable, etc.).
        #
        # Note: run_verify_fix raises VerifyFixError (an Exception
        # subclass), not SystemExit, so the except-Exception below
        # catches its caller-recoverable failures without killing
        # the runner process. SystemExit would have escaped the
        # runner main loop and exited the process — that bug was
        # what made the runner silently disappear on the first
        # verify run.
        from dportsv3.verify_fix import run_verify_fix  # noqa: PLC0415

        start_event = JobEvent.VERIFY_FIX_START
        _apply_transition(job_path.name, start_event)
        bundle_id = job.get("bundle_id", "")
        verify_env = job.get("dev_env", "")

        def _flag_bundle_verification_failed() -> None:
            # Flip bundle.verification_status so the UI stops showing
            # "in flight forever" — the orchestrator never got far
            # enough to POST a result to /verification itself.
            if bundle_id and _state_db_conn is not None:
                try:
                    with _state_db_lock:
                        now = datetime.now(timezone.utc).isoformat()
                        _state_db_conn.execute(
                            """UPDATE bundles SET
                                   verification_status = 'verification_failed',
                                   verification_at = ?,
                                   last_seen_at = ?
                               WHERE bundle_id = ?""",
                            (now, now, bundle_id),
                        )
                        _state_db_conn.commit()
                except sqlite3.Error as db_exc:
                    log(queue_root, "WARN",
                        f"failed to mark bundle {bundle_id} "
                        f"verification_failed: {db_exc}")

        # Verify replays changes.diff (the complete branch-vs-base
        # canonical artifact) on a throwaway ``bundle/<id>-verify``
        # branch cut fresh from base. This decouples verify from the
        # patch agent's ``bundle/<id>`` branch, which Slice 4's
        # terminal sweep may already have dropped — and from master,
        # which verify must never build against directly. We record
        # the ref the env was on so the end-of-run drop can restore it.
        checkout_ok, verify_prev_ref = _checkout_verify_branch_for_job(
            queue_root=queue_root, job_id=job_path.name,
            env=verify_env or None,
            bundle_id=bundle_id or None,
        )
        try:
            if not checkout_ok:
                # Abort BEFORE replay. Without a fresh base branch we
                # can't prove the diff applies cleanly, so any verdict
                # would be meaningless — and replaying onto whatever
                # branch is current would also let apply-and-build's
                # cleanup reset the wrong tree.
                raise _VerifyBranchUnavailable()
            result = run_verify_fix(
                bundle_id=bundle_id,
                env=verify_env,
                tracker_url=_tracker_url(),
            )
            success, status = True, (
                "verified" if result.ok else "verification_failed"
            )
            finish_event = JobEvent.VERIFY_FIX_OK
            failed_stage = result.failure_stage()
            detail = {
                "ok": result.ok,
                "failed_stage": failed_stage,
                "apply_exit": result.apply_exit,
                "reapply_exit": result.reapply_exit,
                "dsynth_exit": result.dsynth_exit,
                "applied_diff_sha256": result.applied_diff_sha256,
                "log_path": result.log_path,
                "stderr_tail": result.stderr_tail,
                "posted": result.posted,
                "bundle_id": bundle_id,
                "env": verify_env,
            }
            if result.ok:
                msg = (f"verify OK for {bundle_id} (env={verify_env}, "
                       f"dsynth_exit=0)")
            else:
                # Name which stage failed so the activity row is
                # actionable without opening the log.
                stage_exit = {
                    "apply": result.apply_exit,
                    "reapply": result.reapply_exit,
                    "dsynth": result.dsynth_exit,
                }.get(failed_stage)
                msg = (f"verify FAIL for {bundle_id} (env={verify_env}): "
                       f"{failed_stage} stage exit={stage_exit}")
            try:
                activity_log(
                    queue_root, "verify_complete", msg,
                    job_id=job_path.name, extra=detail,
                )
            except Exception as log_exc:
                log(queue_root, "WARN",
                    f"activity_log failed for verify_complete: {log_exc}")
        except _VerifyBranchUnavailable:
            # Checkout of the fresh verify branch failed (logged by
            # _checkout_verify_branch_for_job). Fail the job (→ DEAD
            # via the shared tail) and mark the bundle as
            # verification_failed so the UI stops showing "in flight".
            success = False
            status = "verify aborted: could not establish fresh verify branch"
            finish_event = JobEvent.VERIFY_FIX_GAVE_UP
            detail = {
                "reason": status,
                "bundle_id": bundle_id,
                "env": verify_env,
            }
            log(queue_root, "ERROR",
                f"verify job {job_path.name} aborted: "
                f"verify-branch checkout failed")
            try:
                activity_log(
                    queue_root, "verify_failed",
                    f"verify ABORTED for {bundle_id}: "
                    f"verify-branch checkout failed",
                    job_id=job_path.name, extra=detail,
                )
            except Exception as log_exc:
                log(queue_root, "WARN",
                    f"activity_log failed for verify abort: {log_exc}")
            _flag_bundle_verification_failed()
        except Exception as exc:
            success, status = False, f"verify_fix raised: {exc}"
            finish_event = JobEvent.VERIFY_FIX_GAVE_UP
            err_msg = str(exc)[:500]
            detail = {
                "reason": err_msg,
                "exception_type": type(exc).__name__,
                "bundle_id": bundle_id,
                "env": verify_env,
            }
            log(queue_root, "ERROR",
                f"verify job {job_path.name} failed: "
                f"{type(exc).__name__}: {err_msg}")
            try:
                activity_log(
                    queue_root, "verify_failed",
                    f"verify FAILED for {bundle_id}: {err_msg}",
                    job_id=job_path.name, extra=detail,
                )
            except Exception as log_exc:
                log(queue_root, "WARN",
                    f"activity_log failed for verify_failed: {log_exc}")
            _flag_bundle_verification_failed()
        _apply_transition(job_path.name, finish_event, detail=detail)
        # The throwaway verify branch is done either way. Drop it and
        # restore the ref the env was on before verify (verify_prev_ref)
        # so we don't strand the env on a deleted branch or on base
        # when the operator had something else checked out.
        _drop_verify_branch_for_job(
            queue_root=queue_root, job_id=job_path.name,
            env=verify_env or None,
            bundle_id=bundle_id or None,
            restore_ref=verify_prev_ref,
            reason="verify_complete" if success else "verify_failure",
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
    parser.add_argument(
        "--playbooks-dir",
        help="Path to docs/agent-playbooks/ (default: auto-detect via "
             "find_playbooks_dir)",
    )
    parser.add_argument(
        "--env",
        help=(
            "Default dev-env name when a job doesn't carry one and "
            "the tracker hasn't recorded an active env. Trackerless "
            "escape hatch — for tracker-attached runs prefer setting "
            "the active env in the tracker UI."
        ),
    )
    args = parser.parse_args(argv)

    # Stash the CLI env on the module so handlers reach it via
    # cli_env_default() without threading it through every signature.
    global _CLI_ENV_DEFAULT
    _CLI_ENV_DEFAULT = args.env or None

    queue_root = Path(args.queue_root)

    for subdir in ["pending", "inflight", "done", "failed"]:
        d = queue_root / subdir
        if not d.exists():
            print(f"error: queue directory missing: {d}", file=sys.stderr)
            return 1

    init_state_db(queue_root)
    # Populate env_health_status with stub rows for every env on
    # disk so the tracker UI dropdown reflects the runner's view of
    # available envs even before the first health probe fires.
    if _state_db_conn is not None:
        try:
            n = stub_unprobed_envs()
            if n:
                log(queue_root, "INFO",
                    f"stubbed {n} previously-unseen env(s) into env_health_status")
        except Exception as exc:
            log(queue_root, "WARN", f"stub_unprobed_envs failed: {exc}")
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

        # Step 10a: also reap stale QUEUED rows whose .job file has
        # vanished from pending/ (gated on age + missing-file). Catches
        # the failure mode where a row records a path the host runner
        # never scans (in-chroot paths from earlier deployments) and
        # blocks _has_active_same_origin_job indefinitely.
        try:
            max_age = int(os.environ.get(
                "DPORTSV3_STALE_QUEUED_MAX_AGE_SECONDS", "3600",
            ))
            with _state_db_lock:
                stale = lifecycle.reap_stale_queued(
                    _state_db_conn, queue_root,
                    max_age_seconds=max_age,
                    actor=f"runner-{os.getpid()}",
                )
            if stale:
                log(queue_root, "INFO",
                    f"reaped {len(stale)} stale queued job(s): "
                    + ", ".join(stale[:5])
                    + ("..." if len(stale) > 5 else ""))
        except Exception as exc:
            log(queue_root, "WARN", f"reap_stale_queued failed: {exc}")

    if args.playbooks_dir:
        playbooks_dir = Path(args.playbooks_dir)
        if not playbooks_dir.exists():
            print(
                f"warning: playbooks directory not found: {playbooks_dir}",
                file=sys.stderr,
            )
            playbooks_dir = None
    else:
        from dportsv3.agent.playbooks import find_playbooks_dir  # noqa: PLC0415
        playbooks_dir = find_playbooks_dir()

    triage_model = os.environ.get("DP_HARNESS_TRIAGE_MODEL") or "<unset>"
    patch_model_env = os.environ.get("DP_HARNESS_PATCH_MODEL")
    if patch_model_env:
        patch_model = patch_model_env
    elif triage_model != "<unset>":
        patch_model = f"{triage_model} (fallback from triage)"
    else:
        patch_model = "<unset>"
    # Convert flow falls back through the same chain (Step 20):
    # DP_HARNESS_CONVERT_MODEL → PATCH → TRIAGE.
    convert_model_env = os.environ.get("DP_HARNESS_CONVERT_MODEL")
    if convert_model_env:
        convert_model = convert_model_env
    elif patch_model_env:
        convert_model = f"{patch_model_env} (fallback from patch)"
    elif triage_model != "<unset>":
        convert_model = f"{triage_model} (fallback from triage)"
    else:
        convert_model = "<unset>"
    playbooks_info = str(playbooks_dir) if playbooks_dir else "none"
    log(queue_root, "INFO",
        f"starting runner (once={args.once}, dry_run={args.dry_run}, "
        f"triage_model={triage_model}, patch_model={patch_model}, "
        f"convert_model={convert_model}, playbooks={playbooks_info})")
    activity_log(queue_root, "runner_start",
                 f"Runner started (triage={triage_model}, "
                 f"patch={patch_model}, convert={convert_model})")
    update_runner_status("idle", job_id=None, stage=None)

    # Runner-level dev-env for the health probe + dsynth-busy gate.
    # Resolved from the env_resolver with no job context — picks up
    # the tracker active env, --env CLI flag, or auto-picked single
    # env. Re-resolved on each gate tick so UI changes take effect
    # without a runner restart. Empty = no gate (no env to watch);
    # operator gets a one-time WARN at startup.
    runner_env = resolve_env(None) or ""
    if not runner_env:
        log(queue_root, "WARN",
            "no dev-env resolved at runner start; dsynth-busy gating "
            "disabled. Concurrent dsynth runs in the same env may "
            "corrupt buildbase. Set an active env in the tracker UI "
            "or pass --env NAME.")

    _last_busy_reason = ""
    _last_health_reason = ""
    health_cache_seconds = int(
        os.environ.get("DP_HARNESS_HEALTH_CACHE_SECONDS", "60")
    )

    def _gate_blocked() -> bool:
        nonlocal _last_busy_reason, _last_health_reason
        # Re-resolve per cycle (cached for 1 s) so operator selection
        # in the tracker UI takes effect without a runner restart.
        runner_env = resolve_env_for_gate() or ""
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
                    process_job(queue_root, lead, siblings, args.dry_run, playbooks_dir)
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
                process_verify_requests(queue_root)
                batch = claim_next_job_batch(queue_root)
                if batch:
                    lead, siblings = batch
                    process_job(queue_root, lead, siblings, args.dry_run, playbooks_dir)
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
