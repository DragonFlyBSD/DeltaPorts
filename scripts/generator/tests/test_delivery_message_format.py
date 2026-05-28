"""PR title/body + diffstat formatting and configurable committer
identity for the delivery orchestrator.

Covers:
- ``format_commit_message`` produces a sectioned markdown body with
  Summary / What changed / Verification / Provenance, and a clean
  title free of internal target jargon.
- ``_diffstat`` summarizes a unified diff.
- The config loader defaults the committer identity to
  "Fred [bot]" / "github@dragonflybsd.org" and honors overrides.
"""

from __future__ import annotations

from pathlib import Path

from dportsv3.delivery.config import load_delivery_config
from dportsv3.delivery.orchestrator import _diffstat, format_commit_message


_SAMPLE_DIFF = """\
diff --git a/devel/foo/Makefile b/devel/foo/Makefile
index 111..222 100644
--- a/devel/foo/Makefile
+++ b/devel/foo/Makefile
@@ -1,3 +1,4 @@
 PORTNAME=foo
+USES=gmake
-OLD=line
diff --git a/devel/foo/files/patch-x b/devel/foo/files/patch-x
new file mode 100644
--- /dev/null
+++ b/devel/foo/files/patch-x
@@ -0,0 +1,2 @@
+hunk one
+hunk two
"""


def test_diffstat_counts_files_and_lines():
    stat = _diffstat(_SAMPLE_DIFF)
    assert "2 files changed" in stat
    # +USES, +hunk one, +hunk two = 3 insertions; -OLD = 1 deletion.
    assert "+3/-1" in stat
    assert "`devel/foo/Makefile`" in stat
    assert "`devel/foo/files/patch-x`" in stat


def test_diffstat_empty_diff_returns_blank():
    assert _diffstat("") == ""


def test_commit_message_title_has_no_internal_target_jargon():
    title, _ = format_commit_message(
        origin="devel/foo", target="@2026Q2", bundle_id="b-1",
        bundle_url=None, one_line_summary=None, operator="alice",
        model=None, attempts=None, tokens=None, verified_at=None,
    )
    assert title == "devel/foo: fix build failure on DragonFly"
    assert "@2026Q2" not in title


def test_commit_message_body_is_sectioned_markdown():
    _, body = format_commit_message(
        origin="devel/foo", target="@2026Q2", bundle_id="b-1",
        bundle_url="http://tracker/bundles/b-1",
        one_line_summary="Add USES=gmake to fix the build.",
        operator="alice", model="opus", attempts=2, tokens=12345,
        verified_at="2026-05-28T10:00:00Z",
        diff_text=_SAMPLE_DIFF,
    )
    assert "## Summary\n\nAdd USES=gmake to fix the build." in body
    assert "## What changed" in body
    assert "2 files changed, +3/-1" in body
    assert "## Verification" in body
    assert "for target `@2026Q2`." in body
    assert "Verified 2026-05-28T10:00:00Z." in body
    assert "## Provenance" in body
    assert "- Operator: alice" in body
    assert "- Agent: model=opus attempts=2 tokens=12345" in body
    assert "- Bundle: http://tracker/bundles/b-1" in body


def test_commit_message_synthesizes_summary_when_absent():
    _, body = format_commit_message(
        origin="devel/foo", target="@2026Q2", bundle_id="b-1",
        bundle_url=None, one_line_summary=None, operator="alice",
        model=None, attempts=None, tokens=None, verified_at=None,
    )
    assert "## Summary" in body
    assert "`devel/foo`" in body
    # No diff → no "What changed" section.
    assert "## What changed" not in body


def _write_toml(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "delivery.toml"
    p.write_text(text)
    return p


def test_config_defaults_committer_identity(tmp_path):
    cfg = load_delivery_config(
        _write_toml(tmp_path, """
[provider]
type = "github"
repo = "DragonFlyBSD/DeltaPorts"
clone_dir = "/tmp/clone"
"""),
        env={"DPORTSV3_DELIVERY_TOKEN": "tok"},
    )
    assert cfg.committer_name == "Fred [bot]"
    assert cfg.committer_email == "github@dragonflybsd.org"


def test_config_committer_identity_override(tmp_path):
    cfg = load_delivery_config(
        _write_toml(tmp_path, """
[provider]
type = "github"
repo = "DragonFlyBSD/DeltaPorts"
clone_dir = "/tmp/clone"
committer_name = "Custom Bot"
committer_email = "custom@example.org"
"""),
        env={"DPORTSV3_DELIVERY_TOKEN": "tok"},
    )
    assert cfg.committer_name == "Custom Bot"
    assert cfg.committer_email == "custom@example.org"
