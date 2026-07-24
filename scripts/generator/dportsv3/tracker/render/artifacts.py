"""Artifact resolution + preview view-data. Pure over an
artifact_root Path and artifact_refs rows."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .text import (
    render_markdown,
    render_diff,
    highlight_json,
    highlight_log,
    _looks_like_text,
    _is_diff_path,
)


def _is_log_relpath(relpath: str) -> bool:
    """True for artifacts that read as build/agent logs — they get the
    light error-line tinting rather than flat monospace."""
    rp = relpath.lower()
    return (
        rp.startswith("logs/")
        or rp.endswith(".log")
        or rp.endswith(".jsonl")
    )


# Exact-match names always treated as text. Patterns below catch the
# variant forms (Makefile.DragonFly, pkg-plist.in, patch-src_*, etc.).
_INLINE_TEXT_NAMES = {"distinfo", "pkg-descr", "pkg-message", "STATUS"}

_INLINE_TEXT_MEDIA: dict[str, str] = {
    ".md":    "text/plain; charset=utf-8",
    ".txt":   "text/plain; charset=utf-8",
    ".log":   "text/plain; charset=utf-8",
    ".diff":  "text/plain; charset=utf-8",
    ".patch": "text/plain; charset=utf-8",
    ".rej":   "text/plain; charset=utf-8",
    ".dops":  "text/plain; charset=utf-8",
    ".json":  "application/json; charset=utf-8",
    ".html":  "text/html; charset=utf-8",
    ".xml":   "application/xml; charset=utf-8",
    ".yaml":  "text/plain; charset=utf-8",
    ".yml":   "text/plain; charset=utf-8",
}

# Filename glob-style patterns that should always render inline as
# UTF-8 text regardless of extension. Covers FreeBSD-ports conventions
# where the variant suffix carries semantic meaning (Makefile.DragonFly,
# pkg-plist.amd64, patch-src_main.c) but isn't a recognized text
# extension — otherwise ``Path(name).suffix`` returns the variant suffix
# and these land as octet-stream.
_INLINE_TEXT_NAME_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p) for p in (
        r"^Makefile(\..+)?$",
        r"^pkg-plist(\..+)?$",
        r"^patch-.+$",
    )
)


def artifact_media_type(
    relpath: str,
    kind: str | None,
    *,
    fs_path: Path | None = None,
) -> tuple[str, bool]:
    """Pick a Content-Type and an inline-vs-attachment flag for an artifact.

    Three classification layers, tried in order:
    1. Exact name + glob pattern allowlist — covers FreeBSD-ports
       file conventions (``Makefile.DragonFly``, ``pkg-plist.amd64``,
       ``patch-src_main.c``) where the variant suffix carries meaning
       but isn't a text extension.
    2. Extension lookup — explicit table for common text formats.
    3. Content sniff — if ``fs_path`` is provided and the file looks
       like UTF-8 text, treat it as text/plain. Backstop for filenames
       we haven't seen before.

    ``kind`` is honored for compressed payloads (the runner sets it on
    bundled logs). ``fs_path`` is optional so existing callers that
    only have the relpath don't break; without it the content sniff is
    skipped and unknown files fall through to octet-stream.
    """
    if kind == "gzip":
        return "application/gzip", False
    artifact_path = Path(relpath)
    name = artifact_path.name
    if name in _INLINE_TEXT_NAMES:
        return "text/plain; charset=utf-8", True
    for pat in _INLINE_TEXT_NAME_PATTERNS:
        if pat.match(name):
            return "text/plain; charset=utf-8", True
    ext = artifact_path.suffix.lower()
    media = _INLINE_TEXT_MEDIA.get(ext)
    if media is not None:
        return media, True
    if fs_path is not None and _looks_like_text(fs_path):
        return "text/plain; charset=utf-8", True
    return "application/octet-stream", False



def artifact_view_data(
    artifact_root: Path,
    bundle_id: str,
    relpath: str,
    ref: dict[str, Any],
) -> dict[str, Any] | None:
    path = resolve_artifact_path(artifact_root, ref)
    if path is None or not path.exists():
        return None
    media_type, inline = artifact_media_type(
        relpath, ref.get("kind"), fs_path=path,
    )
    suffix = Path(relpath).suffix.lower()
    is_json = suffix == ".json"
    is_markdown = suffix == ".md"
    is_diff = _is_diff_path(relpath)
    content: str | None = None
    render_kind = "download"
    error: str | None = None
    # True when `content` is pre-rendered HTML (markdown/diff already are;
    # highlighted json/log join them). The template renders it `| safe`
    # for the json/text branch instead of escaping.
    content_html = False
    if inline:
        if is_markdown:
            render_kind = "markdown"
        elif is_json:
            render_kind = "json"
        elif is_diff:
            render_kind = "diff"
        else:
            render_kind = "text"
        try:
            raw = path.read_text(errors="replace")
            if is_markdown:
                content = render_markdown(raw)
            elif is_diff:
                content = render_diff(raw)
            elif is_json:
                try:
                    pretty = json.dumps(
                        json.loads(raw), indent=2, sort_keys=True,
                    )
                    content = highlight_json(pretty)
                    content_html = True
                except ValueError as exc:
                    content = raw
                    error = f"invalid JSON: {exc}"
            elif _is_log_relpath(relpath):
                content = highlight_log(raw)
                content_html = True
            else:
                content = raw
        except OSError as exc:
            error = str(exc)
            content = ""
    return {
        "bundle_id": bundle_id,
        "relpath": relpath,
        "ref": ref,
        "media_type": media_type,
        "inline": inline,
        "render_kind": render_kind,
        "content": content,
        "content_html": content_html,
        "error": error,
        "filename": Path(relpath).name,
        "badge": _type_badge(relpath),
        "size": path.stat().st_size if path.exists() else ref.get("size"),
    }



# ---------------------------------------------------------------------------


_DEFAULT_ARTIFACT_PRIORITY = (
    # Operator-facing summaries first — these are what the operator
    # wants to land on when they open a bundle.
    "analysis/proposed_fix.md",     # success path: actionable recipe
    "analysis/manual_handoff.md",   # escalation path: what to do next
    # Then the agent's own outputs, then raw evidence.
    "analysis/triage.md",
    "analysis/patch.md",
    "logs/errors.txt",
    "meta.txt",
)



def default_artifact_relpath(bundle: dict[str, Any]) -> str | None:
    artifacts = bundle.get("artifacts") or []
    relpaths = [str(a.get("relpath")) for a in artifacts if a.get("relpath")]
    relpath_set = set(relpaths)
    for candidate in _DEFAULT_ARTIFACT_PRIORITY:
        if candidate in relpath_set:
            return candidate
    return relpaths[0] if relpaths else None


# The artifact reader's left rail: a single curated list grouped by the
# role each file plays, most-relevant group first, most-relevant file
# first within each group. Replaces the old alphabetical table + the
# duplicate "quick links" rail. Entries are (relpath, label); anything
# not listed here falls through to the trailing "Other" group.
_ARTIFACT_CATALOG: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = (
    ("Outcome", (
        ("analysis/proposed_fix.md",   "Proposed fix"),
        ("analysis/manual_handoff.md", "Manual handoff"),
        ("analysis/changes.diff",      "Changes diff"),
    )),
    ("Reasoning", (
        ("analysis/patch.md",  "Patch narrative"),
        ("analysis/triage.md", "Triage"),
    )),
    ("Evidence", (
        ("analysis/patch_audit.json",   "Patch audit"),
        ("analysis/rebuild_proof.json", "Rebuild proof"),
        ("analysis/tool_trace.jsonl",   "Tool trace"),
    )),
    ("Logs", (
        ("logs/errors.txt",   "Errors log"),
        ("logs/full.log.gz",  "Full log"),
        ("meta.txt",          "meta.txt"),
    )),
)

# suffix → short type badge shown on each row and in the detail header.
_TYPE_BADGES: dict[str, str] = {
    ".md": "MD", ".diff": "DIFF", ".patch": "DIFF", ".json": "JSON",
    ".jsonl": "JSONL", ".gz": "GZ", ".txt": "TXT", ".log": "LOG",
    ".dops": "DOPS",
}


def _type_badge(relpath: str) -> str:
    name = Path(relpath).name
    # Compound suffixes (.log.gz, .jsonl.gz) read by their outer intent.
    if name.endswith(".jsonl") or name.endswith(".jsonl.gz"):
        return "JSONL"
    if name.endswith(".gz"):
        return "GZ"
    suffix = Path(relpath).suffix.lower()
    return _TYPE_BADGES.get(suffix, (suffix[1:].upper() if suffix else "FILE"))


def group_artifacts(bundle: dict[str, Any]) -> list[dict[str, Any]]:
    """Project a bundle's artifacts into the reader's grouped left rail.

    Returns a list of ``{"label": str, "items": [...]}`` groups in
    catalog order, then a trailing "Other" group for anything not in
    the catalog (e.g. session dumps, ad-hoc artifacts). Empty groups
    are dropped. Only artifacts that actually exist on the bundle are
    included — absence is legible by what's missing, not by dimmed
    placeholder rows.

    Each item carries what a row needs to render without a second
    lookup: ``relpath``, ``name`` (basename), ``label`` (friendly),
    ``badge`` (type), ``created_at`` (write time), ``size``, and
    ``is_session`` (session dumps link to the structured viewer, not
    the inline reader).
    """
    artifacts = bundle.get("artifacts") or []
    by_relpath = {
        str(a.get("relpath")): a for a in artifacts if a.get("relpath")
    }

    def _item(relpath: str, label: str | None) -> dict[str, Any]:
        a = by_relpath[relpath]
        name = Path(relpath).name
        is_session = (
            relpath.startswith("analysis/sessions/")
            and (relpath.endswith(".jsonl") or relpath.endswith(".jsonl.gz"))
        )
        return {
            "relpath": relpath,
            "name": name,
            "label": label or name,
            "badge": _type_badge(relpath),
            "created_at": a.get("created_at"),
            "size": a.get("size"),
            "is_session": is_session,
        }

    groups: list[dict[str, Any]] = []
    claimed: set[str] = set()
    for group_label, entries in _ARTIFACT_CATALOG:
        files = []
        for relpath, label in entries:
            if relpath in by_relpath:
                files.append(_item(relpath, label))
                claimed.add(relpath)
        if files:
            # NB: key is "files", not "items" — Jinja's `group.items`
            # would resolve to dict.items (a method), not the list.
            groups.append({"label": group_label, "files": files})

    # Anything not in the catalog — session dumps first (they're the
    # most useful uncatalogued artifact), then the rest by relpath.
    leftover = sorted(
        r for r in by_relpath if r not in claimed
    )
    leftover.sort(key=lambda r: (not r.startswith("analysis/sessions/"), r))
    if leftover:
        groups.append({
            "label": "Other",
            "files": [_item(r, None) for r in leftover],
        })
    return groups



def resolve_artifact_path(
    artifact_root: Path, ref: dict[str, Any]
) -> Path | None:
    """Locate the on-disk file for an artifact_refs row.

    Two backends:
    - 'blob': content-addressed under ``<artifact_root>/objects/sha256/aa/bb/<full>``
    - 'fs':   absolute ``fs_path`` recorded at upsert time
    """
    backend = ref.get("backend")
    if backend == "blob":
        sha = ref.get("sha256")
        if not sha or len(sha) < 4:
            return None
        return (
            artifact_root
            / "blobstore"
            / "objects"
            / "sha256"
            / sha[0:2]
            / sha[2:4]
            / sha
        )
    if backend == "fs":
        fs_path = ref.get("fs_path")
        if not fs_path:
            return None
        return Path(fs_path)
    return None



def load_tool_trace(artifact_root: Path, ref: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Parse analysis/tool_trace.jsonl for compact bundle rendering."""
    if ref is None:
        return []
    path = resolve_artifact_path(artifact_root, ref)
    if path is None or not path.exists():
        return []
    events: list[dict[str, Any]] = []
    try:
        for line in path.read_text(errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if isinstance(ev, dict):
                events.append(ev)
    except OSError:
        return []
    return events
