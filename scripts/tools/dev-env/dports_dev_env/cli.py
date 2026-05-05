from __future__ import annotations

import os
import sys
from pathlib import Path


def legacy_script_path() -> Path:
    return Path(__file__).resolve().parents[2] / "dports-dev-env"


def exec_legacy(argv: list[str]) -> None:
    legacy = legacy_script_path()
    if not legacy.exists():
        print(f"dports-dev-env: legacy script not found: {legacy}", file=sys.stderr)
        raise SystemExit(1)
    os.execv(str(legacy), [str(legacy), *argv])


def main(argv: list[str] | None = None) -> None:
    # Compatibility shim: keep the existing shell implementation authoritative
    # while the Python implementation is introduced command by command.
    exec_legacy(list(sys.argv[1:] if argv is None else argv))
