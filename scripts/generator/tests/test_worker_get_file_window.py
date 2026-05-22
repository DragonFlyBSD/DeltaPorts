"""Layer 1 of the cost fix: line-windowed ``get_file`` + grep context.

Smoke surfaced an entire 1.55M-token budget burned by the patch
agent re-sending a 290KB Makefile.in in every turn's conversation
history. The agent's natural ``get_file path`` returned the whole
file, then every subsequent tool result kept compounding.

Fixes pinned here:
- ``get_file`` returns at most ``limit_lines`` lines (default 200)
  starting at ``offset_lines`` (default 0). For truncated reads,
  the response includes ``total_lines`` + a hint on how to resume.
- ``sha256`` stays over the FULL file regardless of window —
  ``put_file(expected_sha256=...)`` still works across windows.
- ``grep`` returns ``context`` surrounding lines per match (default
  3) via ``grep -C``, so the agent rarely needs to fall back to
  ``get_file`` after grep.
- Prompt directs ``grep``-first investigation.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


_GEN = Path(__file__).resolve().parents[1]
if str(_GEN) not in sys.path:
    sys.path.insert(0, str(_GEN))


@pytest.fixture
def env_dir(tmp_path, monkeypatch):
    """Minimal env-paths so the worker's tools can resolve in-chroot
    paths to host paths under tmp_path."""
    from dportsv3.agent import worker

    writable = tmp_path / "writable"
    (writable / "work").mkdir(parents=True)
    fake = worker.EnvPaths(env_dir=tmp_path, writable=writable)
    monkeypatch.setattr(worker, "env_paths", lambda env: fake)
    return writable


def _write(env_dir: Path, rel: str, content: str) -> Path:
    target = env_dir / "work" / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    return target


# --- get_file window ---------------------------------------------------------


def test_get_file_default_returns_first_200_lines(env_dir):
    """A file with 1000 lines returns lines 1–200 by default with a
    truncation marker pointing the agent at how to resume."""
    from dportsv3.agent import worker
    body = "".join(f"line {i}\n" for i in range(1, 1001))
    _write(env_dir, "big.txt", body)

    res = worker.get_file("env", "/work/big.txt")
    assert res["encoding"] == "text"
    assert res["total_lines"] == 1000
    assert res["first_line"] == 1
    assert res["last_line"] == 200
    assert res["truncated"] is True
    # Content covers exactly lines 1..200.
    lines = res["content"].splitlines()
    assert len(lines) == 200
    assert lines[0] == "line 1"
    assert lines[-1] == "line 200"
    # Hint points at the next offset.
    assert "offset_lines=200" in res["hint"]
    assert "grep" in res["hint"]


def test_get_file_window_respects_offset_and_limit(env_dir):
    from dportsv3.agent import worker
    body = "".join(f"line {i}\n" for i in range(1, 51))
    _write(env_dir, "mid.txt", body)

    res = worker.get_file("env", "/work/mid.txt",
                          offset_lines=10, limit_lines=5)
    lines = res["content"].splitlines()
    assert len(lines) == 5
    assert lines[0] == "line 11"
    assert lines[-1] == "line 15"
    assert res["first_line"] == 11
    assert res["last_line"] == 15
    assert res["truncated"] is True


def test_get_file_small_file_not_truncated(env_dir):
    from dportsv3.agent import worker
    body = "alpha\nbeta\n"
    _write(env_dir, "small.txt", body)

    res = worker.get_file("env", "/work/small.txt")
    assert res["truncated"] is False
    assert "hint" not in res
    assert res["content"] == "alpha\nbeta\n"
    assert res["total_lines"] == 2


def test_get_file_offset_past_end_returns_empty(env_dir):
    from dportsv3.agent import worker
    body = "a\nb\nc\n"
    _write(env_dir, "tiny.txt", body)

    res = worker.get_file("env", "/work/tiny.txt", offset_lines=100)
    assert res["content"] == ""
    # Empty window → first_line=0 signals "no content returned" rather
    # than a confusing 1-past-last index.
    assert res["first_line"] == 0
    assert res["last_line"] == 3
    assert res["truncated"] is False
    assert res["total_lines"] == 3


def test_get_file_sha256_is_full_file_not_window(env_dir):
    """The sha must hash the full file's bytes regardless of window,
    so put_file(expected_sha256=...) works after any line-range read."""
    from dportsv3.agent import worker
    body = "".join(f"line {i}\n" for i in range(1, 1001))
    target = _write(env_dir, "h.txt", body)

    full = worker.get_file("env", "/work/h.txt", limit_lines=1000)
    partial = worker.get_file("env", "/work/h.txt", limit_lines=10)
    assert full["sha256"] == partial["sha256"]

    # Reality check: that hash matches the on-disk file's bytes.
    import hashlib
    expected = hashlib.sha256(target.read_bytes()).hexdigest()
    assert full["sha256"] == expected


def test_get_file_degenerate_limits_use_default(env_dir):
    from dportsv3.agent import worker
    body = "".join(f"L{i}\n" for i in range(1, 501))
    _write(env_dir, "d.txt", body)

    res = worker.get_file("env", "/work/d.txt", limit_lines=0)
    assert res["last_line"] == 200      # default kicked in
    res = worker.get_file("env", "/work/d.txt", limit_lines=-5)
    assert res["last_line"] == 200


def test_get_file_binary_file_capped_at_32k(env_dir):
    from dportsv3.agent import worker
    raw = b"\x00\x01\x02\x03" * 20_000   # 80KB, > 32KB cap
    target = env_dir / "work" / "blob.bin"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(raw)

    res = worker.get_file("env", "/work/blob.bin")
    assert res["encoding"] == "base64"
    assert res["truncated"] is True
    assert res["size"] == 80_000
    # base64 of 32KB is ~43KB, but content length matches the capped chunk.
    import base64
    assert len(base64.b64decode(res["content"])) == 32_768
    assert "grep" in res["hint"]


# --- grep context -----------------------------------------------------------


def test_grep_returns_context_lines_by_default(env_dir):
    from dportsv3.agent import worker
    body = "\n".join(f"line {i}" for i in range(1, 21)) + "\n"
    body = body.replace("line 10", "TARGET_LINE")
    _write(env_dir, "g.txt", body)

    res = worker.grep("env", "TARGET_LINE", "/work/g.txt")
    assert res["ok"] is True
    matches = res["matches"]
    # The target line is present.
    assert "TARGET_LINE" in matches
    # Default context=3 means we see lines 7..13 around the match.
    assert "line 7" in matches
    assert "line 13" in matches
    # But not the rest of the file.
    assert "line 1\n" not in matches
    assert "line 20" not in matches


def test_grep_context_zero_suppresses_surrounding_lines(env_dir):
    from dportsv3.agent import worker
    body = "\n".join(f"line {i}" for i in range(1, 11)) + "\n"
    body = body.replace("line 5", "HIT")
    _write(env_dir, "g0.txt", body)

    res = worker.grep("env", "HIT", "/work/g0.txt", context=0)
    matches = res["matches"]
    assert "HIT" in matches
    # No context lines around it.
    assert "line 4" not in matches
    assert "line 6" not in matches


# --- tool registry + prompt -------------------------------------------------


def test_get_file_schema_advertises_offset_limit():
    from dportsv3.agent.tools import schemas
    spec = next(s for s in schemas()
                if s["function"]["name"] == "get_file")
    props = spec["function"]["parameters"]["properties"]
    assert "offset_lines" in props
    assert "limit_lines" in props
    desc = spec["function"]["description"]
    assert "Prefer grep" in desc or "prefer grep" in desc.lower()


def test_grep_schema_advertises_context():
    from dportsv3.agent.tools import schemas
    spec = next(s for s in schemas()
                if s["function"]["name"] == "grep")
    props = spec["function"]["parameters"]["properties"]
    assert "context" in props
    desc = spec["function"]["description"]
    assert "default" in desc.lower()
    assert "investigating" in desc.lower() or "large" in desc.lower()


def test_prompt_documents_search_before_read_discipline():
    from dportsv3.agent.prompts import PATCH_SYSTEM
    # Heading present.
    assert "SEARCH BEFORE READ" in PATCH_SYSTEM
    # Concrete cost narrative present (this is the rationale the
    # model needs to internalize).
    assert "compound" in PATCH_SYSTEM.lower() or "history" in PATCH_SYSTEM.lower()
    # The decision table mentions grep as the default.
    assert "grep" in PATCH_SYSTEM
    # The bad pattern is named.
    assert "Dump the whole" in PATCH_SYSTEM or "NO" in PATCH_SYSTEM
