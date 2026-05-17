"""Thin wrapper around scripts/snippet-extractor.

The extractor reads snippet requests from ``analysis/triage.md`` (or
``analysis/patch.md``) in the bundle and writes results under
``analysis/snippets/round_N/``. We invoke it as a subprocess and return
the list of files it produced so the harness can append their content
to the next LLM call.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

DEFAULT_EXTRACTOR = Path(__file__).resolve().parents[4] / "scripts" / "snippet-extractor"


def extract_round(
    bundle_dir: Path,
    round_number: int,
    *,
    extractor: Path | None = None,
    prefer_workdir: bool = True,
) -> tuple[int, list[Path]]:
    """Run snippet-extractor for one round; return (exit_code, list of snippet files).

    Exit codes (from snippet-extractor):
      0 — success, at least some snippets extracted
      1 — no snippet requests found
      2 — all requests failed (nothing extracted)
      3 — configuration error
    """
    extractor = extractor or DEFAULT_EXTRACTOR
    cmd: list[str] = [
        str(extractor),
        "--bundle", str(bundle_dir),
        "--round", str(round_number),
    ]
    if prefer_workdir:
        cmd.append("--prefer-workdir")

    result = subprocess.run(cmd, capture_output=True, text=True)

    round_dir = bundle_dir / "analysis" / "snippets" / f"round_{round_number}"
    files: list[Path] = []
    if round_dir.is_dir():
        for sub in sorted(round_dir.rglob("*")):
            if sub.is_file() and sub.suffix in (".txt", ".log"):
                files.append(sub)

    return result.returncode, files


def format_for_prompt(bundle_dir: Path, files: list[Path]) -> str:
    """Render extracted snippet files as a single string for the next user message."""
    parts: list[str] = ["## Extracted Snippets", ""]
    for path in files:
        try:
            rel = path.relative_to(bundle_dir)
        except ValueError:
            rel = path
        try:
            content = path.read_text(errors="replace")
        except OSError as exc:
            content = f"<failed to read: {exc}>"
        parts.append(f"### `{rel}`")
        parts.append("```")
        parts.append(content)
        parts.append("```")
        parts.append("")
    return "\n".join(parts)


def load_manifest(bundle_dir: Path, round_number: int) -> dict:
    """Optional: read the round's manifest.json for audit/debug."""
    manifest = bundle_dir / "analysis" / "snippets" / f"round_{round_number}" / "manifest.json"
    if not manifest.is_file():
        return {}
    try:
        return json.loads(manifest.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
