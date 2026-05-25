"""Compat-mode renderers (Step 25b).

Each renderer takes (translator, intent) and returns an EditResult.
Renderers do the substrate work directly on the filesystem under
``<workspace>/ports/<origin>/``. Per-intent atomicity: each renderer
records what it changed in EditResult.paths_changed; the
substrate_diff is captured via translator.git_diff() at the end.

On any failure (validator-level or filesystem-level), the renderer
performs its own inverse (deletes the file it just wrote, restores
the prior content, etc.) before returning ok=False.

See ``docs/edit-intent-design.md`` §11 (translation table) and §9
(validator rules).
"""

from __future__ import annotations

from pathlib import Path

from .grammar import (
    AddFile, AddPatch, BumpPortrevision, ChangeMakefile,
    DropPatch, ReplaceInPatch,
)
from .validator import IntentError


# --------------------------------------------------------------------
# replace_in_patch
# --------------------------------------------------------------------


def replace_in_patch(t, intent: ReplaceInPatch):
    from .translator import EditResult  # noqa: PLC0415 (cycle)
    target = t.port_path(intent.target)
    if not target.is_file():
        return EditResult(
            ok=False, intent_type="replace_in_patch",
            error=f"target file does not exist: {intent.target}",
        )
    original = target.read_text()
    matches = _find_all(original, intent.find)
    if not matches:
        return EditResult(
            ok=False, intent_type="replace_in_patch",
            error=(
                f"find string not found in {intent.target}: "
                f"{intent.find[:80]!r}"
            ),
        )
    if intent.occurrence > len(matches):
        return EditResult(
            ok=False, intent_type="replace_in_patch",
            error=(
                f"occurrence {intent.occurrence} requested but only "
                f"{len(matches)} match(es) of {intent.find[:40]!r} in "
                f"{intent.target}"
            ),
        )
    # Replace the Nth occurrence (1-based).
    start = matches[intent.occurrence - 1]
    end = start + len(intent.find)
    new = original[:start] + intent.replace + original[end:]
    try:
        target.write_text(new)
    except OSError as exc:
        return EditResult(
            ok=False, intent_type="replace_in_patch",
            error=f"write failed for {intent.target}: {exc}",
        )
    return EditResult(
        ok=True, intent_type="replace_in_patch",
        paths_changed=[str(target.relative_to(t.workspace))],
        substrate_diff=t.git_diff(target),
    )


# --------------------------------------------------------------------
# drop_patch
# --------------------------------------------------------------------


def drop_patch(t, intent: DropPatch):
    from .translator import EditResult
    target = t.port_path(intent.target)
    if not target.is_file():
        return EditResult(
            ok=False, intent_type="drop_patch",
            error=f"patch does not exist: {intent.target}",
        )
    # Capture diff before unlink so git diff has both states.
    backup = target.read_bytes()
    try:
        target.unlink()
    except OSError as exc:
        return EditResult(
            ok=False, intent_type="drop_patch",
            error=f"unlink failed for {intent.target}: {exc}",
        )
    diff = t.git_diff(target)
    if not diff:
        # Pathological: file was tracked but git diff is empty (e.g.
        # only mode change). Roll back to be safe; the agent didn't
        # actually accomplish anything observable.
        try:
            target.write_bytes(backup)
        except OSError:
            pass
        return EditResult(
            ok=False, intent_type="drop_patch",
            error=f"drop_patch produced empty diff for {intent.target}",
        )
    return EditResult(
        ok=True, intent_type="drop_patch",
        paths_changed=[str(target.relative_to(t.workspace))],
        substrate_diff=diff,
    )


# --------------------------------------------------------------------
# add_patch
# --------------------------------------------------------------------


def add_patch(t, intent: AddPatch):
    from .translator import EditResult
    target = t.port_path(intent.target)
    if target.exists():
        return EditResult(
            ok=False, intent_type="add_patch",
            error=f"patch already exists: {intent.target}",
        )
    if intent.from_dupe:
        diff_content = _resolve_from_dupe(t, intent.target)
        if diff_content is None:
            return EditResult(
                ok=False, intent_type="add_patch",
                error=(
                    f"from_dupe: no file matching basename "
                    f"{Path(intent.target).name!r} found in env's "
                    f"genpatch output dir"
                ),
            )
    else:
        diff_content = intent.diff or ""
    if not diff_content.strip():
        return EditResult(
            ok=False, intent_type="add_patch",
            error="add_patch requires non-empty diff content",
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        target.write_text(diff_content)
    except OSError as exc:
        return EditResult(
            ok=False, intent_type="add_patch",
            error=f"write failed for {intent.target}: {exc}",
        )
    return EditResult(
        ok=True, intent_type="add_patch",
        paths_changed=[str(target.relative_to(t.workspace))],
        substrate_diff=t.git_diff(target),
    )


# --------------------------------------------------------------------
# add_file
# --------------------------------------------------------------------


def add_file(t, intent: AddFile):
    from .translator import EditResult
    target = t.port_path(intent.dest)
    if intent.kind == "resource":
        if target.exists():
            return EditResult(
                ok=False, intent_type="add_file",
                error=f"dest already exists: {intent.dest}",
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            target.write_text(intent.content or "")
        except OSError as exc:
            return EditResult(
                ok=False, intent_type="add_file",
                error=f"write failed for {intent.dest}: {exc}",
            )
        return EditResult(
            ok=True, intent_type="add_file",
            paths_changed=[str(target.relative_to(t.workspace))],
            substrate_diff=t.git_diff(target),
        )
    elif intent.kind == "materialize":
        # In compat mode, materialize is a copy from the upstream
        # tree into ports/<origin>/<dest>. The source path resolves
        # against the env's freebsd-ports tree at /work/freebsd-ports
        # — but the translator doesn't see that here; the operator-
        # configured paths come from the env. For 25b we keep this
        # path stub'd until 25c wires it up; explicit error so the
        # agent doesn't silently succeed.
        return EditResult(
            ok=False, intent_type="add_file",
            error=(
                "add_file{kind=materialize} is not yet supported by "
                "the compat-mode renderer (Step 25b stub); the agent "
                "should fall back to add_patch + dupe for now"
            ),
        )
    return EditResult(
        ok=False, intent_type="add_file",
        error=f"unknown kind: {intent.kind!r}",
    )


# --------------------------------------------------------------------
# change_makefile
# --------------------------------------------------------------------


def change_makefile(t, intent: ChangeMakefile):
    from .translator import EditResult
    target = t.port_path(intent.path)
    # Create the file if missing for op=set/append (e.g. fresh
    # Makefile.DragonFly bootstrap from an empty port).
    existed = target.is_file()
    if not existed:
        if intent.op == "remove":
            return EditResult(
                ok=False, intent_type="change_makefile",
                error=(
                    f"change_makefile op='remove' on {intent.path}: "
                    "file does not exist"
                ),
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        original = ""
    else:
        original = target.read_text()
    new = _apply_makefile_op(original, intent)
    if new is None:
        return EditResult(
            ok=False, intent_type="change_makefile",
            error=(
                f"change_makefile op={intent.op!r} on key "
                f"{intent.key!r}: no matching line and op requires it"
            ),
        )
    if new == original and existed:
        return EditResult(
            ok=False, intent_type="change_makefile",
            error=(
                f"change_makefile is a no-op on {intent.path} for "
                f"key {intent.key!r} ({intent.op})"
            ),
        )
    try:
        target.write_text(new)
    except OSError as exc:
        # Restore on write failure (if it existed).
        if existed:
            try:
                target.write_text(original)
            except OSError:
                pass
        return EditResult(
            ok=False, intent_type="change_makefile",
            error=f"write failed for {intent.path}: {exc}",
        )
    return EditResult(
        ok=True, intent_type="change_makefile",
        paths_changed=[str(target.relative_to(t.workspace))],
        substrate_diff=t.git_diff(target),
    )


# --------------------------------------------------------------------
# bump_portrevision
# --------------------------------------------------------------------


def bump_portrevision(t, intent: BumpPortrevision):
    from .translator import EditResult
    target = t.port_path("Makefile")
    if not target.is_file():
        return EditResult(
            ok=False, intent_type="bump_portrevision",
            error="bump_portrevision: no Makefile in port dir",
        )
    original = target.read_text()
    new, ok, reason = _bump_portrevision_line(original)
    if not ok:
        return EditResult(
            ok=False, intent_type="bump_portrevision",
            error=reason,
        )
    try:
        target.write_text(new)
    except OSError as exc:
        return EditResult(
            ok=False, intent_type="bump_portrevision",
            error=f"write failed for Makefile: {exc}",
        )
    return EditResult(
        ok=True, intent_type="bump_portrevision",
        paths_changed=[str(target.relative_to(t.workspace))],
        substrate_diff=t.git_diff(target),
    )


# --------------------------------------------------------------------
# Helpers (pure functions; testable directly)
# --------------------------------------------------------------------


def _find_all(haystack: str, needle: str) -> list[int]:
    """All start indices of needle in haystack, non-overlapping."""
    if not needle:
        return []
    out: list[int] = []
    i = 0
    while True:
        j = haystack.find(needle, i)
        if j < 0:
            return out
        out.append(j)
        i = j + len(needle)


def _resolve_from_dupe(t, target: str) -> str | None:
    """Find the most recently modified file matching target's
    basename in the env's genpatch output directory.

    The env layout for the genpatch output isn't fully resolved
    without consulting the dev-env state (the WRKSRC path depends
    on the port's distfile layout). For 25b we stub to a
    conventional location and return None if nothing matches; 25c
    wires this through the real env_paths helper.
    """
    basename = Path(target).name
    # Conventional path: <env_root>/work/genpatch-out/<basename>.
    # In tests, the workspace contains a sentinel directory.
    candidates = [
        t.workspace / ".genpatch-out" / basename,
    ]
    # Also search any sibling .genpatch-out under work/ for tests
    # that drop the file in a non-default location.
    work = t.workspace
    if work.is_dir():
        for sub in work.rglob(".genpatch-out"):
            cand = sub / basename
            if cand.is_file():
                candidates.append(cand)
    matches = [c for c in candidates if c.is_file()]
    if not matches:
        return None
    matches.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return matches[0].read_text()


def _apply_makefile_op(text: str, intent: ChangeMakefile) -> str | None:
    """Apply a ChangeMakefile op to Makefile text.

    Returns the new text, or None if op=remove and no matching
    line exists (semantic error). For set: replaces the first
    matching line or appends if missing. For append: extends a
    matching VAR+= line or appends a new one. For remove:
    drops the token from any matching VAR-style line.
    """
    import re
    lines = text.splitlines()
    line_re = re.compile(
        rf"^({re.escape(intent.key)})\s*(\+?=|\?=|:=)\s*(.*)$"
    )
    matches = [(i, line_re.match(L)) for i, L in enumerate(lines)]
    matches = [(i, m) for i, m in matches if m is not None]

    if intent.op == "set":
        new_line = f"{intent.key}=\t{intent.value}"
        if matches:
            i, _ = matches[0]
            lines[i] = new_line
        else:
            lines.append(new_line)
    elif intent.op == "append":
        if matches:
            i, m = matches[0]
            existing = m.group(3).rstrip()
            tokens = existing.split()
            if intent.value in tokens:
                # Already present — no-op (caller surfaces as
                # "no-op" error).
                return text
            lines[i] = (
                f"{m.group(1)}{m.group(2)}\t{existing} {intent.value}".rstrip()
            )
        else:
            lines.append(f"{intent.key}+=\t{intent.value}")
    elif intent.op == "remove":
        if not matches:
            return None
        any_change = False
        for i, m in matches:
            existing = m.group(3).rstrip()
            tokens = existing.split()
            if intent.value not in tokens:
                continue
            new_tokens = [t for t in tokens if t != intent.value]
            new_existing = " ".join(new_tokens)
            if new_existing:
                lines[i] = (
                    f"{m.group(1)}{m.group(2)}\t{new_existing}"
                )
            else:
                # Empty line — drop entirely.
                lines[i] = ""
            any_change = True
        if not any_change:
            return None
    else:
        return None
    # Preserve trailing newline if the source had one.
    suffix = "\n" if text.endswith("\n") else ""
    return "\n".join(lines) + suffix


def _bump_portrevision_line(text: str) -> tuple[str, bool, str]:
    """Return (new_text, ok, reason)."""
    import re
    lines = text.splitlines()
    rev_re = re.compile(r"^PORTREVISION\s*=\s*(\d+)\s*$")
    for i, L in enumerate(lines):
        m = rev_re.match(L)
        if m:
            n = int(m.group(1))
            lines[i] = f"PORTREVISION=\t{n + 1}"
            suffix = "\n" if text.endswith("\n") else ""
            return ("\n".join(lines) + suffix, True, "")
    # No existing line — insert one after PORTVERSION (the canonical
    # FreeBSD-ports convention). Fall back to appending if no
    # PORTVERSION either.
    portver_re = re.compile(r"^PORTVERSION\s*=")
    for i, L in enumerate(lines):
        if portver_re.match(L):
            lines.insert(i + 1, "PORTREVISION=\t1")
            suffix = "\n" if text.endswith("\n") else ""
            return ("\n".join(lines) + suffix, True, "")
    # No PORTVERSION — append at end.
    lines.append("PORTREVISION=\t1")
    suffix = "\n" if text.endswith("\n") else ""
    return ("\n".join(lines) + suffix, True, "")
