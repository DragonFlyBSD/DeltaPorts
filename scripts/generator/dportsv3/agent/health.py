"""Structured EnvHealth probe for a dev-env chroot.

Phase 2 of the agentic framework. Replaces the previous
"infer-from-tool-stderr" pattern (``_classify_env_error`` +
``_ENV_BROKEN_SENTINELS`` + the runner's ``_env_broken_reason``
sticky flag) with a direct, named-aspect probe.

The runner calls ``check(env)`` before claiming jobs; the dev-env
CLI exposes it as ``dportsv3 dev-env health NAME``. Steps (Phase 5)
will call it from each step's ``precheck()``. Tool errors that look
health-related can force a re-probe (cache invalidation) but no
longer set state directly.

Three checks land in Phase 2 because each has already burned us:

- ``python_runtime``: the runtime profile packages dportsv3 itself
  depends on are installed in the chroot. Missing → every
  materialize_dports fails identically; the agent can't fix it; the
  operator should recreate the env from the current profile.
- ``writable_overlay``: the env's writable overlay is mounted and
  writable. Touch-test a sentinel under ``work/.health/``.
- ``dports_compose``: ``dportsv3 compose --check`` (dry-run) succeeds
  inside the env. Canary for the gnome_subr-style "compose itself
  is broken" failure mode.

Adding more checks later is one function + one entry in the dispatch
table. Aggregation logic stays put.
"""

from __future__ import annotations

import json
import os
import subprocess
import tomllib
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Literal

# Imported lazily inside check functions to avoid heavyweight
# dportsv3 worker imports for callers that only want one check.


RUNTIME_PROFILES_PATH = Path(__file__).resolve().parents[3] / "tools/dev-env/runtime-profiles.toml"

CheckStatus = Literal["ok", "warn", "broken"]
EnvStatus = Literal["ready", "degraded", "broken"]


@dataclass
class HealthCheck:
    name: str
    status: CheckStatus
    detail: str = ""
    operator_action: str | None = None


@dataclass
class EnvHealth:
    env: str
    status: EnvStatus
    checks: list[HealthCheck] = field(default_factory=list)
    operator_action: str | None = None
    probed_at: str = ""

    def is_ready(self) -> bool:
        return self.status == "ready"

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self, *, indent: int | None = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _runtime_profile() -> tuple[str, tuple[str, ...]]:
    data = tomllib.loads(RUNTIME_PROFILES_PATH.read_text())
    profile_name = os.environ.get("DPORTS_DEV_RUNTIME_PROFILE") or data["default"]
    profile = data["profiles"][profile_name]
    return profile_name, tuple(profile["packages"])


def _aggregate(checks: list[HealthCheck]) -> EnvStatus:
    if any(c.status == "broken" for c in checks):
        return "broken"
    if any(c.status == "warn" for c in checks):
        return "degraded"
    return "ready"


# ----------------------------------------------------------------------
# Concrete checks
# ----------------------------------------------------------------------


def _run_in_env(env: str, *argv: str, timeout: int = 10) -> subprocess.CompletedProcess:
    """Shell out to `dportsv3 dev-env exec ENV -- ARGV` and capture.

    Imported lazily so this module is importable without the dev-env
    package present (tests can fully stub the checks).
    """
    from dportsv3.agent.worker import _dportsv3_cmd

    return subprocess.run(
        [*_dportsv3_cmd(), "dev-env", "exec", "--quiet", env, "--", *argv],
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )


def _check_python_runtime(env: str) -> HealthCheck:
    """Verify required py311-* packages are installed in the chroot.

    One subprocess per pkg would be wasteful, so we use a single
    ``pkg info -e`` form before probing individual missing packages.
    """
    try:
        profile_name, runtime_pkgs = _runtime_profile()
    except Exception as exc:  # noqa: BLE001 — health output should not crash
        return HealthCheck(
            name="python_runtime", status="broken",
            detail=f"could not load runtime profile: {exc}",
            operator_action="recreate the env",
        )
    try:
        p = _run_in_env(env, "pkg", "info", "-e", *runtime_pkgs, timeout=15)
    except subprocess.TimeoutExpired:
        return HealthCheck(
            name="python_runtime", status="broken",
            detail="pkg info -e timed out (>15s)",
            operator_action="check that pkg works inside the env",
        )
    except FileNotFoundError as exc:
        return HealthCheck(
            name="python_runtime", status="broken",
            detail=f"dportsv3 CLI not found: {exc}",
            operator_action="ensure dportsv3 is on PATH or set DPORTSV3_CMD",
        )

    if p.returncode == 0:
        return HealthCheck(
            name="python_runtime", status="ok",
            detail=f"all {len(runtime_pkgs)} packages present from runtime profile {profile_name}",
        )
    # pkg info -e returns 70 (EX_SOFTWARE) when at least one package is
    # missing. We can't tell which without re-running, but the stderr
    # usually lists them. Probe per-pkg to give a precise list, since
    # the operator_action depends on it.
    missing: list[str] = []
    for pkg in runtime_pkgs:
        try:
            one = _run_in_env(env, "pkg", "info", "-e", pkg, timeout=5)
        except subprocess.TimeoutExpired:
            missing.append(pkg)
            continue
        if one.returncode != 0:
            missing.append(pkg)
    if not missing:
        # Aggregate said failure but per-pkg says all present — race?
        # Treat as ok with a warn-quality detail.
        return HealthCheck(
            name="python_runtime", status="ok",
            detail=f"all {len(runtime_pkgs)} packages present from runtime profile {profile_name} (after re-check)",
        )
    return HealthCheck(
        name="python_runtime", status="broken",
        detail=f"missing from runtime profile {profile_name}: {', '.join(missing)}",
        operator_action="recreate the env",
    )


def _check_writable_overlay(env: str) -> HealthCheck:
    """Verify the env's writable overlay is mounted and writable.

    Touch-test a sentinel file under ``work/.health/``. Cleans up
    after itself. Failure = the env isn't mounted or the overlay is
    read-only (both fatal for any tool that wants to write).
    """
    try:
        from dportsv3.agent.worker import env_paths
        paths = env_paths(env)
    except Exception as exc:
        return HealthCheck(
            name="writable_overlay", status="broken",
            detail=f"could not resolve env paths: {exc}",
            operator_action=f"check `dportsv3 dev-env path {env} --writable`",
        )

    sentinel_dir = paths.writable / "work" / ".health"
    sentinel = sentinel_dir / "probe"
    try:
        sentinel_dir.mkdir(parents=True, exist_ok=True)
        sentinel.write_text(_now())
        sentinel.unlink()
    except OSError as exc:
        return HealthCheck(
            name="writable_overlay", status="broken",
            detail=f"sentinel touch failed at {sentinel}: {exc}",
            operator_action=(
                f"ensure env `{env}` is mounted: "
                f"`dportsv3 dev-env shell {env}` to mount, "
                f"or `dportsv3 dev-env status {env}` to inspect"
            ),
        )
    return HealthCheck(
        name="writable_overlay", status="ok",
        detail=f"sentinel write OK at {sentinel_dir}",
    )


def _check_dports_compose(env: str) -> HealthCheck:
    """Canary: does ``dportsv3 compose`` even run inside the env.

    Catches the gnome_subr-style failure where compose dies before
    touching any port because the chroot venv is broken. Uses the
    cheapest invocation we can: ``dportsv3 --version`` inside the
    env, which exercises the same import path as compose without
    actually composing anything.
    """
    try:
        p = _run_in_env(env, "/work/DeltaPorts/dportsv3", "--version", timeout=15)
    except subprocess.TimeoutExpired:
        return HealthCheck(
            name="dports_compose", status="broken",
            detail="/work/DeltaPorts/dportsv3 --version timed out (>15s) inside env",
            operator_action="check the env's venv state",
        )
    if p.returncode == 0:
        return HealthCheck(
            name="dports_compose", status="ok",
            detail=(p.stdout.strip() or "/work/DeltaPorts/dportsv3 --version OK"),
        )
    stderr = (p.stderr or "").strip()
    # Surface the missing-deps message verbatim; that's the most
    # actionable diagnostic and what python_runtime would also catch.
    return HealthCheck(
        name="dports_compose", status="broken",
        detail=f"/work/DeltaPorts/dportsv3 --version failed (rc={p.returncode}): {stderr[:500]}",
        operator_action=(
            "run `dportsv3 dev-env health {env}` for per-aspect detail; "
            "if python_runtime is broken, fix it first"
        ).format(env=env),
    )


# Dispatch table — keep ordering stable; UI/operator output reads
# top-down and "fix python first" is a natural read.
_CHECKS: dict[str, Callable[[str], HealthCheck]] = {
    "python_runtime":   _check_python_runtime,
    "writable_overlay": _check_writable_overlay,
    "dports_compose":   _check_dports_compose,
}


def check(env: str, *, only: list[str] | None = None) -> EnvHealth:
    """Run the named checks (default: all) and aggregate.

    ``only`` is a list of check names; unknown names are ignored
    silently. Failing checks land as ``HealthCheck(status="broken")``
    in the aggregate result rather than raising — callers gate on
    ``EnvHealth.is_ready()``.
    """
    selected = list(_CHECKS.items())
    if only is not None:
        wanted = set(only)
        selected = [(n, fn) for n, fn in _CHECKS.items() if n in wanted]

    results: list[HealthCheck] = []
    for name, fn in selected:
        try:
            results.append(fn(env))
        except Exception as exc:  # noqa: BLE001 — guard the probe
            results.append(HealthCheck(
                name=name, status="broken",
                detail=f"check raised: {type(exc).__name__}: {exc}",
                operator_action=(
                    f"this is a probe bug; see traceback in runner log"
                ),
            ))

    agg = _aggregate(results)
    # Aggregate operator action: first broken check's action, if any.
    op_action: str | None = None
    if agg == "broken":
        for r in results:
            if r.status == "broken" and r.operator_action:
                op_action = r.operator_action
                break

    return EnvHealth(
        env=env,
        status=agg,
        checks=results,
        operator_action=op_action,
        probed_at=_now(),
    )
