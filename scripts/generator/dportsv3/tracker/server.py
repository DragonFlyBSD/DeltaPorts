"""FastAPI application factory for the build tracker."""

from __future__ import annotations

import importlib
import html
import json
import logging
import os
import re
import sqlite3
from contextlib import contextmanager
from importlib import util as importlib_util
from pathlib import Path
from typing import Any, cast

from dportsv3.tracker.progress_adapter import (
    run_history_chunk,
    run_summary,
    target_history_chunk,
    target_summary,
)
from dportsv3.tracker.agentic_queries import (
    active_job_for_port,
    activity_for_job,
    agentic_status,
    bundles_for_run,
    discard_manual_request,
    distinct_targets,
    events_since,
    env_health_statuses,
    get_active_env,
    set_active_env,
    get_artifact_ref,
    get_bundle,
    get_job,
    get_manual_request,
    get_run,
    clear_origin_skip,
    is_origin_skipped,
    job_events_for_job,
    latest_review_request_for_bundle,
    list_bundles,
    update_review_request_status,
    list_jobs,
    list_jobs_for_bundle,
    list_manual_requests,
    list_port_bundles,
    list_runs,
    port_attempt_summary,
    recent_activity,
    recent_activity_for_bundle,
    runner_status,
    set_origin_skip,
    token_usage_for_job,
    token_usage_for_port,
    upsert_user_context_text,
)
from dportsv3.tracker.db import (
    ActiveBuildError,
    compare_builds,
    create_build_run,
    enqueue_ports,
    finish_build_run,
    get_active_builds_summary,
    get_build_results,
    get_build_run,
    get_diff,
    get_failures,
    get_port_history,
    get_port_status,
    get_target_summary,
    init_db,
    list_build_runs,
    open_db,
    record_results,
    update_port_status,
)
from dportsv3.tracker.models import (
    BuildCompareOut,
    BuildRunOut,
    DiffOut,
    EnqueueRequest,
    EnqueueResponse,
    FinishBuildRequest,
    ManualContextRequest,
    ManualContextResponse,
    ManualDiscardRequest,
    ManualDiscardResponse,
    PortStatusOut,
    RecordResultsRequest,
    RecordResultsResponse,
    StartBuildRequest,
    StartBuildResponse,
    UpdatePortStatusRequest,
)

_LOG = logging.getLogger(__name__)

_fastapi = (
    importlib.import_module("fastapi") if importlib_util.find_spec("fastapi") else None
)
_responses = (
    importlib.import_module("fastapi.responses") if _fastapi is not None else None
)
_staticfiles = (
    importlib.import_module("fastapi.staticfiles") if _fastapi is not None else None
)
_templating = (
    importlib.import_module("fastapi.templating") if _fastapi is not None else None
)

if (
    _fastapi is not None
    and _responses is not None
    and _staticfiles is not None
    and _templating is not None
):
    FastAPIType = _fastapi.FastAPI
    HTTPExceptionType = _fastapi.HTTPException
    QueryType = _fastapi.Query
    RequestType = _fastapi.Request
    HTMLResponseType = _responses.HTMLResponse
    StaticFilesType = _staticfiles.StaticFiles
    Jinja2TemplatesType = _templating.Jinja2Templates
    FileResponseType = _responses.FileResponse
    StreamingResponseType = _responses.StreamingResponse
else:

    class _MissingFastAPI:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            raise RuntimeError("Tracker server dependencies are not installed")

    class _MissingHTTPException(Exception):
        pass

    class _MissingRequest:
        pass

    class _MissingHTMLResponse:
        pass

    class _MissingStaticFiles:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            raise RuntimeError("Tracker server dependencies are not installed")

    class _MissingTemplates:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            raise RuntimeError("Tracker server dependencies are not installed")

    def _missing_query(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("Tracker server dependencies are not installed")

    FastAPIType = _MissingFastAPI
    HTTPExceptionType = _MissingHTTPException
    QueryType = _missing_query
    RequestType = _MissingRequest
    HTMLResponseType = _MissingHTMLResponse
    StaticFilesType = _MissingStaticFiles
    Jinja2TemplatesType = _MissingTemplates
    FileResponseType = _MissingHTMLResponse
    StreamingResponseType = _MissingHTMLResponse


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

# Exact-match names always treated as text. Patterns below catch the
# variant forms (Makefile.DragonFly, pkg-plist.in, patch-src_*, etc.).
_INLINE_TEXT_NAMES = {"distinfo", "pkg-descr", "pkg-message", "STATUS"}

# Filename glob-style patterns that should always render inline as
# UTF-8 text regardless of extension. Each pattern is compiled to a
# regex once at module load. Covers FreeBSD-ports conventions where
# the variant suffix carries semantic meaning (Makefile.DragonFly,
# pkg-plist.amd64, patch-src_main.c) but isn't a recognized text
# extension — pre-fix these landed as octet-stream because
# ``Path(name).suffix`` returns the variant suffix, not ``.txt``.
_INLINE_TEXT_NAME_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p) for p in (
        r"^Makefile(\..+)?$",
        r"^pkg-plist(\..+)?$",
        r"^patch-.+$",
    )
)


def _looks_like_text(path: Path, sample_bytes: int = 4096) -> bool:
    """Content-sniff fallback for files we can't classify by name/ext.

    Reads the first ``sample_bytes`` bytes and decides text vs binary
    via two heuristics:
    1. The sample decodes as UTF-8 (errors='strict').
    2. <5% of bytes are control characters outside the standard set
       (\\t \\n \\r). Catches files that decode but are binary-shaped
       (UTF-16 sequences of nulls, etc.).

    Empty files count as text. OS errors return False so the caller
    falls through to octet-stream-and-download — safer than rendering
    something inline that may not be readable.
    """
    try:
        with path.open("rb") as fh:
            chunk = fh.read(sample_bytes)
    except OSError:
        return False
    if not chunk:
        return True
    try:
        chunk.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return False
    allowed_controls = {0x09, 0x0A, 0x0D}  # tab, LF, CR
    suspicious = sum(
        1 for b in chunk
        if b < 0x20 and b not in allowed_controls
    )
    return suspicious / len(chunk) < 0.05


_INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
_INLINE_BOLD_RE = re.compile(r"\*\*([^*\n]+)\*\*")


def _render_inline(escaped: str) -> str:
    """Apply inline ``code`` and ``**bold**`` to already-HTML-escaped text.

    ``code`` spans are extracted to sentinels before ``**bold**`` is
    processed, then re-substituted. Without that, asterisks inside
    backticks (e.g. ``` `**literal**` ```) would be wrongly bolded.
    The patterns reject newlines so a stray asterisk or backtick on
    its own line can't accidentally span paragraphs.
    """
    placeholders: list[str] = []

    def _stash(m):
        placeholders.append(m.group(1))
        return f"\x00C{len(placeholders) - 1}\x00"

    s = _INLINE_CODE_RE.sub(_stash, escaped)
    s = _INLINE_BOLD_RE.sub(r"<strong>\1</strong>", s)
    for i, content in enumerate(placeholders):
        s = s.replace(f"\x00C{i}\x00", f"<code>{content}</code>")
    return s


_TABLE_SEPARATOR_RE = re.compile(r"^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)+\|?\s*$")


def _parse_table_row(line: str) -> list[str]:
    """Split a GitHub-style markdown table row into cell strings.

    ``| a | b | c |`` → ``['a', 'b', 'c']``. Leading/trailing empty
    cells from outer pipes are dropped. Cell contents are returned
    raw — caller is responsible for HTML-escaping + inline rendering.
    """
    cells = line.strip().split("|")
    if cells and cells[0] == "":
        cells = cells[1:]
    if cells and cells[-1] == "":
        cells = cells[:-1]
    return [c.strip() for c in cells]


def _parse_table_alignments(separator: str) -> list[str]:
    """Map a separator row to per-column alignment strings.

    ``|:---|:---:|---:|`` → ``['left', 'center', 'right']``. Cells
    without explicit colon markers map to ``''`` (use default).
    """
    out: list[str] = []
    for cell in _parse_table_row(separator):
        left = cell.startswith(":")
        right = cell.endswith(":")
        if left and right:
            out.append("center")
        elif right:
            out.append("right")
        elif left:
            out.append("left")
        else:
            out.append("")
    return out


def _render_markdown(text: str) -> str:
    """Render the small Markdown subset used by agent artifacts.

    Keep this stdlib-only and escape all content before wrapping it in
    HTML. It is intentionally conservative: headings, paragraphs,
    bullet lists, fenced code blocks, GitHub-style tables, and inline
    ``code`` + ``**bold**`` cover triage/patch reports, manual_handoff,
    and the analysis docs the agent emits with table-shaped data
    (e.g. ``deferred_verdicts`` tables in patch.md).
    """
    out: list[str] = []
    paragraph: list[str] = []
    bullets_open = False
    code_open = False
    code_lines: list[str] = []

    def flush_paragraph() -> None:
        nonlocal paragraph
        if paragraph:
            out.append("<p>" + "<br>".join(paragraph) + "</p>")
            paragraph = []

    def close_bullets() -> None:
        nonlocal bullets_open
        if bullets_open:
            out.append("</ul>")
            bullets_open = False

    lines = text.splitlines()
    i = 0
    while i < len(lines):
        raw_line = lines[i]
        line = raw_line.rstrip()
        stripped = line.strip()
        if stripped.startswith("```"):
            flush_paragraph()
            close_bullets()
            if code_open:
                out.append(
                    "<pre class=\"artifact-content\"><code>"
                    + html.escape("\n".join(code_lines))
                    + "</code></pre>"
                )
                code_lines = []
                code_open = False
            else:
                code_open = True
            i += 1
            continue
        if code_open:
            code_lines.append(line)
            i += 1
            continue
        if not stripped:
            flush_paragraph()
            close_bullets()
            i += 1
            continue
        if stripped.startswith("#"):
            flush_paragraph()
            close_bullets()
            marker, _, title = stripped.partition(" ")
            if title and 1 <= len(marker) <= 6 and set(marker) == {"#"}:
                level = min(len(marker) + 1, 6)
                out.append(
                    f"<h{level}>"
                    + _render_inline(html.escape(title))
                    + f"</h{level}>"
                )
                i += 1
                continue
        # GitHub-style table: a `|...|` header row followed by a
        # `|---|---|...|` separator. Without the separator we treat
        # the line as a normal paragraph so stray `|` characters in
        # prose don't accidentally start a table.
        if (
            stripped.startswith("|")
            and i + 1 < len(lines)
            and _TABLE_SEPARATOR_RE.match(lines[i + 1].strip())
        ):
            flush_paragraph()
            close_bullets()
            header_cells = _parse_table_row(stripped)
            alignments = _parse_table_alignments(lines[i + 1].strip())
            # Pad alignments to header width if separator is shorter.
            while len(alignments) < len(header_cells):
                alignments.append("")
            out.append('<table class="artifact-table">')
            out.append("<thead><tr>")
            for idx, cell in enumerate(header_cells):
                align = alignments[idx] if idx < len(alignments) else ""
                style = f' style="text-align:{align};"' if align else ""
                out.append(
                    f"<th{style}>"
                    + _render_inline(html.escape(cell))
                    + "</th>"
                )
            out.append("</tr></thead><tbody>")
            j = i + 2
            while j < len(lines):
                row_line = lines[j].strip()
                if not row_line.startswith("|"):
                    break
                row_cells = _parse_table_row(row_line)
                out.append("<tr>")
                for idx, cell in enumerate(row_cells):
                    align = alignments[idx] if idx < len(alignments) else ""
                    style = f' style="text-align:{align};"' if align else ""
                    out.append(
                        f"<td{style}>"
                        + _render_inline(html.escape(cell))
                        + "</td>"
                    )
                out.append("</tr>")
                j += 1
            out.append("</tbody></table>")
            i = j
            continue
        if stripped.startswith("- "):
            flush_paragraph()
            if not bullets_open:
                out.append("<ul>")
                bullets_open = True
            out.append(
                "<li>"
                + _render_inline(html.escape(stripped[2:].strip()))
                + "</li>"
            )
            i += 1
            continue
        paragraph.append(_render_inline(html.escape(stripped)))
        i += 1
    if code_open:
        out.append(
            "<pre class=\"artifact-content\"><code>"
            + html.escape("\n".join(code_lines))
            + "</code></pre>"
        )
    flush_paragraph()
    close_bullets()
    return "\n".join(out)


# Render a unified-diff file as colored HTML. The format is small
# enough to parse line-by-line without a dependency. Hunks carry
# per-side line numbers; we track current old/new line counters as we
# walk a hunk's body. Lines we don't recognize are surfaced verbatim
# so prologue text (commit messages, `diff --git`, etc.) isn't lost.
_DIFF_HUNK_RE = re.compile(
    r"^@@\s+-(\d+)(?:,(\d+))?\s+\+(\d+)(?:,(\d+))?\s+@@(.*)$"
)


def _render_diff(raw: str) -> str:
    """Parse a unified diff and emit colored HTML.

    Output shape:

        <div class="diff-view">
          <div class="diff-stat">N files, +X / -Y lines</div>
          <div class="diff-file">
            <div class="diff-file-header">--- a/foo / +++ b/foo</div>
            <div class="diff-hunk">
              <div class="diff-hunk-header">@@ -10,3 +10,4 @@</div>
              <div class="diff-line diff-add">
                <span class="ln-old"></span><span class="ln-new">11</span>
                <span class="content">+added</span>
              </div>
              ...
            </div>
          </div>
        </div>

    All content is HTML-escaped. Unknown lines (e.g. ``diff --git``,
    ``index abc..def``, commit-message prologue) are emitted as
    ``diff-meta`` rows so the diff is faithful to its input.
    """
    out: list[str] = []
    files = 0
    adds = 0
    rems = 0

    # Per-hunk counters; reset at every @@ header.
    old_lineno: int | None = None
    new_lineno: int | None = None
    in_file = False
    in_hunk = False

    def _close_hunk() -> None:
        nonlocal in_hunk
        if in_hunk:
            out.append("</div>")  # diff-hunk
            in_hunk = False

    def _close_file() -> None:
        nonlocal in_file
        _close_hunk()
        if in_file:
            out.append("</div>")  # diff-file
            in_file = False

    def _open_file() -> None:
        nonlocal in_file, files
        _close_file()
        out.append('<div class="diff-file">')
        in_file = True
        files += 1

    def _line(cls: str, old: str, new: str, content: str) -> str:
        return (
            f'<div class="diff-line {cls}">'
            f'<span class="ln-old">{old}</span>'
            f'<span class="ln-new">{new}</span>'
            f'<span class="content">{html.escape(content)}</span>'
            f"</div>"
        )

    out.append('<div class="diff-view">')
    out.append('<div class="diff-stat-placeholder"></div>')  # filled below
    stat_idx = len(out) - 1

    for line in raw.splitlines():
        # Hunk header — opens a new hunk within the current file.
        m = _DIFF_HUNK_RE.match(line)
        if m:
            _close_hunk()
            if not in_file:
                _open_file()
            old_lineno = int(m.group(1))
            new_lineno = int(m.group(3))
            out.append('<div class="diff-hunk">')
            in_hunk = True
            out.append(
                '<div class="diff-hunk-header">'
                + html.escape(line)
                + "</div>"
            )
            continue
        # File header.
        if line.startswith("--- "):
            _close_hunk()
            if not in_file:
                _open_file()
            else:
                # Two consecutive --- without a +++ in between would
                # be malformed; treat the new one as opening a new file.
                _close_file()
                _open_file()
            out.append(
                '<div class="diff-file-header diff-path-old">'
                + html.escape(line)
                + "</div>"
            )
            continue
        if line.startswith("+++ "):
            if not in_file:
                _open_file()
            out.append(
                '<div class="diff-file-header diff-path-new">'
                + html.escape(line)
                + "</div>"
            )
            continue
        # Hunk body.
        if in_hunk and old_lineno is not None and new_lineno is not None:
            if line.startswith("+"):
                out.append(_line("diff-add", "", str(new_lineno), line))
                new_lineno += 1
                adds += 1
                continue
            if line.startswith("-"):
                out.append(_line("diff-del", str(old_lineno), "", line))
                old_lineno += 1
                rems += 1
                continue
            if line.startswith(" ") or line == "":
                out.append(
                    _line(
                        "diff-context",
                        str(old_lineno),
                        str(new_lineno),
                        line,
                    )
                )
                old_lineno += 1
                new_lineno += 1
                continue
            if line.startswith("\\"):
                # "\ No newline at end of file" — metadata, no counter
                out.append(
                    f'<div class="diff-line diff-meta">'
                    f'<span class="ln-old"></span><span class="ln-new"></span>'
                    f'<span class="content">{html.escape(line)}</span>'
                    f"</div>"
                )
                continue
        # Prologue / unrecognized line outside any hunk — meta row.
        out.append(
            f'<div class="diff-line diff-meta">'
            f'<span class="ln-old"></span><span class="ln-new"></span>'
            f'<span class="content">{html.escape(line)}</span>'
            f"</div>"
        )

    _close_file()
    out.append("</div>")  # diff-view

    # Render the stat. Use # files as // of seen --- headers (any
    # diff without --- headers shows files=0, which is honest).
    out[stat_idx] = (
        f'<div class="diff-stat">'
        f"{files} file{'s' if files != 1 else ''}, "
        f'<span class="diff-stat-add">+{adds}</span> / '
        f'<span class="diff-stat-del">-{rems}</span> lines'
        f"</div>"
    )
    return "\n".join(out)


_DIFF_EXTENSIONS = frozenset({".diff", ".patch", ".rej"})

# FreeBSD ports convention: any file basename starting with ``patch-``
# under a port subtree (``port/files/`` or ``port/dragonfly/``) is a
# unified diff regardless of the trailing extension. Examples:
# ``patch-Makefile.in``, ``patch-src_main.c``, ``patch-Makefile.pre.in``.
_DIFF_NAME_PATTERN = re.compile(r"^patch-")


def _is_diff_path(relpath: str) -> bool:
    """True if ``relpath`` should render with the diff renderer.

    Two triggers: an explicit diff/patch/rej extension, OR a basename
    matching the FreeBSD-ports ``patch-*`` convention (any extension).
    """
    p = Path(relpath)
    if p.suffix.lower() in _DIFF_EXTENSIONS:
        return True
    return bool(_DIFF_NAME_PATTERN.match(p.name))


def _artifact_media_type(
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


def _artifact_view_data(
    artifact_root: Path,
    bundle_id: str,
    relpath: str,
    ref: dict[str, Any],
) -> dict[str, Any] | None:
    path = _resolve_artifact_path(artifact_root, ref)
    if path is None or not path.exists():
        return None
    media_type, inline = _artifact_media_type(
        relpath, ref.get("kind"), fs_path=path,
    )
    suffix = Path(relpath).suffix.lower()
    is_json = suffix == ".json"
    is_markdown = suffix == ".md"
    is_diff = _is_diff_path(relpath)
    content: str | None = None
    render_kind = "download"
    error: str | None = None
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
                content = _render_markdown(raw)
            elif is_diff:
                content = _render_diff(raw)
            elif is_json:
                try:
                    content = json.dumps(json.loads(raw), indent=2, sort_keys=True)
                except ValueError as exc:
                    content = raw
                    error = f"invalid JSON: {exc}"
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
        "error": error,
        "filename": Path(relpath).name,
        "size": path.stat().st_size if path.exists() else ref.get("size"),
    }


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


def _default_artifact_relpath(bundle: dict[str, Any]) -> str | None:
    artifacts = bundle.get("artifacts") or []
    relpaths = [str(a.get("relpath")) for a in artifacts if a.get("relpath")]
    relpath_set = set(relpaths)
    for candidate in _DEFAULT_ARTIFACT_PRIORITY:
        if candidate in relpath_set:
            return candidate
    return relpaths[0] if relpaths else None


def _resolve_artifact_path(
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


def _load_tool_trace(artifact_root: Path, ref: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Parse analysis/tool_trace.jsonl for compact bundle rendering."""
    if ref is None:
        return []
    path = _resolve_artifact_path(artifact_root, ref)
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


def _load_intent_log(
    artifact_root: Path, ref: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Step 25f: load and parse analysis/intent_log.json for the
    bundle detail page's "Intent sequence" card.

    Returns the full document on success (the template iterates
    intents[].intent + intents[].substrate_diff). Returns None for
    missing / malformed / non-dict artifacts so the template's
    `{% if intent_log %}` short-circuits cleanly. Pre-Step-25
    bundles + bundles where the patch agent didn't use the intent
    flow simply skip this rendering.
    """
    if ref is None:
        return None
    path = _resolve_artifact_path(artifact_root, ref)
    if path is None or not path.exists():
        return None
    try:
        doc = json.loads(path.read_text(errors="replace"))
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def create_app(db_path: str | Path) -> Any:
    """Create one tracker FastAPI app instance."""
    if (
        _fastapi is None
        or _responses is None
        or _staticfiles is None
        or _templating is None
    ):
        raise RuntimeError(
            "Tracker server requires optional dependencies. Install with: "
            'pip install -e ".[tracker]"'
        )
    FastAPI = cast(Any, FastAPIType)
    HTTPException = cast(Any, HTTPExceptionType)
    Query = cast(Any, QueryType)
    HTMLResponse = cast(Any, HTMLResponseType)
    StaticFiles = cast(Any, StaticFilesType)
    Jinja2Templates = cast(Any, Jinja2TemplatesType)
    FileResponse = cast(Any, FileResponseType)
    StreamingResponse = cast(Any, StreamingResponseType)

    app: Any = FastAPI(title="DeltaPorts Build Tracker")
    app.state.db_path = str(db_path)
    # Resolves /api/bundles/<id>/artifacts/<relpath> for the 'blob'
    # backend. Defaults match artifact-store's --logs-root default.
    app.state.artifact_root = Path(
        os.environ.get("DPORTSV3_ARTIFACT_ROOT", "/build/synth/logs/evidence")
    )
    templates_dir = Path(__file__).with_name("templates")
    static_dir = Path(__file__).with_name("static")
    templates: Any = Jinja2Templates(directory=str(templates_dir))
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @app.on_event("startup")
    def _startup() -> None:
        conn = init_db(app.state.db_path)
        conn.close()

    @app.on_event("shutdown")
    def _shutdown() -> None:
        return None

    @contextmanager
    def _conn() -> Any:
        conn = open_db(app.state.db_path)
        try:
            yield conn
        finally:
            conn.close()

    def _raise_http_error(exc: Exception) -> None:
        if isinstance(exc, ActiveBuildError):
            raise HTTPException(
                status_code=409,
                detail={
                    "error": str(exc),
                    "active_run": exc.active_run,
                },
            ) from exc
        if isinstance(exc, ValueError):
            message = str(exc)
            status_code = 404 if message.startswith("Unknown build run:") else 400
            raise HTTPException(status_code=status_code, detail=message) from exc
        raise exc

    @app.post("/api/builds", response_model=StartBuildResponse)
    def start_build(payload: StartBuildRequest) -> dict[str, int]:
        run_id = 0
        try:
            with _conn() as conn:
                run_id = create_build_run(
                    conn,
                    target=payload.target,
                    build_type=payload.build_type,
                    started_at=payload.started_at,
                )
        except Exception as exc:
            _raise_http_error(exc)
            raise AssertionError("unreachable")
        return {"id": run_id}

    @app.patch("/api/builds/{run_id}")
    def finish_build(run_id: int, payload: FinishBuildRequest) -> dict[str, bool]:
        try:
            with _conn() as conn:
                finish_build_run(
                    conn,
                    run_id=run_id,
                    finished_at=payload.finished_at,
                    commit_sha=payload.commit_sha,
                    commit_branch=payload.commit_branch,
                    commit_pushed_at=payload.commit_pushed_at,
                )
        except Exception as exc:
            _raise_http_error(exc)
        return {"ok": True}

    @app.post("/api/builds/{run_id}/results", response_model=RecordResultsResponse)
    def add_results(
        run_id: int,
        payload: RecordResultsRequest,
    ) -> dict[str, int]:
        recorded = 0
        try:
            with _conn() as conn:
                run = get_build_run(conn, run_id)
                recorded = record_results(
                    conn,
                    run_id=run_id,
                    target=str(run["target"]),
                    results=[item.model_dump() for item in payload.results],
                )
        except Exception as exc:
            _raise_http_error(exc)
            raise AssertionError("unreachable")
        return {"recorded": recorded}

    @app.post("/api/builds/{run_id}/queue", response_model=EnqueueResponse)
    def enqueue(run_id: int, payload: EnqueueRequest) -> dict[str, int]:
        try:
            with _conn() as conn:
                count = enqueue_ports(
                    conn,
                    run_id,
                    [item.model_dump() for item in payload.ports],
                    total_expected=payload.total_expected,
                )
        except Exception as exc:
            _raise_http_error(exc)
            raise AssertionError("unreachable")
        return {"queued": count}

    @app.patch("/api/builds/{run_id}/ports/{origin:path}/status")
    def patch_port_status(
        run_id: int,
        origin: str,
        payload: UpdatePortStatusRequest,
    ) -> dict[str, bool]:
        try:
            with _conn() as conn:
                update_port_status(conn, run_id, origin, payload.status)
        except Exception as exc:
            _raise_http_error(exc)
        return {"ok": True}

    @app.get("/api/builds", response_model=list[BuildRunOut])
    def api_list_builds(
        target: str | None = None,
        build_type: str | None = None,
        limit: int = Query(default=20, ge=1, le=1000),
    ) -> list[dict[str, Any]]:
        with _conn() as conn:
            return list_build_runs(
                conn, target=target, build_type=build_type, limit=limit
            )

    @app.get("/api/builds/compare", response_model=BuildCompareOut)
    def api_compare_builds(a: int, b: int) -> dict[str, Any]:
        try:
            with _conn() as conn:
                return compare_builds(conn, a, b)
        except Exception as exc:
            _raise_http_error(exc)
            raise AssertionError("unreachable")

    @app.get("/api/builds/{run_id}")
    def api_get_build(run_id: int) -> dict[str, Any]:
        try:
            with _conn() as conn:
                return {
                    "build_run": get_build_run(conn, run_id),
                    "results": get_build_results(conn, run_id),
                }
        except Exception as exc:
            _raise_http_error(exc)
            raise AssertionError("unreachable")

    @app.get("/api/status", response_model=list[PortStatusOut])
    def api_status(
        target: str | None = None,
        origin: str | None = None,
    ) -> list[dict[str, Any]]:
        with _conn() as conn:
            return get_port_status(conn, target=target, origin=origin)

    @app.get("/api/failures", response_model=list[PortStatusOut])
    def api_failures(target: str) -> list[dict[str, Any]]:
        with _conn() as conn:
            return get_failures(conn, target)

    @app.get("/api/diff", response_model=DiffOut)
    def api_diff(a: str, b: str) -> dict[str, Any]:
        with _conn() as conn:
            return get_diff(conn, a, b)

    # ------------------------------------------------------------------
    # Agentic-read endpoints (absorbed from the retired state-server).
    # ------------------------------------------------------------------

    @app.get("/api/health")
    def api_health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/agentic-status")
    def api_agentic_status() -> dict[str, Any]:
        with _conn() as conn:
            return agentic_status(conn)

    @app.get("/api/activity")
    def api_activity(
        limit: int = Query(default=10, ge=1, le=500),
        target: str | None = None,
        job_id: str | None = None,
        since_id: int = Query(default=0, ge=0),
        stage_filter: str | None = None,
    ) -> list[dict[str, Any]]:
        """Activity-log query.

        - ``job_id`` set → per-job rows. With ``since_id > 0`` returns
          new rows oldest-first (polling shape for the job detail
          page's live refresh — Step 9c). Optional ``stage_filter``
          narrows to ``llm_turn`` or ``tool`` (Step 9b).
        - ``job_id`` unset → global recent (newest-first), optionally
          filtered by target.
        """
        with _conn() as conn:
            if job_id:
                return activity_for_job(
                    conn, job_id, limit=limit, since_id=since_id,
                    stage_filter=stage_filter,
                )
            return recent_activity(conn, limit=limit, target=target)

    @app.get("/api/runner-status")
    def api_runner_status() -> dict[str, Any]:
        with _conn() as conn:
            return runner_status(conn)

    @app.get("/api/env-health")
    def api_env_health() -> list[dict[str, Any]]:
        with _conn() as conn:
            return env_health_statuses(conn)

    @app.get("/api/config/active-env")
    def api_get_active_env() -> dict[str, Any]:
        with _conn() as conn:
            return {"name": get_active_env(conn)}

    @app.put("/api/config/active-env")
    def api_put_active_env(payload: dict[str, Any]) -> dict[str, Any]:
        name = payload.get("name")
        if name is not None and not isinstance(name, str):
            raise HTTPException(
                status_code=400,
                detail="name must be a string or null",
            )
        # Empty string normalizes to None (clear).
        if isinstance(name, str) and not name.strip():
            name = None
        with _conn() as conn:
            set_active_env(conn, name)
            return {"name": get_active_env(conn)}

    @app.get("/api/runs")
    def api_runs(
        target: str | None = None,
        limit: int = Query(default=50, ge=1, le=500),
    ) -> list[dict[str, Any]]:
        with _conn() as conn:
            return list_runs(conn, target=target, limit=limit)

    @app.get("/api/runs/{run_id}")
    def api_run_detail(run_id: str) -> dict[str, Any]:
        with _conn() as conn:
            row = get_run(conn, run_id)
        if row is None:
            raise HTTPException(status_code=404, detail=f"Unknown run: {run_id}")
        return row

    @app.get("/api/jobs")
    def api_jobs(
        state: str | None = None,
        target: str | None = None,
        limit: int = Query(default=100, ge=1, le=500),
    ) -> list[dict[str, Any]]:
        with _conn() as conn:
            return list_jobs(conn, state=state, target=target, limit=limit)

    @app.get("/api/jobs/{job_id}")
    def api_job_detail(job_id: str) -> dict[str, Any]:
        with _conn() as conn:
            row = get_job(conn, job_id)
        if row is None:
            raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}")
        return row

    @app.post("/api/jobs/{job_id}/abandon")
    def api_job_abandon(job_id: str) -> dict[str, Any]:
        """Operator-triggered kill. Transitions a QUEUED or in-flight
        job to DEAD with ``retire_reason='abandoned'``. Rejects calls
        against terminal states (DONE/DEAD/ESCALATED) — the operator
        can't abandon something that's already retired."""
        from dportsv3.agent import lifecycle as _lc  # noqa: PLC0415
        with _conn() as conn:
            row = get_job(conn, job_id)
        if row is None:
            raise HTTPException(
                status_code=404, detail=f"Unknown job: {job_id}",
            )
        # lifecycle.apply runs explicit BEGIN IMMEDIATE / COMMIT and
        # is incompatible with sqlite3's default deferred-transaction
        # wrapper. Use a dedicated autocommit-mode connection so the
        # explicit transaction works as authored.
        write_conn = sqlite3.connect(
            str(app.state.db_path), check_same_thread=False,
            isolation_level=None,
        )
        write_conn.row_factory = sqlite3.Row
        try:
            try:
                new_state = _lc.apply(
                    write_conn, job_id, _lc.JobEvent.ABANDON, actor="operator",
                )
            except _lc.IllegalTransition as exc:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"Cannot abandon job in state {row.get('state')!r}: "
                        f"{exc}"
                    ),
                )
        finally:
            write_conn.close()
        return {
            "ok": True,
            "job_id": job_id,
            "previous_state": row.get("state"),
            "new_state": new_state.value,
            "retire_reason": "abandoned",
        }

    @app.get("/api/bundles")
    def api_bundles(
        target: str | None = None,
        origin: str | None = None,
        limit: int = Query(default=100, ge=1, le=500),
    ) -> list[dict[str, Any]]:
        with _conn() as conn:
            return list_bundles(conn, target=target, origin=origin, limit=limit)

    @app.get("/api/bundles/{bundle_id}")
    def api_bundle_detail(
        bundle_id: str,
        include: str = "",
    ) -> dict[str, Any]:
        """Return the bundle row + its artifacts. With ``include=jobs``
        also attaches the list of jobs that touched this bundle
        (linked via jobs.bundle_dir basename → bundle_id) so the
        analyzer subagent doesn't need a separate list-jobs join."""
        with _conn() as conn:
            row = get_bundle(conn, bundle_id)
            if row is None:
                raise HTTPException(
                    status_code=404, detail=f"Unknown bundle: {bundle_id}",
                )
            includes = {t.strip() for t in include.split(",") if t.strip()}
            if "jobs" in includes:
                row["jobs"] = list_jobs_for_bundle(conn, bundle_id)
        return row

    @app.post("/api/bundles/{bundle_id}/verification")
    def api_bundle_verification(
        bundle_id: str, body: dict[str, Any],
    ) -> dict[str, Any]:
        """Record an independent-verification outcome for a bundle
        (plan Step 11b Slice 2).

        Body shape (validated minimally on purpose — the orchestrator
        in Slice 3 owns the schema):

            {
              "ok": bool,                          # required
              "applied_diff_sha256": "<hex>"|null, # required (forensics)
              "verified_at": "<iso>"|null,         # optional; server fills
              "dsynth_exit": int|null,             # optional, kept for audit
            }

        Updates three columns on bundles: verification_status (set to
        'verified' or 'verification_failed'), verification_at,
        verification_applied_diff_sha256. Emits a bundle_verified
        event so the SSE stream picks it up. Idempotent: re-POSTing
        with a different applied_diff_sha256 overwrites — the column
        records the *last* verification attempt, not a history.

        The dsynth log itself is not stored here — Slice 3 may upload
        it as a bundle artifact (e.g. analysis/verification.log) via
        the artifact-store endpoint independently.
        """
        from datetime import datetime, timezone  # noqa: PLC0415

        if "ok" not in body or not isinstance(body["ok"], bool):
            raise HTTPException(
                status_code=400,
                detail="body must include boolean 'ok'",
            )
        if "applied_diff_sha256" not in body:
            raise HTTPException(
                status_code=400,
                detail="body must include 'applied_diff_sha256' "
                       "(null acceptable if no diff was applied)",
            )

        with _conn() as conn:
            row = get_bundle(conn, bundle_id)
        if row is None:
            raise HTTPException(
                status_code=404, detail=f"Unknown bundle: {bundle_id}",
            )

        status = "verified" if body["ok"] else "verification_failed"
        verified_at = body.get("verified_at") or (
            datetime.now(timezone.utc).isoformat()
        )
        applied_diff_sha = body.get("applied_diff_sha256")

        write_conn = sqlite3.connect(
            str(app.state.db_path), check_same_thread=False,
            isolation_level=None,
        )
        write_conn.row_factory = sqlite3.Row
        try:
            write_conn.execute(
                """UPDATE bundles SET
                       verification_status = ?,
                       verification_at = ?,
                       verification_applied_diff_sha256 = ?,
                       last_seen_at = ?
                   WHERE bundle_id = ?""",
                (status, verified_at, applied_diff_sha,
                 verified_at, bundle_id),
            )
            from dportsv3.artifact_store import emit_event  # noqa: PLC0415
            emit_event(write_conn, "bundle_verified", {
                "bundle_id": bundle_id,
                "verification_status": status,
                "verification_at": verified_at,
                "applied_diff_sha256": applied_diff_sha,
                "dsynth_exit": body.get("dsynth_exit"),
            })
        finally:
            write_conn.close()

        return {
            "ok": True,
            "bundle_id": bundle_id,
            "verification_status": status,
            "verification_at": verified_at,
            "applied_diff_sha256": applied_diff_sha,
        }

    @app.post("/api/bundles/{bundle_id}/verify")
    def api_bundle_verify(
        bundle_id: str, body: dict[str, Any],
    ) -> dict[str, Any]:
        """Operator-triggered verify (Step 11c). Writes a row to
        ``verify_requests``; the runner's poll loop picks it up,
        calls ``dportsv3.verify_fix.run_verify_fix`` in-process, and
        the result POSTs back to ``/verification`` (Slice 2) when
        done.

        Body: ``{"env": "<dev-env-name>"}``. Operator-chosen env;
        auto-provisioning is a follow-up.

        The tracker doesn't import the runner or touch the queue
        filesystem any more (layer-violation cleanup). The
        ``verify_requests`` table mirrors the
        ``user_context_requests`` pattern: the tracker records
        intent, the runner reconciles.
        """
        from datetime import datetime, timezone  # noqa: PLC0415

        env = (body or {}).get("env")
        if not env or not isinstance(env, str):
            raise HTTPException(
                status_code=400,
                detail="body must include 'env' (dev-env name)",
            )
        with _conn() as conn:
            row = get_bundle(conn, bundle_id)
        if row is None:
            raise HTTPException(
                status_code=404, detail=f"Unknown bundle: {bundle_id}",
            )
        if row.get("resolution") in ("accepted", "rejected"):
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Cannot verify bundle in terminal state "
                    f"{row.get('resolution')!r}"
                ),
            )

        now = datetime.now(timezone.utc).isoformat()
        write_conn = sqlite3.connect(
            str(app.state.db_path), check_same_thread=False,
            isolation_level=None,
        )
        write_conn.row_factory = sqlite3.Row
        try:
            cur = write_conn.execute(
                """INSERT INTO verify_requests
                       (bundle_id, env, requested_by, requested_at, status)
                   VALUES (?, ?, 'operator', ?, 'pending')""",
                (bundle_id, env, now),
            )
            request_id = cur.lastrowid
            from dportsv3.artifact_store import emit_event  # noqa: PLC0415
            emit_event(write_conn, "verify_requested", {
                "bundle_id": bundle_id,
                "request_id": request_id,
                "env": env,
                "requested_at": now,
            })
        finally:
            write_conn.close()

        return {
            "ok": True,
            "bundle_id": bundle_id,
            "request_id": request_id,
            "status": "pending",
            "env": env,
        }

    def _activity_log(
        write_conn: sqlite3.Connection,
        stage: str,
        message: str,
        extra: dict[str, Any] | None = None,
        bundle_id: str | None = None,
    ) -> None:
        """Tracker-side activity_log writer. Mirrors the runner's
        ``activity_log()`` helper but uses the connection the caller
        already holds (the runner's is bound to a module global).

        Visibility plan: every accept and every delivery outcome
        emits one row so operators can see what happened on the
        bundle detail page's activity ribbon AND via
        ``dportsv3 tracker get-activity``. Pairs with a daemon-log
        line for tail-the-log workflows. ``bundle_id`` lands in
        its own column so the bundle page can render
        bundle-scoped rows without parsing extra_json.
        """
        from datetime import datetime, timezone  # noqa: PLC0415
        ts = datetime.now(timezone.utc).isoformat()
        try:
            write_conn.execute(
                """INSERT INTO activity_log
                       (ts, job_id, bundle_id, stage, message,
                        duration_ms, extra_json)
                       VALUES (?, NULL, ?, ?, ?, NULL, ?)""",
                (ts, bundle_id, stage, message,
                 json.dumps(extra) if extra is not None else None),
            )
        except sqlite3.Error as exc:
            # Activity logging is best-effort — never break an
            # accept because the activity table couldn't take a row.
            _LOG.warning("activity_log insert failed: %s", exc)

    def _accept_delivery_step(
        *,
        bundle: dict[str, Any],
        request_body: dict[str, Any],
        write_conn: sqlite3.Connection,
    ) -> dict[str, Any]:
        """Step 11d-2: optional delivery side-effect on Accept.

        Always returns a dict carrying the outcome for the accept
        response. ``status`` is one of:
          - ``created`` / ``updated`` — provider succeeded.
          - ``create_failed`` — provider raised; a create_failed row
            is also written for operator visibility.
          - ``skipped`` — delivery wasn't attempted; ``skip_reason``
            names which gate fired (``operator_optout``,
            ``no_config``, ``no_changes_diff``,
            ``changes_diff_empty``, ``changes_diff_unreadable``).

        Step 30 slice 5: reads ``analysis/changes.diff`` — now the
        single canonical diff (branch-vs-base, includes convert
        commits). Pre-slice-5 bundles had a HEAD-relative
        changes.diff that silently lost convert work on converted
        ports; the slice-5 cutover makes changes.diff the
        full-chain artifact.

        The bundle accept itself never depends on delivery — any
        exception in here writes a create_failed row and returns a
        descriptive dict but does not propagate.
        """
        if request_body.get("deliver") is False:
            return {"status": "skipped", "skip_reason": "operator_optout"}

        from dportsv3.delivery.orchestrator import (  # noqa: PLC0415
            resolve_config, deliver, DeliveryOutcome,
        )
        from dportsv3.delivery import DeliveryConfigError  # noqa: PLC0415

        try:
            cfg = resolve_config(target=bundle.get("target") or None)
        except DeliveryConfigError as exc:
            # Config exists but is malformed — surface as a delivery
            # error rather than silently skipping.
            return {
                "status": "create_failed",
                "error": f"DeliveryConfigError: {exc}",
            }
        if cfg is None:
            return {"status": "skipped", "skip_reason": "no_config"}

        bundle_id = bundle.get("bundle_id") or ""
        diff_ref = get_artifact_ref(
            write_conn, bundle_id, "analysis/changes.diff",
        )
        if diff_ref is None:
            return {
                "status": "skipped",
                "skip_reason": "no_changes_diff",
            }
        diff_path = _resolve_artifact_path(app.state.artifact_root, diff_ref)
        if diff_path is None or not diff_path.is_file():
            return {
                "status": "skipped",
                "skip_reason": "changes_diff_unreadable",
            }
        try:
            diff_text = diff_path.read_text()
        except OSError as exc:
            return {
                "status": "create_failed",
                "error": f"changes.diff read failed: {exc}",
            }
        if not diff_text.strip():
            return {
                "status": "skipped",
                "skip_reason": "changes_diff_empty",
            }

        operator = (
            (request_body.get("operator") or "operator").strip()
            or "operator"
        )

        # Read artifact text best-effort for the PR body. triage.md
        # supplies the Problem section (Root Cause / Evidence /
        # classification); patch.md supplies the Fix rationale;
        # patch_audit.json supplies model / attempts / tokens.
        def _read_artifact_text(relpath: str) -> str | None:
            ref = get_artifact_ref(write_conn, bundle_id, relpath)
            if ref is None:
                return None
            path = _resolve_artifact_path(app.state.artifact_root, ref)
            if path is None or not path.is_file():
                return None
            try:
                return path.read_text()
            except OSError:
                return None

        model = attempts = tokens = None
        audit_text = _read_artifact_text("analysis/patch_audit.json")
        if audit_text:
            try:
                import json as _json  # noqa: PLC0415
                audit_data = _json.loads(audit_text)
                model = audit_data.get("model")
                raw_attempts = audit_data.get("attempts")
                if isinstance(raw_attempts, list):
                    attempts = len(raw_attempts)
                tu = audit_data.get("tokens_used") or {}
                tokens = tu.get("total") if isinstance(tu, dict) else None
            except Exception:
                pass

        triage_md = _read_artifact_text("analysis/triage.md")
        patch_md = _read_artifact_text("analysis/patch.md")

        try:
            outcome: DeliveryOutcome = deliver(
                bundle=bundle,
                diff_text=diff_text,
                cfg=cfg,
                operator=operator,
                triage_md=triage_md,
                patch_md=patch_md,
                model=model, attempts=attempts, tokens=tokens,
                write_conn=write_conn,
            )
        except Exception as exc:
            # The orchestrator already catches provider failures and
            # writes a create_failed row. Any exception that escapes
            # here is a bug in orchestrator.deliver itself; surface
            # without crashing the accept.
            return {
                "status": "create_failed",
                "error": f"deliver() raised: {type(exc).__name__}: {exc}",
            }

        from dportsv3.artifact_store import emit_event  # noqa: PLC0415
        emit_event(write_conn, "bundle_delivered", {
            "bundle_id": bundle_id,
            "provider": outcome.provider,
            "status": outcome.status,
            "url": outcome.url,
            "branch": outcome.branch,
            "operator": operator,
        })
        result: dict[str, Any] = {
            "status": outcome.status,
            "provider": outcome.provider,
        }
        if outcome.url:
            result["url"] = outcome.url
        if outcome.provider_pr_id:
            result["provider_pr_id"] = outcome.provider_pr_id
        if outcome.branch:
            result["branch"] = outcome.branch
        if outcome.error:
            result["error"] = outcome.error
        return result

    @app.post("/api/bundles/{bundle_id}/accept")
    def api_bundle_accept(
        bundle_id: str, body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Operator accept (Step 11c + 28e follow-up).

        Gated on ``verification_status='verified'`` (409 otherwise).
        Sets ``resolution='accepted'`` + ``accepted_at``; emits a
        ``bundle_accepted`` event.

        Step 28e follow-up: when the prior resolution was
        ``operator_owned`` (operator's manual fix being accepted),
        also release the ``origin_skip_flags`` row if this bundle
        owns it. Without this the lock would stay forever — accept
        is terminal, and ``/reopen`` only clears locks on reopen-
        from-discarded. Mirrors release/reopen's own/sibling
        semantics. The ``skip_action`` field on the response and
        the event payload makes the lock disposition observable.
        """
        from datetime import datetime, timezone  # noqa: PLC0415

        with _conn() as conn:
            row = get_bundle(conn, bundle_id)
        if row is None:
            raise HTTPException(
                status_code=404, detail=f"Unknown bundle: {bundle_id}",
            )
        if row.get("resolution") in ("accepted", "rejected", "discarded"):
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Cannot accept bundle in terminal state "
                    f"{row.get('resolution')!r}"
                ),
            )
        if row.get("verification_status") != "verified":
            raise HTTPException(
                status_code=409,
                detail=(
                    "Accept requires verification_status='verified'; "
                    f"current: {row.get('verification_status') or 'unverified'!r}"
                ),
            )

        prior_resolution = row.get("resolution")
        target = (row.get("target") or "").strip()
        origin = (row.get("origin") or "").strip()
        now = datetime.now(timezone.utc).isoformat()

        write_conn = sqlite3.connect(
            str(app.state.db_path), check_same_thread=False,
            isolation_level=None,
        )
        write_conn.row_factory = sqlite3.Row
        skip_action = "none"
        try:
            write_conn.execute(
                """UPDATE bundles SET
                       resolution = 'accepted',
                       accepted_at = ?,
                       pre_terminal_resolution = ?,
                       last_seen_at = ?
                   WHERE bundle_id = ?""",
                (now, prior_resolution, now, bundle_id),
            )
            # 28e follow-up: only the operator_owned → accepted
            # path interacts with the skip lock. agent_fixed accepts
            # never opened a lock, so there's nothing to clear.
            if prior_resolution == "operator_owned" and target and origin:
                existing = is_origin_skipped(write_conn, target, origin)
                if existing is not None:
                    if existing.get("bundle_id") == bundle_id:
                        clear_origin_skip(
                            write_conn,
                            target=target, origin=origin,
                            cleared_by="operator-accept",
                        )
                        skip_action = "cleared"
                    else:
                        skip_action = (
                            f"left_intact_owned_by:"
                            f"{existing.get('bundle_id')}"
                        )
            from dportsv3.artifact_store import emit_event  # noqa: PLC0415
            emit_event(write_conn, "bundle_accepted", {
                "bundle_id": bundle_id,
                "accepted_at": now,
                "prior_resolution": prior_resolution,
                "skip_action": skip_action,
                "note": (body or {}).get("note"),
            })

            # Visibility plan: activity row + daemon log for every
            # accept. Pairs with the delivery_complete row below so
            # operators see the full accept-then-deliver story on
            # the bundle's activity ribbon and `get-activity` output.
            accept_msg = (
                f"bundle {bundle_id} accepted (prior_resolution="
                f"{prior_resolution!r}, skip_action={skip_action!r})"
            )
            _activity_log(
                write_conn, "bundle_accepted", accept_msg,
                bundle_id=bundle_id,
                extra={
                    "bundle_id": bundle_id,
                    "prior_resolution": prior_resolution,
                    "skip_action": skip_action,
                    "accepted_at": now,
                },
            )
            _LOG.info(accept_msg)

            # Step 11d-2: Accept-with-delivery. Best-effort — any
            # exception path here writes a create_failed row and
            # logs but the bundle stays accepted. Skipped silently
            # when delivery.toml isn't configured.
            delivery = _accept_delivery_step(
                bundle=row,
                request_body=(body or {}),
                write_conn=write_conn,
            )

            # Visibility plan: one activity row + one daemon log
            # line per delivery outcome, regardless of status. The
            # prior shape only wrote a bundle_review_requests row
            # for provider-stage outcomes (created / updated /
            # provider-raised create_failed); skips and pre-provider
            # config errors went into a black hole. This row covers
            # all of them in one stage so a tail / grep / activity-
            # table render shows the full story.
            d_status = (delivery or {}).get("status", "unknown")
            d_skip = (delivery or {}).get("skip_reason")
            d_url = (delivery or {}).get("url")
            d_err = (delivery or {}).get("error")
            d_provider = (delivery or {}).get("provider")
            d_request = (delivery or {}).get("request_id")
            if d_status == "skipped":
                d_msg = (
                    f"delivery skipped for {bundle_id}: "
                    f"{d_skip or 'unknown'}"
                )
                _LOG.info(d_msg)
            elif d_status in ("created", "updated"):
                d_msg = (
                    f"delivery {d_status} for {bundle_id} "
                    f"(provider={d_provider}, url={d_url or 'n/a'})"
                )
                _LOG.info(d_msg)
            elif d_status == "create_failed":
                d_msg = (
                    f"delivery FAILED for {bundle_id}: "
                    f"{d_err or 'unspecified error'}"
                )
                _LOG.error(d_msg)
            else:
                d_msg = (
                    f"delivery completed for {bundle_id} with "
                    f"unrecognized status={d_status!r}"
                )
                _LOG.warning(d_msg)
            _activity_log(
                write_conn, "delivery_complete", d_msg,
                bundle_id=bundle_id,
                extra={
                    "bundle_id": bundle_id,
                    "status": d_status,
                    "skip_reason": d_skip,
                    "provider": d_provider,
                    "url": d_url,
                    "request_id": d_request,
                    "error": d_err,
                },
            )
        finally:
            write_conn.close()

        return {
            "ok": True,
            "bundle_id": bundle_id,
            "resolution": "accepted",
            "accepted_at": now,
            "prior_resolution": prior_resolution,
            "skip_action": skip_action,
            # _accept_delivery_step always returns a dict (skipped /
            # created / updated / create_failed), so `delivery` is
            # always present on the response.
            "delivery": delivery,
        }

    @app.post("/api/bundles/{bundle_id}/reject")
    def api_bundle_reject(
        bundle_id: str, body: dict[str, Any],
    ) -> dict[str, Any]:
        """Operator reject (Step 11c). Terminal "this fix is wrong,
        stop" action: sets resolution='rejected' + rejected_at +
        rejection_reason and stops. The reason is recorded for audit
        only — reject does NOT re-triage. An operator who wants the
        loop to try again with feedback uses /retry instead (allowed
        on agent_fixed), which plants the feedback as user_context
        and re-triages.

        Body: ``{"reason": "<text>"}``. Reason is required (an
        unexplained reject is uninformative)."""
        from datetime import datetime, timezone  # noqa: PLC0415

        reason = (body or {}).get("reason")
        if not reason or not isinstance(reason, str):
            raise HTTPException(
                status_code=400,
                detail="body must include 'reason' (rejection text)",
            )
        with _conn() as conn:
            row = get_bundle(conn, bundle_id)
        if row is None:
            raise HTTPException(
                status_code=404, detail=f"Unknown bundle: {bundle_id}",
            )
        if row.get("resolution") in ("accepted", "rejected", "discarded"):
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Cannot reject bundle in terminal state "
                    f"{row.get('resolution')!r}"
                ),
            )

        prior_resolution = row.get("resolution")
        now = datetime.now(timezone.utc).isoformat()
        write_conn = sqlite3.connect(
            str(app.state.db_path), check_same_thread=False,
            isolation_level=None,
        )
        write_conn.row_factory = sqlite3.Row
        try:
            write_conn.execute(
                """UPDATE bundles SET
                       resolution = 'rejected',
                       rejected_at = ?,
                       rejection_reason = ?,
                       pre_terminal_resolution = ?,
                       last_seen_at = ?
                   WHERE bundle_id = ?""",
                (now, reason, prior_resolution, now, bundle_id),
            )
            from dportsv3.artifact_store import emit_event  # noqa: PLC0415
            emit_event(write_conn, "bundle_rejected", {
                "bundle_id": bundle_id,
                "rejected_at": now,
                "reason": reason,
            })
        finally:
            write_conn.close()

        return {
            "ok": True,
            "bundle_id": bundle_id,
            "resolution": "rejected",
            "rejected_at": now,
            "reason": reason,
        }

    @app.post("/api/bundles/{bundle_id}/take-over")
    def api_bundle_take_over(
        bundle_id: str, body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Operator take-over of a failed bundle (Step 28a).

        Stakes the (target, origin) pair so the runner stops competing
        with the operator's manual work. Sets bundle.resolution to
        ``operator_owned`` (non-terminal — Step 11c's Verify/Accept
        path can still fire from this state) and opens a row in
        ``origin_skip_flags`` so subsequent dsynth hooks for the same
        pair produce a tombstone bundle instead of fresh triage.

        Allowed only from failure resolutions
        (``agent_budget_exhausted`` / ``agent_gave_up`` /
        ``escalated_manual``). 409 from already-terminal accept/reject
        or already-operator_owned. 404 if bundle unknown.

        Body (all optional):
          - ``operator``: freeform identifier (defaults to "operator");
            integrating with the auth model is Step 17 territory.
          - ``reason``: short note describing the take-over context;
            defaults to a generic label.
        """
        from datetime import datetime, timezone  # noqa: PLC0415

        operator = ((body or {}).get("operator") or "operator").strip()
        reason = ((body or {}).get("reason") or "operator take-over").strip()
        if not operator:
            operator = "operator"
        if not reason:
            reason = "operator take-over"

        with _conn() as conn:
            row = get_bundle(conn, bundle_id)
        if row is None:
            raise HTTPException(
                status_code=404, detail=f"Unknown bundle: {bundle_id}",
            )
        current_resolution = row.get("resolution")
        if current_resolution in ("accepted", "rejected", "discarded"):
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Cannot take over bundle in terminal state "
                    f"{current_resolution!r}"
                ),
            )
        if current_resolution == "operator_owned":
            raise HTTPException(
                status_code=409,
                detail="Bundle is already operator_owned",
            )
        # The take-over is meaningful only on failure-shaped bundles.
        # Success-shaped ones (agent_fixed) have Step 11c's
        # Accept/Reject as the appropriate operator action surface;
        # a takeover there would muddle the resolution lane.
        allowed_from = {
            "agent_budget_exhausted", "agent_gave_up",
            "escalated_manual", "convert_gave_up", None,
        }
        if current_resolution not in allowed_from:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Take-over is for failure-shaped bundles; "
                    f"current resolution is {current_resolution!r}"
                ),
            )

        target = (row.get("target") or "").strip()
        origin = (row.get("origin") or "").strip()
        if not target or not origin:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Bundle is missing target/origin metadata; cannot "
                    "open a skip lock for the (target, origin) pair"
                ),
            )

        now = datetime.now(timezone.utc).isoformat()
        write_conn = sqlite3.connect(
            str(app.state.db_path), check_same_thread=False,
            isolation_level=None,
        )
        write_conn.row_factory = sqlite3.Row
        try:
            # Pre-check the open-lock invariant against a different
            # bundle for the same (target, origin) pair — could happen
            # if the operator already took over a sibling bundle.
            existing = is_origin_skipped(write_conn, target, origin)
            if existing is not None and existing.get("bundle_id") != bundle_id:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"(target={target!r}, origin={origin!r}) is "
                        f"already locked by bundle "
                        f"{existing.get('bundle_id')!r}; un-skip it first"
                    ),
                )
            write_conn.execute("BEGIN IMMEDIATE")
            try:
                write_conn.execute(
                    """UPDATE bundles SET
                           resolution = 'operator_owned',
                           taken_over_at = ?,
                           taken_over_by = ?,
                           last_seen_at = ?
                       WHERE bundle_id = ?""",
                    (now, operator, now, bundle_id),
                )
                if existing is None:
                    try:
                        set_origin_skip(
                            write_conn,
                            target=target, origin=origin,
                            set_by=operator,
                            reason=reason,
                            bundle_id=bundle_id,
                        )
                    except sqlite3.IntegrityError:
                        # Race window: another operator opened the lock
                        # between our pre-check and this INSERT. The
                        # partial-unique index correctly rejected the
                        # duplicate; convert to 409 so the caller gets
                        # the same answer as the pre-check would have.
                        write_conn.execute("ROLLBACK")
                        raise HTTPException(
                            status_code=409,
                            detail=(
                                f"(target={target!r}, origin={origin!r}) "
                                f"was just locked by a concurrent operator "
                                f"action; refresh and try again"
                            ),
                        )
                write_conn.execute("COMMIT")
            except HTTPException:
                raise
            except Exception:
                write_conn.execute("ROLLBACK")
                raise
            from dportsv3.artifact_store import emit_event  # noqa: PLC0415
            emit_event(write_conn, "bundle_taken_over", {
                "bundle_id": bundle_id,
                "target": target,
                "origin": origin,
                "taken_over_at": now,
                "taken_over_by": operator,
                "reason": reason,
            })
        finally:
            write_conn.close()

        return {
            "ok": True,
            "bundle_id": bundle_id,
            "resolution": "operator_owned",
            "taken_over_at": now,
            "taken_over_by": operator,
            "target": target,
            "origin": origin,
        }

    @app.post("/api/bundles/{bundle_id}/discard")
    def api_bundle_discard(
        bundle_id: str, body: dict[str, Any],
    ) -> dict[str, Any]:
        """Operator discard of a failed (or operator-owned) bundle
        (Step 28b).

        Marks the bundle resolved as ``discarded`` (terminal) and,
        when ``skip_origin`` is true (the default), opens a row in
        ``origin_skip_flags`` so the runner stops auto-processing
        the (target, origin) pair until an operator un-skips it.

        Allowed from failure-shaped resolutions
        (``agent_budget_exhausted`` / ``agent_gave_up`` /
        ``escalated_manual`` / ``convert_gave_up``) and from
        ``operator_owned`` (operator gave a manual fix a try, then
        decided to drop it). 409 from already-terminal resolutions
        (``accepted`` / ``rejected`` / ``discarded``) and from
        ``agent_fixed`` (success-side bundles route through Step
        11c's Reject). 404 if bundle unknown.

        Body (``reason`` required; an unexplained discard is
        uninformative forensics):
          - ``reason`` (str, required): why the operator is
            walking away from this bundle.
          - ``skip_origin`` (bool, default true): when true, opens
            a per-``(target, origin)`` lock alongside the discard.
            Pass false for "discard just this bundle; let the loop
            try again on the next dsynth hook."
          - ``operator`` (str, optional): freeform identifier;
            defaults to ``"operator"``. Step 17 territory for real
            auth.

        If a sibling bundle already locked the (target, origin)
        pair, the discard still succeeds — the lock is shared
        forensics across the discard / take-over paths, and the
        existing lock keeps its set_by / bundle_id provenance.
        """
        from datetime import datetime, timezone  # noqa: PLC0415

        reason = (body or {}).get("reason")
        if not reason or not isinstance(reason, str) or not reason.strip():
            raise HTTPException(
                status_code=400,
                detail="body must include 'reason' (discard text)",
            )
        reason = reason.strip()
        operator = ((body or {}).get("operator") or "operator").strip()
        if not operator:
            operator = "operator"
        skip_origin = bool((body or {}).get("skip_origin", True))

        with _conn() as conn:
            row = get_bundle(conn, bundle_id)
        if row is None:
            raise HTTPException(
                status_code=404, detail=f"Unknown bundle: {bundle_id}",
            )
        current_resolution = row.get("resolution")
        if current_resolution in ("accepted", "rejected", "discarded"):
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Cannot discard bundle in terminal state "
                    f"{current_resolution!r}"
                ),
            )
        allowed_from = {
            "agent_budget_exhausted", "agent_gave_up",
            "escalated_manual", "convert_gave_up",
            "operator_owned", None,
        }
        if current_resolution not in allowed_from:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Discard is for failure-shaped or operator-owned "
                    f"bundles; current resolution is {current_resolution!r}"
                ),
            )

        target = (row.get("target") or "").strip()
        origin = (row.get("origin") or "").strip()
        if skip_origin and (not target or not origin):
            raise HTTPException(
                status_code=409,
                detail=(
                    "Bundle is missing target/origin metadata; cannot "
                    "open a skip lock. Re-issue with skip_origin=false "
                    "to discard the bundle without locking."
                ),
            )

        now = datetime.now(timezone.utc).isoformat()
        write_conn = sqlite3.connect(
            str(app.state.db_path), check_same_thread=False,
            isolation_level=None,
        )
        write_conn.row_factory = sqlite3.Row
        skip_action = "none"
        try:
            existing = (
                is_origin_skipped(write_conn, target, origin)
                if skip_origin and target and origin else None
            )
            write_conn.execute("BEGIN IMMEDIATE")
            try:
                write_conn.execute(
                    """UPDATE bundles SET
                           resolution = 'discarded',
                           discarded_at = ?,
                           discard_reason = ?,
                           pre_terminal_resolution = ?,
                           last_seen_at = ?
                       WHERE bundle_id = ?""",
                    (now, reason, current_resolution, now, bundle_id),
                )
                if skip_origin and target and origin and existing is None:
                    try:
                        set_origin_skip(
                            write_conn,
                            target=target, origin=origin,
                            set_by=operator,
                            reason=f"discard: {reason}",
                            bundle_id=bundle_id,
                        )
                        skip_action = "opened"
                    except sqlite3.IntegrityError:
                        # Race window: a concurrent operator action
                        # opened the lock between our pre-check and
                        # this INSERT. The discard itself still lands
                        # (the bundle is being walked away from
                        # regardless of who locks the origin), but
                        # report skip_action so the operator sees
                        # what happened.
                        skip_action = "race_lost_to_concurrent_lock"
                elif skip_origin and existing is not None:
                    # Sibling already locked the pair; don't duplicate.
                    # The discard still lands; the existing lock keeps
                    # its provenance. Operator visibility comes from
                    # the bundle_discarded event + the existing skip
                    # flag (which is the same forensic record either
                    # take-over or discard would have written).
                    skip_action = (
                        f"already_locked_by:{existing.get('bundle_id')}"
                    )
                write_conn.execute("COMMIT")
            except Exception:
                write_conn.execute("ROLLBACK")
                raise
            from dportsv3.artifact_store import emit_event  # noqa: PLC0415
            emit_event(write_conn, "bundle_discarded", {
                "bundle_id": bundle_id,
                "target": target,
                "origin": origin,
                "discarded_at": now,
                "discarded_by": operator,
                "reason": reason,
                "skip_origin": skip_origin,
                "skip_action": skip_action,
            })
        finally:
            write_conn.close()

        return {
            "ok": True,
            "bundle_id": bundle_id,
            "resolution": "discarded",
            "discarded_at": now,
            "discard_reason": reason,
            "target": target,
            "origin": origin,
            "skip_origin": skip_origin,
            "skip_action": skip_action,
        }

    @app.post("/api/bundles/{bundle_id}/retry")
    def api_bundle_retry(
        bundle_id: str, body: dict[str, Any],
    ) -> dict[str, Any]:
        """Operator retry-with-context on a failed (or operator-owned)
        bundle (Step 28c).

        Plants the same DB rows the manual-queue context-submit path
        plants (``user_context`` text + ``user_context_requests``
        pending row) so the runner's existing
        ``process_user_context_updates`` poll picks the retry up
        without any runner-side changes. The bundle's resolution
        moves to ``retry_requested`` (transient) until the runner
        enqueues the new triage.

        Allowed from failure-shaped resolutions
        (``agent_budget_exhausted`` / ``agent_gave_up`` /
        ``escalated_manual`` / ``convert_gave_up``), from
        ``operator_owned`` (operator gives up on the manual attempt
        and hands back to the loop with context), and from
        ``agent_fixed`` — the "this fix is wrong, try again with my
        feedback" path for a verified-but-rejected fix. (Reject is
        the separate *terminal* "this fix is wrong, stop" action; it
        records a reason but does NOT re-triage.) 409 only from the
        already-terminal states accept / reject / discarded.

        Body:
          - ``context`` (str, required, ≤ 8000 chars): operator's
            note that will land in the next triage's payload via
            the existing ``## User Context`` section.
          - ``operator`` (str, optional): freeform identifier.
        """
        from datetime import datetime, timezone  # noqa: PLC0415

        text = ((body or {}).get("context") or "")
        if not isinstance(text, str):
            raise HTTPException(
                status_code=400,
                detail="body 'context' must be a string",
            )
        text = text.strip()
        if not text:
            raise HTTPException(
                status_code=400,
                detail="body 'context' is required (non-empty)",
            )
        if len(text) > 8000:
            raise HTTPException(
                status_code=400,
                detail="body 'context' too long (max 8000 chars)",
            )
        # Two distinct roles for the body's ``operator`` field:
        #   - ``submitted_by`` on the new user_context_history row: NULL
        #     when the operator didn't identify themselves, matching
        #     /api/manual-requests/.../context's NULL-on-empty behavior.
        #   - ``requested_by`` on the response/event payload: keeps the
        #     pre-29b "operator" literal fallback so existing event
        #     consumers see a non-empty string.
        raw_operator = ((body or {}).get("operator") or "").strip()
        submitted_by = raw_operator or None
        operator = raw_operator or "operator"

        with _conn() as conn:
            row = get_bundle(conn, bundle_id)
        if row is None:
            raise HTTPException(
                status_code=404, detail=f"Unknown bundle: {bundle_id}",
            )
        current_resolution = row.get("resolution")
        if current_resolution in ("accepted", "rejected", "discarded"):
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Cannot retry bundle in terminal state "
                    f"{current_resolution!r}"
                ),
            )
        run_id = (row.get("run_id") or "").strip()
        origin = (row.get("origin") or "").strip()
        if not run_id or not origin:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Bundle is missing run_id/origin metadata; cannot "
                    "plant a retry request"
                ),
            )

        now = datetime.now(timezone.utc).isoformat()
        write_conn = sqlite3.connect(
            str(app.state.db_path), check_same_thread=False,
            isolation_level=None,
        )
        write_conn.row_factory = sqlite3.Row
        try:
            # Atomicity note: BEGIN IMMEDIATE doesn't work cleanly
            # here because upsert_user_context_text commits internally
            # (it's a shared helper used by other paths). Mirroring
            # 11c's accept/reject pattern, the three writes are
            # sequential under autocommit. Partial-failure tradeoff
            # is acceptable: a user_context row that landed without
            # a matching UCR row gets picked up by the next operator
            # /retry call; a UCR row without a bundle resolution
            # update degrades the UI badge but not correctness — the
            # runner's poll loop still acts on the UCR row.
            new_rev = upsert_user_context_text(
                write_conn, run_id, origin, text,
                submitted_by=submitted_by,
            )
            # Default iteration to 1 — operator-driven retry is an
            # explicit override of the automated retry-cap logic.
            # max_iterations defaults to the runner's standard
            # (matches enqueue_triage_job's caller convention).
            ucr_row = write_conn.execute(
                """SELECT 1 FROM user_context_requests
                   WHERE run_id = ? AND origin = ? AND bundle_id = ?""",
                (run_id, origin, bundle_id),
            ).fetchone()
            if ucr_row:
                write_conn.execute(
                    """UPDATE user_context_requests
                       SET requested_at = ?, status = 'pending',
                           iteration = ?, max_iterations = ?
                       WHERE run_id = ? AND origin = ? AND bundle_id = ?""",
                    (now, 1, 3, run_id, origin, bundle_id),
                )
            else:
                write_conn.execute(
                    """INSERT INTO user_context_requests
                       (run_id, origin, bundle_id, classification,
                        confidence, iteration, max_iterations,
                        requested_at, status,
                        last_context_rev_handled)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?,
                               'pending', ?)""",
                    (run_id, origin, bundle_id,
                     row.get("classification") or "",
                     row.get("confidence") or "",
                     1, 3, now, max(0, new_rev - 1)),
                )
            # Move bundle resolution to retry_requested (transient).
            # The runner's enqueue path (process_user_context_updates)
            # clears this back to NULL when it actually plants the
            # new triage job, so a stuck retry_requested is observable.
            write_conn.execute(
                """UPDATE bundles SET
                       resolution = 'retry_requested',
                       last_seen_at = ?
                   WHERE bundle_id = ?""",
                (now, bundle_id),
            )
            from dportsv3.artifact_store import emit_event  # noqa: PLC0415
            emit_event(write_conn, "bundle_retry_requested", {
                "bundle_id": bundle_id,
                "run_id": run_id,
                "origin": origin,
                "requested_at": now,
                "requested_by": operator,
                "context_rev": new_rev,
                "context_chars": len(text),
            })
        finally:
            write_conn.close()

        return {
            "ok": True,
            "bundle_id": bundle_id,
            "resolution": "retry_requested",
            "run_id": run_id,
            "origin": origin,
            "context_rev": new_rev,
            "requested_at": now,
            "requested_by": operator,
        }

    @app.post("/api/bundles/{bundle_id}/release")
    def api_bundle_release(
        bundle_id: str, body: dict[str, Any],
    ) -> dict[str, Any]:
        """Operator release of an ``operator_owned`` bundle (Step 28e).

        The "Hand back to the loop" action: the operator stops
        staking the bundle's (target, origin) pair, without
        terminalizing it via Discard. Bundle moves back to a NULL
        resolution (re-actionable); the skip lock is released iff
        this bundle owns it.

        Allowed only from ``operator_owned``. 409 from any other
        resolution. 404 unknown bundle.

        Body:
          - ``reason`` (str, required): why the stake is being
            released. Audit trail.
          - ``operator`` (str, optional): freeform identifier.

        Side effects:
          - ``resolution`` → NULL. ``taken_over_at`` and
            ``taken_over_by`` are preserved as historical record;
            only the live resolution clears.
          - If this bundle owns the open ``origin_skip_flags`` row
            for its (target, origin), the lock is cleared. If a
            sibling owns it, it's left intact — that's the
            sibling's stake to release.
          - Emits ``bundle_released`` event.
        """
        from datetime import datetime, timezone  # noqa: PLC0415

        reason = (body or {}).get("reason")
        if not reason or not isinstance(reason, str) or not reason.strip():
            raise HTTPException(
                status_code=400,
                detail="body must include 'reason' (release text)",
            )
        reason = reason.strip()
        operator = ((body or {}).get("operator") or "operator").strip()
        if not operator:
            operator = "operator"

        with _conn() as conn:
            row = get_bundle(conn, bundle_id)
        if row is None:
            raise HTTPException(
                status_code=404, detail=f"Unknown bundle: {bundle_id}",
            )
        if row.get("resolution") != "operator_owned":
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Release requires resolution='operator_owned'; "
                    f"current is {row.get('resolution')!r}"
                ),
            )

        target = (row.get("target") or "").strip()
        origin = (row.get("origin") or "").strip()
        now = datetime.now(timezone.utc).isoformat()

        write_conn = sqlite3.connect(
            str(app.state.db_path), check_same_thread=False,
            isolation_level=None,
        )
        write_conn.row_factory = sqlite3.Row
        skip_action = "none"
        try:
            write_conn.execute("BEGIN IMMEDIATE")
            try:
                write_conn.execute(
                    """UPDATE bundles SET
                           resolution = NULL,
                           last_seen_at = ?
                       WHERE bundle_id = ?""",
                    (now, bundle_id),
                )
                if target and origin:
                    existing = is_origin_skipped(write_conn, target, origin)
                    if existing is not None:
                        if existing.get("bundle_id") == bundle_id:
                            clear_origin_skip(
                                write_conn,
                                target=target, origin=origin,
                                cleared_by=operator,
                            )
                            skip_action = "cleared"
                        else:
                            skip_action = (
                                f"left_intact_owned_by:"
                                f"{existing.get('bundle_id')}"
                            )
                write_conn.execute("COMMIT")
            except Exception:
                write_conn.execute("ROLLBACK")
                raise
            from dportsv3.artifact_store import emit_event  # noqa: PLC0415
            emit_event(write_conn, "bundle_released", {
                "bundle_id": bundle_id,
                "target": target,
                "origin": origin,
                "released_at": now,
                "released_by": operator,
                "reason": reason,
                "skip_action": skip_action,
            })
        finally:
            write_conn.close()

        return {
            "ok": True,
            "bundle_id": bundle_id,
            "resolution": None,
            "released_at": now,
            "released_by": operator,
            "reason": reason,
            "target": target,
            "origin": origin,
            "skip_action": skip_action,
        }

    @app.post("/api/bundles/{bundle_id}/reopen")
    def api_bundle_reopen(
        bundle_id: str, body: dict[str, Any],
    ) -> dict[str, Any]:
        """Operator reopen of a terminally-resolved bundle (Step 28d).

        Clears ``bundle.resolution`` back to NULL so the bundle is
        actionable again (take-over / discard / retry can fire from
        a fresh slate). Rare — undo path for an operator decision
        that turned out to be wrong (accepted a fix that then broke,
        rejected a fix that was actually correct, discarded a port
        that should be retried).

        Allowed only from terminal resolutions (``accepted`` /
        ``rejected`` / ``discarded``). 409 from any non-terminal
        state (no point reopening). 404 unknown bundle.

        Body:
          - ``reason`` (str, required): why the prior terminal
            decision is being undone. Audit trail; freeform.
          - ``operator`` (str, optional): freeform identifier.

        Side effects:
          - ``resolution`` → NULL.
          - ``reopened_at``, ``reopened_by``, ``reopened_from``
            populated. Prior terminal-state columns
            (``accepted_at``, ``rejected_at``, ``discarded_*``,
            ``taken_over_*``) are preserved as historical record.
          - If the prior state was ``discarded`` AND this bundle
            holds the open ``origin_skip_flags`` row (its
            ``bundle_id`` matches), the lock is cleared. If a
            sibling holds the lock, it's left alone — that's the
            sibling's stake to release.
          - Emits ``bundle_reopened`` event with
            ``prior_resolution`` + ``skip_action``.
        """
        from datetime import datetime, timezone  # noqa: PLC0415

        reason = (body or {}).get("reason")
        if not reason or not isinstance(reason, str) or not reason.strip():
            raise HTTPException(
                status_code=400,
                detail="body must include 'reason' (reopen text)",
            )
        reason = reason.strip()
        operator = ((body or {}).get("operator") or "operator").strip()
        if not operator:
            operator = "operator"

        with _conn() as conn:
            row = get_bundle(conn, bundle_id)
        if row is None:
            raise HTTPException(
                status_code=404, detail=f"Unknown bundle: {bundle_id}",
            )
        prior = row.get("resolution")
        if prior not in ("accepted", "rejected", "discarded"):
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Reopen requires a terminal resolution; current "
                    f"is {prior!r} (nothing to undo)"
                ),
            )

        target = (row.get("target") or "").strip()
        origin = (row.get("origin") or "").strip()
        now = datetime.now(timezone.utc).isoformat()

        write_conn = sqlite3.connect(
            str(app.state.db_path), check_same_thread=False,
            isolation_level=None,
        )
        write_conn.row_factory = sqlite3.Row
        skip_action = "none"
        try:
            write_conn.execute("BEGIN IMMEDIATE")
            try:
                # Restore the pre-terminal resolution so the
                # operator-action gates (verify/accept/reject — all
                # keyed on resolution='agent_fixed' or
                # 'operator_owned') light up again. Falls back to
                # NULL for legacy rows where the snapshot wasn't
                # taken; those land in the "actionable from take-
                # over" lane, same as pre-restore behavior.
                restored = row.get("pre_terminal_resolution")
                write_conn.execute(
                    """UPDATE bundles SET
                           resolution = ?,
                           reopened_at = ?,
                           reopened_by = ?,
                           reopened_from = ?,
                           pre_terminal_resolution = NULL,
                           last_seen_at = ?
                       WHERE bundle_id = ?""",
                    (restored, now, operator, prior, now, bundle_id),
                )
                # Clear the origin skip lock if (a) we came from
                # 'discarded' (only path that opens a lock — accept
                # and reject don't), and (b) THIS bundle owns it.
                # Sibling-owned locks are left alone — the sibling
                # made the lock decision and should release it.
                if prior == "discarded" and target and origin:
                    existing = is_origin_skipped(write_conn, target, origin)
                    if existing is not None:
                        if existing.get("bundle_id") == bundle_id:
                            clear_origin_skip(
                                write_conn,
                                target=target, origin=origin,
                                cleared_by=operator,
                            )
                            skip_action = "cleared"
                        else:
                            skip_action = (
                                f"left_intact_owned_by:"
                                f"{existing.get('bundle_id')}"
                            )
                write_conn.execute("COMMIT")
            except Exception:
                write_conn.execute("ROLLBACK")
                raise
            from dportsv3.artifact_store import emit_event  # noqa: PLC0415
            emit_event(write_conn, "bundle_reopened", {
                "bundle_id": bundle_id,
                "target": target,
                "origin": origin,
                "reopened_at": now,
                "reopened_by": operator,
                "reopened_from": prior,
                "restored_resolution": restored,
                "reason": reason,
                "skip_action": skip_action,
            })
        finally:
            write_conn.close()

        return {
            "ok": True,
            "bundle_id": bundle_id,
            "resolution": restored,
            "reopened_at": now,
            "reopened_by": operator,
            "reopened_from": prior,
            "reason": reason,
            "target": target,
            "origin": origin,
            "skip_action": skip_action,
        }

    @app.post("/api/bundles/{bundle_id}/delivery/status")
    def api_bundle_delivery_status(
        bundle_id: str, body: dict[str, Any],
    ) -> dict[str, Any]:
        """Operator-driven manual status update on the latest
        ``bundle_review_requests`` row (Step 11d-5).

        Bridges to a future PR-status polling step (out of scope
        for 11d): for now the operator tells the tracker "I just
        merged this PR upstream" / "I closed the PR" so the bundle's
        Delivery card reflects reality.

        Body:
          - ``status`` (str, required): one of ``"merged"`` /
            ``"closed"``. Other values (``created`` / ``updated``)
            are reserved for the orchestrator and refused here.
          - ``note`` (str, optional): short freeform context. Stored
            as the row's ``error`` column with a ``note:`` prefix
            (the column is repurposed for both create failures and
            operator-supplied annotations — v1 keeps the schema small).

        Refuses (404) if the bundle has no delivery row. Refuses
        (409) if the latest row is already in a terminal state
        (``closed`` / ``merged``) or in ``create_failed`` — the
        status machine is one-way; an operator who needs to flip
        a terminal back to created can use the standard reopen
        flow (Step 28d) followed by a fresh Accept.
        """
        from datetime import datetime, timezone  # noqa: PLC0415

        status = (body or {}).get("status")
        if status not in ("merged", "closed"):
            raise HTTPException(
                status_code=400,
                detail=(
                    "body 'status' must be 'merged' or 'closed'; "
                    "'created'/'updated' are reserved for the "
                    "orchestrator"
                ),
            )
        note = (body or {}).get("note")
        if note is not None and not isinstance(note, str):
            raise HTTPException(
                status_code=400,
                detail="body 'note', if supplied, must be a string",
            )
        # Cap note length explicitly rather than silently
        # truncating — operators get a clear signal when their
        # note won't fit. Matches the /retry context cap (8000)
        # so operators don't have to remember two limits, but
        # the practical use case is much shorter (one line).
        _NOTE_MAX = 2000
        if note is not None and len(note) > _NOTE_MAX:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"body 'note' too long ({len(note)} chars; "
                    f"max {_NOTE_MAX})"
                ),
            )

        with _conn() as conn:
            latest = latest_review_request_for_bundle(conn, bundle_id)
        if latest is None:
            raise HTTPException(
                status_code=404,
                detail=(
                    f"Bundle {bundle_id!r} has no delivery row "
                    f"(was it ever Accepted?)"
                ),
            )
        prior_status = latest.get("status")
        if prior_status in ("merged", "closed", "create_failed"):
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Latest delivery row is in terminal state "
                    f"{prior_status!r}; manual status updates are "
                    f"a one-way street. Reopen the bundle (Step 28d) "
                    f"and re-Accept for a fresh delivery."
                ),
            )

        # 11d-5 Finding 7 follow-up: note lands in its own column.
        # Trim leading/trailing whitespace; empty notes are stored
        # as NULL (the operator skipped the prompt).
        note_text: str | None = None
        if note:
            stripped = note.strip()
            if stripped:
                note_text = stripped

        write_conn = sqlite3.connect(
            str(app.state.db_path), check_same_thread=False,
            isolation_level=None,
        )
        write_conn.row_factory = sqlite3.Row
        try:
            updated = update_review_request_status(
                write_conn,
                request_id=int(latest["id"]),
                status=status,
                note=note_text,
            )
            if not updated:
                # The row vanished between latest_review_request_for_bundle
                # and the UPDATE. Vanishingly rare; surface as 404.
                raise HTTPException(
                    status_code=404,
                    detail=(
                        f"Bundle {bundle_id!r} delivery row "
                        f"disappeared mid-update"
                    ),
                )
            from dportsv3.artifact_store import emit_event  # noqa: PLC0415
            emit_event(write_conn, "bundle_delivery_status_changed", {
                "bundle_id": bundle_id,
                "request_id": int(latest["id"]),
                "prior_status": prior_status,
                "new_status": status,
                "note": note,
            })
        finally:
            write_conn.close()

        now = datetime.now(timezone.utc).isoformat()
        return {
            "ok": True,
            "bundle_id": bundle_id,
            "request_id": int(latest["id"]),
            "status": status,
            "prior_status": prior_status,
            "last_synced_at": now,
            "note": note,
        }

    @app.get("/api/ports/{origin:path}")
    def api_port_bundles(
        origin: str,
        target: str | None = None,
        limit: int = Query(default=50, ge=1, le=500),
    ) -> list[dict[str, Any]]:
        with _conn() as conn:
            return list_port_bundles(conn, origin=origin, target=target, limit=limit)

    @app.get("/api/bundles/{bundle_id}/artifacts/{relpath:path}")
    def api_bundle_artifact(bundle_id: str, relpath: str) -> Any:
        # Resolve via artifact_refs, then stream from disk. Two backends:
        # 'blob' (content-addressed under blob_root/objects/sha256) or
        # 'fs' (absolute path in fs_path).
        with _conn() as conn:
            ref = get_artifact_ref(conn, bundle_id, relpath)
        if ref is None:
            raise HTTPException(status_code=404, detail="Unknown artifact")
        path = _resolve_artifact_path(app.state.artifact_root, ref)
        if path is None or not path.exists():
            raise HTTPException(status_code=404, detail="Artifact file missing")
        media_type, inline = _artifact_media_type(
            relpath, ref.get("kind"), fs_path=path,
        )
        # Set Content-Disposition: inline for text-like artifacts so the
        # browser renders them instead of triggering a download.
        headers = {"Content-Disposition": f"inline; filename=\"{Path(relpath).name}\""} if inline else None
        return FileResponse(str(path), media_type=media_type, headers=headers)

    @app.get("/api/events")
    def api_events(
        target: str | None = None,
        last_id: int = 0,
    ) -> Any:
        # Server-sent events: poll the events table on a 1s tick, emit
        # rows with id > last_id, filter by target (best-effort — see
        # events_since docstring).
        import asyncio
        import json as _json

        async def _gen() -> Any:
            cursor = int(last_id)
            try:
                while True:
                    with _conn() as conn:
                        rows = events_since(conn, last_id=cursor, target=target)
                    for row in rows:
                        cursor = max(cursor, int(row["id"]))
                        payload = _json.dumps(row, default=str)
                        yield f"event: {row['type']}\ndata: {payload}\n\n"
                    await asyncio.sleep(1.0)
            except asyncio.CancelledError:
                return

        return StreamingResponse(_gen(), media_type="text/event-stream")

    @app.get("/", response_class=HTMLResponse)
    def dashboard_index(request: RequestType) -> Any:
        with _conn() as conn:
            active_builds = get_active_builds_summary(conn)
            return templates.TemplateResponse(
                request,
                "index.html",
                {
                    "title": "Targets",
                    "targets": get_target_summary(conn),
                    "active_builds": active_builds,
                    "refresh_seconds": 30 if active_builds else None,
                },
            )

    # ------------------------------------------------------------------
    # Phase 4 step 6: agentic HTML views.
    # ------------------------------------------------------------------

    @app.get("/agentic", response_class=HTMLResponse)
    def agentic_index(request: RequestType) -> Any:
        with _conn() as conn:
            return templates.TemplateResponse(
                request,
                "agentic_index.html",
                {
                    "title": "Agentic",
                    "status": agentic_status(conn),
                    "env_health": env_health_statuses(conn),
                    "active_env": get_active_env(conn),
                    "recent_bundles": list_bundles(conn, limit=10),
                    "recent_jobs": list_jobs(conn, limit=10),
                },
            )

    @app.get("/agentic/bundles", response_class=HTMLResponse)
    def agentic_bundles(
        request: RequestType,
        target: str | None = None,
        origin: str | None = None,
    ) -> Any:
        target_value = target or None
        origin_value = (origin or "").strip() or None
        with _conn() as conn:
            return templates.TemplateResponse(
                request,
                "agentic_bundles.html",
                {
                    "title": "Bundles",
                    "bundles": list_bundles(
                        conn, target=target_value, origin=origin_value, limit=200
                    ),
                    "target_options": distinct_targets(conn),
                    "selected_target": target_value,
                    "selected_origin": origin_value,
                },
            )

    @app.get("/agentic/bundles/{bundle_id}", response_class=HTMLResponse)
    def agentic_bundle_detail(
        request: RequestType,
        bundle_id: str,
        artifact: str | None = None,
    ) -> Any:
        with _conn() as conn:
            bundle = get_bundle(conn, bundle_id)
            tool_trace_ref = get_artifact_ref(conn, bundle_id, "analysis/tool_trace.jsonl")
            intent_log_ref = get_artifact_ref(conn, bundle_id, "analysis/intent_log.json")
            selected_relpath = artifact or (_default_artifact_relpath(bundle) if bundle else None)
            selected_ref = (
                get_artifact_ref(conn, bundle_id, selected_relpath)
                if selected_relpath else None
            )
            # Step 9: prior attempts table. Other bundles for the same
            # (origin, target) so the operator can see the agent's
            # history at a glance from any bundle page.
            prior_attempts = (
                [b for b in list_port_bundles(
                    conn, origin=bundle.get("origin"),
                    target=bundle.get("target"), limit=10,
                ) if b["bundle_id"] != bundle_id]
                if bundle is not None else []
            )
            # Step 9: lifetime token usage for this port, across
            # every job (triage + each patch attempt).
            port_token_usage = (
                token_usage_for_port(
                    conn, origin=bundle.get("origin"),
                    target=bundle.get("target"),
                )
                if bundle is not None and bundle.get("origin") else None
            )
            # Step 20f / Step 11c layer-violation cleanup: the dops
            # state is now persisted to bundles.dops_state at triage
            # time by the runner (which has chroot access). The
            # tracker no longer reaches into the host filesystem to
            # compute it live. NULL on legacy rows where no triage
            # ran post-this-change — the template hides the pill in
            # that case.
            dops_state = bundle.get("dops_state") if bundle is not None else None
            # Step 11d-2: most-recent delivery attempt for the Delivery
            # card. None on bundles that haven't been delivered yet
            # (the template hides the card in that case).
            delivery_request = latest_review_request_for_bundle(
                conn, bundle_id,
            )
            # Visibility plan: tracker-side activity rows
            # (bundle_accepted, delivery_complete) live with
            # bundle_id set but job_id=NULL. Surface them on the
            # bundle page directly so accept-and-deliver outcomes
            # are visible without dropping out to the CLI.
            bundle_activity = recent_activity_for_bundle(
                conn, bundle_id, limit=20,
            )
        if bundle is None:
            raise HTTPException(status_code=404, detail=f"Unknown bundle: {bundle_id}")
        if selected_relpath and selected_ref is None:
            raise HTTPException(status_code=404, detail="Unknown artifact")
        selected_artifact = (
            _artifact_view_data(app.state.artifact_root, bundle_id, selected_relpath, selected_ref)
            if selected_relpath and selected_ref else None
        )
        if selected_relpath and selected_artifact is None:
            raise HTTPException(status_code=404, detail="Artifact file missing")
        tool_trace = _load_tool_trace(app.state.artifact_root, tool_trace_ref)
        intent_log = _load_intent_log(app.state.artifact_root, intent_log_ref)
        # Step 11c: operator-action button matrix. Buttons show on
        # any bundle whose agent has finished (resolution=
        # 'agent_fixed') and the operator hasn't decided yet
        # (accepted/rejected are terminal). Verify is the gate:
        # Accept is enabled only when verification_status='verified'.
        # Reject is always enabled on the non-terminal cases — the
        # operator can refuse without verifying for obviously-wrong
        # fixes.
        resolution = (bundle.get("resolution") if bundle else None)
        actionable = resolution == "agent_fixed"
        # Step 28a: take-over action shows on failure-shaped
        # resolutions. The endpoint itself also accepts a NULL
        # resolution (a CLI user can stake a fresh bundle before
        # the loop touches it), but the UI surfacing is narrower —
        # surfacing it on a brand-new bundle that hasn't yet been
        # triaged would be racy and visually noisy. Terminal
        # states (accepted/rejected/discarded) and already-
        # operator_owned exclude it.
        failure_resolutions = {
            "agent_budget_exhausted",
            "agent_gave_up",
            "escalated_manual",
            "convert_gave_up",
        }
        can_take_over = (
            bundle is not None
            and resolution in failure_resolutions
            and bool(bundle.get("target"))
            and bool(bundle.get("origin"))
        )
        # Step 28b: discard surfaces on the same failure-shaped
        # resolutions as take-over, AND on operator_owned (operator
        # stakes a bundle then decides to drop it). Terminal states
        # (accepted/rejected/discarded) exclude it.
        can_discard = (
            bundle is not None
            and resolution in (failure_resolutions | {"operator_owned"})
        )
        # Step 28c: retry-with-context surfaces wherever discard does
        # (failure resolutions + operator_owned) AND on agent_fixed —
        # the "this verified fix is wrong, try again with my feedback"
        # path. Reject stays the separate *terminal* "wrong, stop"
        # action on agent_fixed; retry is "wrong, re-triage with
        # context". (Discard deliberately does NOT extend to
        # agent_fixed, so can_retry is no longer just can_discard.)
        can_retry = (
            bundle is not None
            and resolution in (
                failure_resolutions | {"operator_owned", "agent_fixed"}
            )
        )
        # Step 28d: reopen-from-terminal undoes an accept/reject/
        # discard. Rare; the only state where the button surfaces.
        can_reopen = resolution in ("accepted", "rejected", "discarded")
        # Step 28e: operator_owned bundles get the Verify button —
        # an operator who manually fixed something wants to verify
        # the build before moving on. Reuses 11c's verify endpoint
        # as-is.
        verify_eligible = actionable or (resolution == "operator_owned")
        # Step 28e follow-up: Accept also surfaces on operator_owned
        # once verification_status='verified'. The flow operators
        # want is "take over → fix manually → Verify → Accept";
        # without this gate the Verify ran but Accept stayed
        # disabled, dead-ending the manual-fix path. Reject stays
        # agent_fixed-only — its semantics (enqueue new triage with
        # rejection reason as user_context) make sense only for
        # rejecting an agent-produced fix.
        # Step 28e: release surfaces only on operator_owned — it's
        # the operator's "I'm done staking this, hand back to the
        # loop" action.
        can_release = resolution == "operator_owned"
        can_accept = (
            verify_eligible
            and bundle.get("verification_status") == "verified"
        )
        # Accept renders whenever it's contextually relevant: on the
        # agent_fixed lane (where 11c renders it disabled-before-verify
        # so the operator sees the path) AND on operator_owned-verified
        # (the manual-fix terminalization). Reject stays gated to
        # show_11c_group — its semantics (re-triage with rejection
        # reason as user_context) make sense only on agent_fixed.
        show_accept_button = actionable or can_accept
        # Env picker for the Verify button — replaces the old JS
        # prompt() which forced operators to remember the env name
        # by hand. Populate from env_health (the tracker's known
        # set of dev-envs) and default-select the active env. Skip
        # the lookup when Verify isn't even eligible — keeps the
        # accept-only / terminal lanes cheap.
        verify_envs: list[str] = []
        verify_default_env: str | None = None
        if verify_eligible:
            with _conn() as _envs_conn:
                verify_envs = [
                    str(r.get("env"))
                    for r in env_health_statuses(_envs_conn)
                    if r.get("env")
                ]
                verify_default_env = get_active_env(_envs_conn)
            # If the active env isn't in the health list (cleared,
            # decommissioned), fall back to the first env. None
            # stays None when no envs are known at all.
            if (
                verify_default_env is not None
                and verify_default_env not in verify_envs
            ):
                verify_default_env = (
                    verify_envs[0] if verify_envs else None
                )
            elif verify_default_env is None and verify_envs:
                verify_default_env = verify_envs[0]

        operator_actions = {
            "show": (actionable or can_take_over or can_discard
                     or can_retry or can_reopen or can_release),
            # show_11c_group still gates Reject; Accept moved to its
            # own flag (show_accept_button) so it can surface on
            # operator_owned-verified without dragging Reject along.
            "show_11c_group": actionable,
            "show_accept_button": show_accept_button,
            "can_verify": verify_eligible,
            "can_accept": can_accept,
            "can_reject": actionable,
            "can_take_over": can_take_over,
            "can_discard": can_discard,
            "can_retry": can_retry,
            "can_reopen": can_reopen,
            "can_release": can_release,
            "verify_envs": verify_envs,
            "verify_default_env": verify_default_env,
        }
        return templates.TemplateResponse(
            request,
            "agentic_bundle.html",
            {
                "title": bundle_id,
                "bundle": bundle,
                "tool_trace": tool_trace,
                "intent_log": intent_log,
                "selected_artifact": selected_artifact,
                "selected_artifact_relpath": selected_relpath,
                "prior_attempts": prior_attempts,
                "port_token_usage": port_token_usage,
                "dops_state": dops_state,
                "operator_actions": operator_actions,
                "delivery_request": delivery_request,
                "bundle_activity": bundle_activity,
            },
        )

    @app.get("/agentic/bundles/{bundle_id}/artifacts/{relpath:path}", response_class=HTMLResponse)
    def agentic_bundle_artifact_view(
        request: RequestType,
        bundle_id: str,
        relpath: str,
    ) -> Any:
        with _conn() as conn:
            bundle = get_bundle(conn, bundle_id)
            ref = get_artifact_ref(conn, bundle_id, relpath)
        if bundle is None:
            raise HTTPException(status_code=404, detail=f"Unknown bundle: {bundle_id}")
        if ref is None:
            raise HTTPException(status_code=404, detail="Unknown artifact")
        artifact = _artifact_view_data(app.state.artifact_root, bundle_id, relpath, ref)
        if artifact is None:
            raise HTTPException(status_code=404, detail="Artifact file missing")
        return templates.TemplateResponse(
            request,
            "agentic_artifact.html",
            {"title": relpath, "bundle": bundle, "artifact": artifact},
        )

    @app.get("/agentic/jobs", response_class=HTMLResponse)
    def agentic_jobs(
        request: RequestType,
        target: str | None = None,
        state: str | None = None,
    ) -> Any:
        target_value = target or None
        state_value = state or None
        with _conn() as conn:
            return templates.TemplateResponse(
                request,
                "agentic_jobs.html",
                {
                    "title": "Jobs",
                    "jobs": list_jobs(
                        conn, state=state_value, target=target_value, limit=200
                    ),
                    "target_options": distinct_targets(conn),
                    "selected_target": target_value,
                    "selected_state": state_value,
                },
            )

    @app.get("/agentic/jobs/{job_id}", response_class=HTMLResponse)
    def agentic_job_detail(
        request: RequestType,
        job_id: str,
        limit: int = 500,
        stage_filter: str | None = None,
    ) -> Any:
        limit = max(10, min(int(limit), 5000))
        # Normalize the filter. Step 9b — three pills: all/llm_turn/tool.
        sf = stage_filter if stage_filter in ("llm_turn", "tool") else None
        with _conn() as conn:
            job = get_job(conn, job_id)
            activity = (activity_for_job(conn, job_id, limit=limit,
                                          stage_filter=sf)
                        if job is not None else [])
            transitions = (
                job_events_for_job(conn, job_id, limit=limit)
                if job is not None else []
            )
            attempt_summary = (
                port_attempt_summary(
                    conn,
                    target=job.get("target"),
                    origin=job.get("origin"),
                    window_hours=int(os.environ.get("DP_HARNESS_ATTEMPT_WINDOW_HOURS", "2")),
                    max_attempts=int(os.environ.get("DP_HARNESS_MAX_PATCH_ATTEMPTS", "3")),
                ) if job is not None else None
            )
            token_usage = (
                token_usage_for_job(conn, job_id)
                if job is not None else None
            )
            # Step 9: prior-attempts table — recent bundles for the
            # same (origin, target) so the operator can see history.
            prior_attempts = (
                list_port_bundles(
                    conn, origin=job.get("origin"),
                    target=job.get("target"), limit=10,
                )
                if job is not None and job.get("origin") else []
            )
            # Step 9: when a job ends in 'escalated', operators
            # currently have to bounce out to /agentic/manual to read
            # the handoff. Inline it: pull the most recent bundle for
            # this (origin, target) that has manual_handoff.md and
            # render it next to the activity timeline.
            handoff = None
            if job is not None and job.get("state") == "escalated" and prior_attempts:
                for cand in prior_attempts:
                    ref = get_artifact_ref(
                        conn, cand["bundle_id"], "analysis/manual_handoff.md",
                    )
                    if ref is not None:
                        handoff = _artifact_view_data(
                            app.state.artifact_root,
                            cand["bundle_id"],
                            "analysis/manual_handoff.md",
                            ref,
                        )
                        handoff = dict(handoff or {})
                        handoff["bundle_id"] = cand["bundle_id"]
                        handoff["run_id"] = cand.get("run_id")
                        break
        if job is None:
            raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}")
        # Activity rows stay newest-first (the query default) so the
        # autorefresh JS — which prepends new rows to the top — keeps
        # the table consistent. Mixing ASC initial + prepended new
        # rows produced a chaotic sort (gperf/liblz4 2026-05-26):
        # live rows piled up at the top above an ASC-sorted body.
        # Cursor for the live-refresh polling — the client polls
        # /api/activity?job_id=X&since_id=N for new rows.
        max_id = max((a.get("id") or 0) for a in activity) if activity else 0
        # Whether the job is still doing its own work — drives the
        # live-poll indicator. Computed server-side from the single
        # canonical set (lifecycle.ACTIVE_WORK_STATES) rather than an
        # inline template literal, so it can't drift from the runner's
        # retriage guard and the dashboard count. Notably: a `triaged`
        # job is NOT active (it handed off to a spawned patch/convert
        # job and rests there), and `verifying_fix` IS active.
        from dportsv3.agent.lifecycle import (  # noqa: PLC0415
            ACTIVE_WORK_STATE_VALUES,
        )
        job_is_active = job.get("state") in ACTIVE_WORK_STATE_VALUES
        return templates.TemplateResponse(
            request,
            "agentic_job.html",
            {
                "title": job_id,
                "job": job,
                "activity": activity,
                "transitions": transitions,
                "attempt_summary": attempt_summary,
                "token_usage": token_usage,
                "max_activity_id": max_id,
                "limit": limit,
                "limit_options": [50, 200, 500, 2000, 5000],
                "stage_filter": sf,
                "prior_attempts": prior_attempts,
                "handoff": handoff,
                "job_is_active": job_is_active,
            },
        )

    @app.get("/agentic/runs/{run_id}", response_class=HTMLResponse)
    def agentic_run_detail(request: RequestType, run_id: str) -> Any:
        with _conn() as conn:
            run = get_run(conn, run_id)
            bundles = bundles_for_run(conn, run_id) if run is not None else []
        if run is None:
            raise HTTPException(status_code=404, detail=f"Unknown run: {run_id}")
        return templates.TemplateResponse(
            request,
            "agentic_run.html",
            {"title": run_id, "run": run, "bundles": bundles},
        )

    @app.get("/agentic/runner", response_class=HTMLResponse)
    def agentic_runner(request: RequestType) -> Any:
        with _conn() as conn:
            return templates.TemplateResponse(
                request,
                "agentic_runner.html",
                {"title": "Runner", "runner": runner_status(conn)},
            )

    @app.get("/agentic/activity", response_class=HTMLResponse)
    def agentic_activity(
        request: RequestType,
        target: str | None = None,
        limit: int = 200,
    ) -> Any:
        target_value = target or None
        limit = max(10, min(int(limit), 5000))
        with _conn() as conn:
            return templates.TemplateResponse(
                request,
                "agentic_activity.html",
                {
                    "title": "Activity",
                    "activity": recent_activity(conn, limit=limit, target=target_value),
                    "target_options": distinct_targets(conn),
                    "selected_target": target_value,
                    "limit": limit,
                    "limit_options": [50, 200, 500, 2000, 5000],
                },
            )

    # ------------------------------------------------------------------
    # Manual escalation queue (post-impl plan, Step 4).
    # Operators land here when a job escalates to MANUAL — read the
    # handoff artifact, type context, hit "Try again with this
    # context." POST writes the context_text + bumps context_rev; the
    # runner's existing process_user_context_updates loop picks it up
    # and re-enqueues a triage job.
    # ------------------------------------------------------------------

    @app.get("/agentic/manual", response_class=HTMLResponse)
    def agentic_manual_list(
        request: RequestType,
        open_only: bool = True,
    ) -> Any:
        with _conn() as conn:
            return templates.TemplateResponse(
                request,
                "agentic_manual_list.html",
                {
                    "title": "Manual Queue",
                    "requests": list_manual_requests(conn, open_only=open_only),
                    "open_only": open_only,
                },
            )

    @app.get("/agentic/manual/{run_id}/{origin:path}", response_class=HTMLResponse)
    def agentic_manual_detail(
        request: RequestType,
        run_id: str,
        origin: str,
    ) -> Any:
        with _conn() as conn:
            mr = get_manual_request(conn, run_id, origin)
            handoff = None
            blocking_job = (
                active_job_for_port(
                    conn, origin=origin, target=mr.get("target"),
                )
                if mr is not None else None
            )
            if mr is not None and mr.get("bundle_id"):
                ref = get_artifact_ref(
                    conn, mr["bundle_id"], "analysis/manual_handoff.md",
                )
                if ref is not None:
                    handoff = _artifact_view_data(
                        app.state.artifact_root,
                        mr["bundle_id"],
                        "analysis/manual_handoff.md",
                        ref,
                    )
        if mr is None:
            raise HTTPException(
                status_code=404,
                detail=f"No manual request for run={run_id} origin={origin}",
            )
        return templates.TemplateResponse(
            request,
            "agentic_manual_detail.html",
            {
                "title": f"Manual: {origin}",
                "request_row": mr,
                "handoff": handoff,
                "blocking_job": blocking_job,
            },
        )

    @app.get("/api/manual-requests")
    def api_manual_requests(open_only: bool = True) -> dict[str, Any]:
        with _conn() as conn:
            rows = list_manual_requests(conn, open_only=open_only)
        return {"requests": rows}

    @app.post(
        "/api/manual-requests/{run_id}/{origin:path}/context",
        response_model=ManualContextResponse,
    )
    def api_manual_submit_context(
        run_id: str,
        origin: str,
        payload: ManualContextRequest,
    ) -> dict[str, Any]:
        text = (payload.context_text or "").strip()
        if not text:
            raise HTTPException(
                status_code=400, detail="context_text cannot be empty",
            )
        if len(text) > 8000:
            raise HTTPException(
                status_code=400,
                detail="context_text too long (max 8000 chars)",
            )
        with _conn() as conn:
            mr = get_manual_request(conn, run_id, origin)
            if mr is None:
                raise HTTPException(
                    status_code=404,
                    detail=f"No manual request for run={run_id} origin={origin}",
                )
            operator = (payload.operator or "").strip() or None
            new_rev = upsert_user_context_text(
                conn, run_id, origin, text, submitted_by=operator,
            )
        return {"ok": True, "context_rev": new_rev}

    @app.post(
        "/api/manual-requests/{run_id}/{origin:path}/discard",
        response_model=ManualDiscardResponse,
    )
    def api_manual_discard(
        run_id: str,
        origin: str,
        payload: ManualDiscardRequest | None = None,
    ) -> dict[str, Any]:
        with _conn() as conn:
            mr = get_manual_request(conn, run_id, origin)
            if mr is None:
                raise HTTPException(
                    status_code=404,
                    detail=f"No manual request for run={run_id} origin={origin}",
                )
            reason = (payload.reason if payload else "") or ""
            discarded = discard_manual_request(conn, run_id, origin, reason)
        return {"ok": True, "discarded": discarded}

    # ------------------------------------------------------------------
    # Phase 5 step 1: dsynth-progress UI adapter. Lifts the
    # www/example/progress.{html,js,css} UI and feeds it from tracker
    # data via two JSON endpoints (summary + chunked history). No
    # change to the existing /target/{target} dashboard yet.
    # ------------------------------------------------------------------

    # JSON endpoints live under /api/progress/{target}/ to stay clear
    # of the legacy /target/{target}/{cat}/{port} catch-all. The HTML
    # page is served at the canonical /target/{target} below.

    @app.get("/api/progress/{target}/summary.json")
    def progress_summary(target: str) -> dict[str, Any]:
        with _conn() as conn:
            return target_summary(conn, target)

    @app.get("/api/progress/{target}/{chunk}_history.json")
    def progress_history(target: str, chunk: str) -> Any:
        try:
            chunk_index = int(chunk)
        except ValueError:
            raise HTTPException(status_code=404, detail="Bad chunk index")
        with _conn() as conn:
            # Returns [] past the last chunk — kfiles in summary.json
            # bounds the UI's fetch range so this is rarely hit.
            return target_history_chunk(conn, target, chunk_index)

    @app.get("/api/progress/build/{run_id}/summary.json")
    def progress_build_summary(run_id: int) -> dict[str, Any]:
        with _conn() as conn:
            summary = run_summary(conn, run_id)
        if summary is None:
            raise HTTPException(status_code=404, detail=f"Unknown build run: {run_id}")
        return summary

    @app.get("/api/progress/build/{run_id}/{chunk}_history.json")
    def progress_build_history(run_id: int, chunk: str) -> Any:
        try:
            chunk_index = int(chunk)
        except ValueError:
            raise HTTPException(status_code=404, detail="Bad chunk index")
        with _conn() as conn:
            return run_history_chunk(conn, run_id, chunk_index)

    @app.get("/target/{target}", response_class=HTMLResponse)
    def dashboard_target(request: RequestType, target: str) -> Any:
        # The page uses progress.{css,js} (lifted from dsynth-progress)
        # and fetches data from /api/progress/{target}/. The <base> tag
        # pins those relative URLs to the canonical API root.
        return templates.TemplateResponse(
            request,
            "progress.html",
            {
                "title": target,
                "target": target,
                "progress_base": f"/api/progress/{target}/",
            },
        )

    @app.get("/target/{target}/{cat}/{port}", response_class=HTMLResponse)
    def dashboard_port_detail(
        request: RequestType, target: str, cat: str, port: str
    ) -> Any:
        origin = f"{cat}/{port}"
        with _conn() as conn:
            rows = get_port_status(conn, target=target, origin=origin)
            if not rows:
                raise HTTPException(
                    status_code=404, detail=f"Unknown port status: {target} {origin}"
                )
            return templates.TemplateResponse(
                request,
                "port_detail.html",
                {
                    "title": f"{origin} {target}",
                    "target": target,
                    "origin": origin,
                    "status": rows[0],
                    "history": get_port_history(conn, target, origin, limit=20),
                },
            )

    @app.get("/builds", response_class=HTMLResponse)
    def dashboard_builds(
        request: RequestType,
        target: str | None = None,
        build_type: str | None = None,
        limit: int = Query(default=50, ge=1, le=500),
    ) -> Any:
        with _conn() as conn:
            runs = list_build_runs(
                conn, target=target, build_type=build_type, limit=limit
            )
            compare_links = _resolve_compare_links(runs)
            return templates.TemplateResponse(
                request,
                "builds.html",
                {
                    "title": "Builds",
                    "runs": runs,
                    "compare_links": compare_links,
                    "target": target,
                    "build_type": build_type,
                },
            )

    @app.get("/builds/compare", response_class=HTMLResponse)
    def dashboard_build_compare(request: RequestType, a: int, b: int) -> Any:
        with _conn() as conn:
            return templates.TemplateResponse(
                request,
                "build_compare.html",
                {
                    "title": "Build Compare",
                    "compare": compare_builds(conn, a, b),
                },
            )

    @app.get("/builds/{run_id}", response_class=HTMLResponse)
    def dashboard_build_detail(request: RequestType, run_id: int) -> Any:
        # Build detail uses the same dsynth-progress UI as /target/{target},
        # just scoped to one run_id. Verify the run exists so unknown
        # IDs 404 here rather than at the JSON fetch.
        try:
            with _conn() as conn:
                build = get_build_run(conn, run_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return templates.TemplateResponse(
            request,
            "progress.html",
            {
                "title": f"Build {run_id}",
                "target": f"{build['target']} (run {run_id})",
                "progress_base": f"/api/progress/build/{run_id}/",
            },
        )

    @app.get("/diff", response_class=HTMLResponse)
    def dashboard_diff(
        request: RequestType,
        a: str | None = None,
        b: str | None = None,
    ) -> Any:
        with _conn() as conn:
            targets = get_target_summary(conn)
            diff_payload = get_diff(conn, a, b) if a and b else None
            return templates.TemplateResponse(
                request,
                "diff.html",
                {
                    "title": "Target Diff",
                    "targets": targets,
                    "target_a": a,
                    "target_b": b,
                    "diff": diff_payload,
                },
            )

    return app


def _resolve_compare_links(runs: list[dict[str, Any]]) -> dict[int, int]:
    links: dict[int, int] = {}
    by_group: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for run in runs:
        key = (str(run["target"]), str(run["build_type"]))
        by_group.setdefault(key, []).append(run)
    for group_runs in by_group.values():
        for index, run in enumerate(group_runs[:-1]):
            older_run = group_runs[index + 1]
            links[int(run["id"])] = int(older_run["id"])
    return links
