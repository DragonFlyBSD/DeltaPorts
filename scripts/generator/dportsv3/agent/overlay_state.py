"""Shared overlay-state assessment for dops conversion decisions.

Runtime code may collect facts from different substrates (host checkout
for tests/tools, dev-env chroot for the runner), but the interpretation
must be identical. This module owns that facts -> rules -> assessment
mapping.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from dportsv3.common.text import safe_read_text


CLASSIFICATION_STATES = (
    "converted",
    "auto_safe_pending",
    "needs_judgment",
    # Public vocabulary retained for callers that understand legacy
    # migration buckets. Agent overlay assessment does not emit this
    # state today; staleness is an operator/status concern.
    "stale",
    "not_in_scope",
)

_MAKEFILE_DRAGONFLY_NAMES = ("Makefile.DragonFly", "Makefile.DragonFly.@any")
_TARGET_LINE_RE = re.compile(r"^([A-Za-z0-9_.-]+):\s*$")
_ASSIGN_RE = re.compile(r"^([A-Z0-9_]+)\s*(\+?=|\?=|:=|!=)\s*(.*)$")


@dataclass(frozen=True)
class OverlayFacts:
    origin: str
    port_exists: bool
    overlay_dops: bool = False
    makefile_dragonfly: tuple[str, ...] = ()
    targeted_makefile_dragonfly: tuple[str, ...] = ()
    dragonfly_files: tuple[str, ...] = ()
    diff_files: tuple[str, ...] = ()
    newport: bool = False
    auto_safe_makefile: bool = False
    makefile_reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class OverlayRuleResult:
    code: str
    severity: str = "info"
    artifacts: tuple[str, ...] = ()
    message: str = ""


@dataclass(frozen=True)
class OverlayAssessment:
    state: str
    action: str
    rules: tuple[OverlayRuleResult, ...] = ()
    reasons: tuple[str, ...] = ()
    unmigrated_artifacts: tuple[str, ...] = ()
    invariant_violations: tuple[str, ...] = ()
    # Semantic hints for downstream routing/logging. The runner mostly
    # branches on action today, but convert/reporting code can consume
    # these without re-deriving meaning from rule names.
    deterministic_convertible: bool = False
    needs_llm: bool = False

    def to_log_dict(self) -> dict:
        return {
            "state": self.state,
            "action": self.action,
            "reasons": list(self.reasons),
            "rules": [r.code for r in self.rules],
            "unmigrated_artifacts": list(self.unmigrated_artifacts),
            "invariant_violations": list(self.invariant_violations),
            "deterministic_convertible": self.deterministic_convertible,
            "needs_llm": self.needs_llm,
        }


def makefile_dragonfly_text_auto_safe(text: str) -> tuple[bool, str]:
    if not text.strip():
        return True, "empty_makefile_dragonfly"

    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        i += 1
        if not line or line.startswith("#"):
            continue
        if line.startswith((".if", ".elif", ".else")):
            return False, "conditional_block_present"
        if _TARGET_LINE_RE.match(line):
            while i < len(lines) and (lines[i].startswith("\t") or not lines[i].strip()):
                i += 1
            continue
        if _ASSIGN_RE.match(line):
            continue
        return False, "unsupported_line_pattern"
    return True, "supported_makefile_dragonfly_pattern"


def _makefile_dragonfly_auto_safe(path: Path) -> tuple[bool, str]:
    return makefile_dragonfly_text_auto_safe(safe_read_text(path))


def facts_from_repo(origin: str, repo_root: Path) -> OverlayFacts:
    return facts_from_port_dir(origin, Path(repo_root) / "ports" / origin)


def facts_from_port_dir(origin: str, port_dir: Path) -> OverlayFacts:
    port_dir = Path(port_dir)
    if not port_dir.is_dir():
        return OverlayFacts(origin=origin, port_exists=False)

    diffs_dir = port_dir / "diffs"
    diff_files = tuple(
        sorted(
            str(path.relative_to(port_dir))
            for path in diffs_dir.rglob("*")
            if path.is_file() and path.suffix in {".diff", ".patch"}
        )
    ) if diffs_dir.exists() else ()

    dragonfly_dir = port_dir / "dragonfly"
    dragonfly_files = tuple(
        sorted(
            str(path.relative_to(port_dir))
            for path in dragonfly_dir.rglob("*")
            if path.is_file()
        )
    ) if dragonfly_dir.exists() else ()

    makefiles = tuple(name for name in _MAKEFILE_DRAGONFLY_NAMES if (port_dir / name).exists())
    targeted = tuple(
        sorted(
            path.name for path in port_dir.iterdir()
            if path.is_file()
            and path.name.startswith("Makefile.DragonFly.")
            and path.name not in _MAKEFILE_DRAGONFLY_NAMES
        )
    )

    auto_safe = False
    reasons: list[str] = []
    if len(makefiles) == 1 and makefiles[0] == "Makefile.DragonFly":
        auto_safe, reason = _makefile_dragonfly_auto_safe(port_dir / makefiles[0])
        reasons.append(reason)
    elif makefiles or targeted:
        reasons.append("targeted_or_multiple_makefile_dragonfly")

    return OverlayFacts(
        origin=origin,
        port_exists=True,
        overlay_dops=(port_dir / "overlay.dops").exists(),
        makefile_dragonfly=makefiles,
        targeted_makefile_dragonfly=targeted,
        dragonfly_files=dragonfly_files,
        diff_files=diff_files,
        newport=(port_dir / "newport").exists(),
        auto_safe_makefile=auto_safe,
        makefile_reasons=tuple(reasons),
    )


def assess_overlay(facts: OverlayFacts) -> OverlayAssessment:
    rules: list[OverlayRuleResult] = []
    reasons: list[str] = []
    invariants: list[str] = []

    compat_artifacts = (
        facts.makefile_dragonfly
        + facts.targeted_makefile_dragonfly
        + facts.dragonfly_files
        + facts.diff_files
        + (("newport/",) if facts.newport else ())
    )
    unmigrated = (
        facts.makefile_dragonfly
        + facts.targeted_makefile_dragonfly
        + (("newport/",) if facts.newport else ())
    )

    if not facts.port_exists or not (facts.overlay_dops or compat_artifacts):
        return OverlayAssessment(
            state="not_in_scope",
            action="proceed_triage",
            rules=(OverlayRuleResult("no_overlay_artifacts"),),
            reasons=("no overlay artifacts detected",),
        )

    if facts.overlay_dops:
        rules.append(OverlayRuleResult("valid_dops_present"))
        if facts.dragonfly_files:
            rules.append(OverlayRuleResult(
                "static_patches_allowed_with_dops",
                artifacts=facts.dragonfly_files,
            ))
        if facts.diff_files:
            rules.append(OverlayRuleResult(
                "diffs_allowed_with_dops",
                artifacts=facts.diff_files,
            ))
        if facts.makefile_dragonfly or facts.targeted_makefile_dragonfly:
            rules.append(OverlayRuleResult(
                "unmigrated_makefile_dragonfly",
                severity="conversion_blocker",
                artifacts=facts.makefile_dragonfly + facts.targeted_makefile_dragonfly,
            ))
            invariants.append("dops_with_unmigrated_makefile_dragonfly")
        if facts.newport:
            rules.append(OverlayRuleResult(
                "legacy_newport_present",
                severity="conversion_blocker",
                artifacts=("newport/",),
            ))
            invariants.append("dops_with_unmigrated_newport")
        if not unmigrated:
            return OverlayAssessment(
                state="converted",
                action="proceed_triage",
                rules=tuple(rules),
                reasons=("overlay.dops present with no unmigrated legacy artifacts",),
            )

    if facts.makefile_dragonfly and not (
        facts.targeted_makefile_dragonfly
        or facts.dragonfly_files
        or facts.diff_files
        or facts.newport
    ):
        if facts.auto_safe_makefile:
            rules.append(OverlayRuleResult(
                "deterministic_makefile_dragonfly",
                artifacts=facts.makefile_dragonfly,
            ))
            reasons.extend(facts.makefile_reasons or ("plain Makefile.DragonFly is deterministic-convertible",))
            return OverlayAssessment(
                state="auto_safe_pending",
                action="surface_invariant" if invariants else "defer_to_convert",
                rules=tuple(rules),
                reasons=tuple(reasons),
                unmigrated_artifacts=unmigrated,
                invariant_violations=tuple(invariants),
                deterministic_convertible=True,
            )

    rules.append(OverlayRuleResult("requires_llm_judgment", severity="conversion_blocker"))
    reasons.extend(facts.makefile_reasons)
    if not reasons:
        reasons.append("overlay shape requires conversion judgment")
    return OverlayAssessment(
        state="needs_judgment",
        action="surface_invariant" if invariants else "defer_to_convert",
        rules=tuple(rules),
        reasons=tuple(reasons),
        unmigrated_artifacts=unmigrated,
        invariant_violations=tuple(invariants),
        needs_llm=True,
    )
