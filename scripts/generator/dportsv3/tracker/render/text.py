"""Text renderers for tracker artifacts: a conservative Markdown
subset and a unified-diff -> HTML renderer. Stdlib-only, pure."""

from __future__ import annotations

import html
import re
from pathlib import Path



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



def render_markdown(text: str) -> str:
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



def render_diff(raw: str) -> str:
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
