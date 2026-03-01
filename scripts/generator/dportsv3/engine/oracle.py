"""Constrained bmake oracle checks for post-rewrite validation."""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Literal

OracleProfile = Literal["off", "local", "ci"]
_VALID_PROFILES = {"off", "local", "ci"}
_CI_PROBE_VARIABLES = ("PORTNAME", "CATEGORIES", "MAINTAINER")


@dataclass
class OracleResult:
    """Result row for bmake oracle execution."""

    ok: bool
    profile: OracleProfile
    checks_run: int = 0
    failures: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    skipped: bool = False
    unavailable: bool = False


RunCommand = Callable[[list[str], Path], subprocess.CompletedProcess[str]]


def normalize_oracle_profile(value: str | None) -> OracleProfile:
    """Normalize requested oracle profile."""
    if value is None:
        return "local"
    candidate = value.strip().lower()
    if candidate not in _VALID_PROFILES:
        raise ValueError(
            f"invalid oracle profile: {value!r} (expected off, local, or ci)"
        )
    return candidate  # type: ignore[return-value]


def _default_run_command(
    command: list[str], cwd: Path
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=False,
    )


def _format_failure(
    command: list[str], completed: subprocess.CompletedProcess[str]
) -> str:
    detail = (
        completed.stderr.strip() or completed.stdout.strip() or "bmake command failed"
    )
    return f"{' '.join(command)} -> {detail}"


def run_bmake_oracle(
    port_root: Path,
    *,
    profile: str = "local",
    run_command: RunCommand | None = None,
    bmake_path: str | None = None,
) -> OracleResult:
    """Run constrained bmake checks for one rewritten port tree."""
    normalized = normalize_oracle_profile(profile)
    if normalized == "off":
        return OracleResult(ok=True, profile=normalized, skipped=True)

    makefile = port_root / "Makefile"
    if not makefile.exists() or not makefile.is_file():
        return OracleResult(
            ok=True,
            profile=normalized,
            skipped=True,
            warnings=["Makefile not found for oracle check"],
        )

    executable = bmake_path or shutil.which("bmake")
    if executable is None:
        if normalized == "ci":
            return OracleResult(
                ok=False,
                profile=normalized,
                unavailable=True,
                failures=["bmake not found in PATH"],
            )
        return OracleResult(
            ok=True,
            profile=normalized,
            skipped=True,
            unavailable=True,
            warnings=["bmake not found in PATH"],
        )

    runner = run_command or _default_run_command
    commands: list[list[str]] = [[executable, "-n", "-f", "Makefile"]]
    if normalized == "ci":
        for variable in _CI_PROBE_VARIABLES:
            commands.append([executable, "-f", "Makefile", "-V", variable])

    result = OracleResult(ok=True, profile=normalized)
    for command in commands:
        completed = runner(command, port_root)
        result.checks_run += 1
        if completed.returncode != 0:
            result.failures.append(_format_failure(command, completed))

    result.ok = not result.failures
    return result
