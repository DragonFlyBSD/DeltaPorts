"""Byte-parity test for build_triage_payload across the Phase-4 cutover.

Phase 4 Step 2. The test builds a synthetic bundle directory + job
dict, calls ``build_triage_payload``, and asserts the output matches
a hand-derived expected string that encodes the legacy
``parts.append(...)`` semantics. After the refactor (sections +
ContextAssembler) lands in the same commit, this test pins
byte-equivalence so no LLM prompt drifts on us.

Five fixture variants:
- minimal: only build errors + Makefile present.
- full:  every optional section present (KEDB, user_context,
         meta, errors, makefile, plist, distinfo, existing patches).
- with_siblings: same as minimal + a sibling bundle.
- with_prior_triages: same as minimal + 2 historical bundles.
- snippet_round: a follow-up round (snippet_round=1, has_snippets=true)
                 with snippet feedback + content.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dportsv3.agent import runner


# --- helpers ------------------------------------------------------------------


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def _minimal_bundle(tmp_path: Path) -> Path:
    bdir = tmp_path / "bundle-min"
    _write(bdir / "logs" / "errors.txt", "synthetic compile error\n")
    _write(bdir / "port" / "Makefile",
           "PORTNAME=foo\nPORTVERSION=1.0\n")
    return bdir


def _full_bundle(tmp_path: Path) -> Path:
    bdir = tmp_path / "bundle-full"
    _write(bdir / "meta.txt", "origin=foo/bar\nrun_id=run-1\n")
    _write(bdir / "logs" / "errors.txt", "boom: synthetic\n")
    _write(bdir / "port" / "Makefile", "PORTNAME=foo\n")
    _write(bdir / "port" / "pkg-plist", "bin/foo\nshare/foo/data\n")
    _write(bdir / "port" / "distinfo",
           "SHA256 (foo-1.0.tar.gz) = abc\nSIZE (foo-1.0.tar.gz) = 123\n")
    return bdir


# --- fixtures ----------------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_artifact_store(monkeypatch):
    """Make the artifact-store / tracker fetch functions no-ops so
    the legacy build_*_payload only reads from bundle_dir, which is
    what the parity tests provide."""
    monkeypatch.setattr(runner, "artifact_store_get", lambda *a, **kw: None)
    monkeypatch.setattr(runner, "tracker_artifact_get", lambda *a, **kw: None)
    monkeypatch.setattr(runner, "bundle_artifact_list", lambda *a, **kw: [])
    monkeypatch.setattr(runner, "port_bundle_history", lambda *a, **kw: [])
    monkeypatch.setattr(runner, "get_user_context", lambda *a, **kw: (None, 0))
    # build_*_payload now goes through dportsv3.agent.playbooks.load_playbooks;
    # passing playbooks_dir=None at the call site short-circuits it to an
    # empty result, so no monkeypatch is needed here (load_kedb retired).


# --- minimal -----------------------------------------------------------------


def test_minimal_bundle_parity(tmp_path):
    bdir = _minimal_bundle(tmp_path)
    job = {
        "origin": "devel/foo",
        "snippet_round": "0",
        "has_snippets": "false",
    }

    expected = (
        "## Build Errors\n"
        "synthetic compile error\n\n"
        "\n"  # ← gap because the trailing-empty-string emit produces ## Port Files\n on a fresh line
        "## Port Files\n"
        "### Makefile\n"
        "```makefile\n"
        "PORTNAME=foo\nPORTVERSION=1.0\n\n"
        "```\n\n"
        "---\n"
        "Analyze this build failure and provide your triage report."
    )

    actual = runner.build_triage_payload(bdir, None, job)
    assert actual == expected, (
        "byte-parity mismatch for minimal bundle.\n"
        f"--- expected ({len(expected)} bytes) ---\n{expected!r}\n"
        f"--- actual   ({len(actual)} bytes) ---\n{actual!r}"
    )


def test_full_bundle_parity(tmp_path):
    bdir = _full_bundle(tmp_path)
    job = {
        "origin": "devel/foo",
        "snippet_round": "0",
        "has_snippets": "false",
    }
    actual = runner.build_triage_payload(bdir, None, job)

    # Verify spot-checks rather than enumerating every byte; the
    # minimal test already pins per-line structure.
    assert "## Metadata" in actual
    assert "origin=foo/bar" in actual
    assert "## Build Errors" in actual
    assert "boom: synthetic" in actual
    assert "## Port Files" in actual
    assert "### Makefile" in actual
    assert "```makefile" in actual
    assert "### pkg-plist" in actual
    assert "bin/foo" in actual
    assert "### distinfo" in actual
    assert "SHA256 (foo-1.0.tar.gz) = abc" in actual
    assert actual.endswith("---\nAnalyze this build failure and provide your triage report.")


def test_no_bundle_dir_still_produces_footer(tmp_path):
    """bundle_dir=None + bundle_id=None + nothing else: payload is
    just the port-files header + footer."""
    job = {"snippet_round": "0", "has_snippets": "false"}
    actual = runner.build_triage_payload(None, None, job)
    assert actual.endswith("---\nAnalyze this build failure and provide your triage report.")
    assert "## Port Files" in actual


# --- sibling bundles ---------------------------------------------------------


def test_with_siblings(tmp_path, monkeypatch):
    bdir = _minimal_bundle(tmp_path)
    sib_errors = "sibling boom 1\n"

    def fake_read(bd, bid, relpath):
        # Real read for the primary bundle's files (delegate to disk).
        if bd is not None:
            p = bd / relpath
            return p.read_text() if p.exists() else None
        # Sibling fetches: synthetic error text per bundle_id.
        if bid == "sib-a":
            return sib_errors
        return None

    monkeypatch.setattr(runner, "read_bundle_text", fake_read)

    job = {
        "origin": "devel/foo",
        "snippet_round": "0",
        "has_snippets": "false",
        "sibling_bundle_ids": "sib-a",
    }
    actual = runner.build_triage_payload(bdir, None, job)
    assert "## Sibling Pending Failures (this batch)" in actual
    assert "### Bundle sib-a" in actual
    assert sib_errors.rstrip() in actual


# --- prior triages -----------------------------------------------------------


def test_with_prior_triages(tmp_path, monkeypatch):
    bdir = _minimal_bundle(tmp_path)

    def fake_history(origin):
        return [{"bundle_id": "old-a"}, {"bundle_id": "old-b"}]

    def fake_read(bd, bid, relpath):
        if bd is not None:
            p = bd / relpath
            return p.read_text() if p.exists() else None
        if bid == "old-a" and relpath == "analysis/triage.md":
            return "## Classification\nplist-error\n"
        if bid == "old-a" and relpath == "analysis/rebuild_proof.json":
            return '{"rebuild_ok": false}\n'
        return None

    monkeypatch.setattr(runner, "port_bundle_history", fake_history)
    monkeypatch.setattr(runner, "read_bundle_text", fake_read)

    job = {
        "origin": "devel/foo",
        "snippet_round": "0",
        "has_snippets": "false",
    }
    actual = runner.build_triage_payload(bdir, None, job)
    assert "## Prior Triages (most recent 2)" in actual
    assert "### Bundle old-a" in actual
    assert "#### Triage" in actual
    assert "#### Rebuild Proof" in actual
    assert "```json" in actual


def test_prior_triages_includes_patch_evidence(tmp_path, monkeypatch):
    """Step 29d: prior bundles' patch.md + changes.diff land in the
    triage payload so the triage model can see what the patch
    agent already tried. Without this, operator context like
    "i don't see you tried X" makes no sense to the model — it
    has no record of what the patch agent did."""
    bdir = _minimal_bundle(tmp_path)

    def fake_history(origin):
        return [{"bundle_id": "old-a"}]

    patch_md = "# Patch Report\n\nTried `mk set X` — failed.\n"
    diff = (
        "diff --git a/ports/x/y/Makefile b/ports/x/y/Makefile\n"
        "--- a/ports/x/y/Makefile\n"
        "+++ b/ports/x/y/Makefile\n"
        "@@ -1,1 +1,1 @@\n"
        "-OLD\n"
        "+NEW\n"
    )

    def fake_read(bd, bid, relpath):
        if bd is not None:
            p = bd / relpath
            return p.read_text() if p.exists() else None
        if bid == "old-a" and relpath == "analysis/triage.md":
            return "## Classification\nmissing-dep\n"
        if bid == "old-a" and relpath == "analysis/patch.md":
            return patch_md
        if bid == "old-a" and relpath == "analysis/changes.diff":
            return diff
        return None

    monkeypatch.setattr(runner, "port_bundle_history", fake_history)
    monkeypatch.setattr(runner, "read_bundle_text", fake_read)

    actual = runner.build_triage_payload(
        bdir, None,
        {"origin": "devel/foo", "snippet_round": "0",
         "has_snippets": "false"},
    )
    assert "#### Patch Report" in actual
    assert "Tried `mk set X` — failed." in actual
    assert "#### Changes Diff" in actual
    assert "```diff" in actual
    assert "+NEW" in actual


def test_prior_triages_truncates_long_patch_evidence(tmp_path, monkeypatch):
    """Caps on patch.md (2000) and changes.diff (3000) keep the
    triage payload bounded — patch logs and diffs can be very
    large and triage budget is leaner than patch's."""
    bdir = _minimal_bundle(tmp_path)

    huge_patch = "x" * 5000
    huge_diff = "diff line\n" * 2000  # ~20000 chars

    def fake_history(origin):
        return [{"bundle_id": "old-a"}]

    def fake_read(bd, bid, relpath):
        if bd is not None:
            p = bd / relpath
            return p.read_text() if p.exists() else None
        if bid == "old-a" and relpath == "analysis/triage.md":
            return "## Classification\nmissing-dep\n"
        if bid == "old-a" and relpath == "analysis/patch.md":
            return huge_patch
        if bid == "old-a" and relpath == "analysis/changes.diff":
            return huge_diff
        return None

    monkeypatch.setattr(runner, "port_bundle_history", fake_history)
    monkeypatch.setattr(runner, "read_bundle_text", fake_read)

    actual = runner.build_triage_payload(
        bdir, None,
        {"origin": "devel/foo", "snippet_round": "0",
         "has_snippets": "false"},
    )
    assert "[...truncated to 2000 chars...]" in actual
    assert "[...truncated to 3000 chars...]" in actual


# --- snippet round -----------------------------------------------------------


def test_snippet_round_includes_feedback_and_content(tmp_path, monkeypatch):
    bdir = _minimal_bundle(tmp_path)

    monkeypatch.setattr(
        runner, "build_snippet_feedback",
        lambda bundle_dir, round_num: "## Snippet Feedback\n(synthetic)",
    )
    monkeypatch.setattr(
        runner, "load_snippets_content",
        lambda bundle_dir, round_num: "## Extracted Snippets\n(synthetic)",
    )

    job = {
        "origin": "devel/foo",
        "snippet_round": "1",
        "has_snippets": "true",
    }
    actual = runner.build_triage_payload(bdir, None, job)
    assert "## Snippet Feedback" in actual
    assert "(synthetic)" in actual
    assert "## Extracted Snippets" in actual
    # Snippet sections come BEFORE Port Files in the legacy ordering.
    assert actual.index("## Snippet Feedback") < actual.index("## Port Files")
