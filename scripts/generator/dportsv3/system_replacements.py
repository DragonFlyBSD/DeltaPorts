"""Global system replacement rules for compose output."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class ReplacementRule:
    """One deterministic text replacement rule."""

    rule_id: str
    pattern: re.Pattern[str]
    replacement: str
    mode: str = "sub"


SYSTEM_REPLACEMENT_RULES: tuple[ReplacementRule, ...] = (
    ReplacementRule(
        rule_id="options-default-amd64",
        pattern=re.compile(r"OPTIONS_DEFAULT_amd64"),
        replacement="OPTIONS_DEFAULT_x86_64",
    ),
    ReplacementRule(
        rule_id="options-define-amd64",
        pattern=re.compile(r"OPTIONS_DEFINE_amd64"),
        replacement="OPTIONS_DEFINE_x86_64",
    ),
    ReplacementRule(
        rule_id="broken-amd64",
        pattern=re.compile(r"BROKEN_amd64"),
        replacement="BROKEN_x86_64",
    ),
    ReplacementRule(
        rule_id="suffix-on-amd64",
        pattern=re.compile(r"_ON_amd64"),
        replacement="_ON_x86_64",
    ),
    ReplacementRule(
        rule_id="suffix-off-amd64",
        pattern=re.compile(r"_OFF_amd64"),
        replacement="_OFF_x86_64",
    ),
    ReplacementRule(
        rule_id="cflags-amd64",
        pattern=re.compile(r"CFLAGS_amd64"),
        replacement="CFLAGS_x86_64",
    ),
    ReplacementRule(
        rule_id="arch-match-amd64",
        pattern=re.compile(r"\{ARCH:Mamd64\}"),
        replacement="{ARCH:Mx86_64}",
    ),
    ReplacementRule(
        rule_id="assign-amd64",
        pattern=re.compile(r"_amd64="),
        replacement="_x86_64=",
    ),
    ReplacementRule(
        rule_id="libomp-dep",
        pattern=re.compile(r"libomp\.so:devel/openmp\b\s*"),
        replacement="",
        mode="remove",
    ),
    ReplacementRule(
        rule_id="libomp0-dep",
        pattern=re.compile(r"libomp\.so\.0:devel/openmp\b\s*"),
        replacement="",
        mode="remove",
    ),
)

_ARCH_LINE_GUARD = re.compile(r"ARCH\}.*(?:amd64|\"amd64\")")


@dataclass
class ReplacementStats:
    """Result summary for one port replacement pass."""

    files_scanned: int = 0
    files_changed: int = 0
    rule_hits: dict[str, int] = field(default_factory=dict)


def _candidate_files(port_root: Path) -> list[Path]:
    files = set(port_root.glob("Makefile*"))
    files.update(port_root.glob("*.common"))
    return sorted(path for path in files if path.is_file())


def _apply_rules(text: str) -> tuple[str, dict[str, int]]:
    updated = text
    hits: dict[str, int] = {}

    for rule in SYSTEM_REPLACEMENT_RULES:
        next_text, count = rule.pattern.subn(rule.replacement, updated)
        if count > 0:
            hits[rule.rule_id] = hits.get(rule.rule_id, 0) + count
        updated = next_text

    guarded_lines: list[str] = []
    guarded_hits = 0
    for line in updated.splitlines(keepends=True):
        if _ARCH_LINE_GUARD.search(line):
            count = line.count("amd64")
            if count > 0:
                line = line.replace("amd64", "x86_64")
                guarded_hits += count
        guarded_lines.append(line)

    if guarded_hits > 0:
        hits["arch-guarded-amd64"] = hits.get("arch-guarded-amd64", 0) + guarded_hits

    return "".join(guarded_lines), hits


def apply_system_replacements_to_port(
    port_root: Path, *, dry_run: bool = False
) -> ReplacementStats:
    """Apply global compose replacements to one port directory."""
    stats = ReplacementStats()

    for path in _candidate_files(port_root):
        try:
            text = path.read_text()
        except (OSError, UnicodeDecodeError):
            continue
        stats.files_scanned += 1
        updated, hits = _apply_rules(text)
        if updated == text:
            continue

        stats.files_changed += 1
        for rule_id, count in hits.items():
            stats.rule_hits[rule_id] = stats.rule_hits.get(rule_id, 0) + count

        if not dry_run:
            path.write_text(updated)

    return stats
