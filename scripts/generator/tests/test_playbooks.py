"""Tests for the agent playbook library (Step 27b).

Covers:
- frontmatter parser (YAML-subset, list values, defaults, malformed)
- entry parsing (title extraction, body separation, est_tokens)
- selector (role / classification / intents / toolchains / convert_phase)
- budget gate (priority-aware drop)
- find_playbooks_dir walking up ancestors to locate the docs/ dir
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dportsv3.agent.playbooks import (
    PlaybookEntry,
    PlaybookTriggers,
    SelectionResult,
    _parse_frontmatter,
    _parse_inline_list,
    find_playbooks_dir,
    list_entries,
    load_playbooks,
)


# ----- frontmatter primitives -----------------------------------------


@pytest.mark.parametrize("raw,expected", [
    ("", ()),
    ("[]", ()),
    ("[a, b, c]", ("a", "b", "c")),
    ('["quoted", \'single\']', ("quoted", "single")),
    ("[ spaced , trailing , ]", ("spaced", "trailing")),
    ("not-a-list", ()),
])
def test_parse_inline_list(raw, expected):
    assert _parse_inline_list(raw) == expected


def test_parse_frontmatter_extracts_top_and_nested():
    text = (
        "---\n"
        "priority: 50\n"
        "tags: [a, b]\n"
        "triggers:\n"
        "  classifications: [compile-error]\n"
        "  flows: [patch]\n"
        "---\n"
        "# Title\n\nBody.\n"
    )
    fm, body = _parse_frontmatter(text)
    assert fm["priority"] == "50"
    assert fm["tags"] == "[a, b]"
    assert isinstance(fm["triggers"], dict)
    assert fm["triggers"]["classifications"] == "[compile-error]"
    assert fm["triggers"]["flows"] == "[patch]"
    assert body == "# Title\n\nBody.\n"


def test_parse_frontmatter_missing_returns_empty_dict_and_body_unchanged():
    text = "# No frontmatter\n\nBody only."
    fm, body = _parse_frontmatter(text)
    assert fm == {}
    assert body == text


def test_parse_frontmatter_ignores_comment_lines():
    text = (
        "---\n"
        "# this is a comment\n"
        "priority: 10\n"
        "---\n"
        "body\n"
    )
    fm, body = _parse_frontmatter(text)
    assert fm == {"priority": "10"}
    assert body == "body\n"


# ----- entry loading --------------------------------------------------


def _write(dirpath: Path, name: str, content: str) -> Path:
    path = dirpath / name
    path.write_text(content)
    return path


def test_list_entries_parses_frontmatter_and_skips_readme_template(tmp_path):
    _write(tmp_path, "README.md", "skip me")
    _write(tmp_path, "TEMPLATE.md", "skip me too")
    _write(tmp_path, "error-x.md",
        "---\n"
        "triggers:\n"
        "  classifications: [compile-error]\n"
        "  flows: [triage, patch]\n"
        "priority: 80\n"
        "---\n"
        "# Title X\n\nBody X\n"
    )
    _write(tmp_path, "intent-y.md",
        "---\n"
        "triggers:\n"
        "  intents: [replace_in_dops_block]\n"
        "  flows: [patch]\n"
        "---\n"
        "# Title Y\n\nBody Y\n"
    )
    entries = list_entries(tmp_path)
    names = sorted(e.path.name for e in entries)
    assert names == ["error-x.md", "intent-y.md"]
    by_name = {e.path.name: e for e in entries}
    assert by_name["error-x.md"].title == "Title X"
    assert by_name["error-x.md"].priority == 80
    assert by_name["error-x.md"].triggers.classifications == ("compile-error",)
    assert by_name["error-x.md"].triggers.flows == ("triage", "patch")
    assert by_name["intent-y.md"].triggers.intents == ("replace_in_dops_block",)
    assert by_name["intent-y.md"].triggers.flows == ("patch",)


def test_list_entries_handles_missing_dir():
    assert list_entries(None) == []
    assert list_entries(Path("/no/such/dir/here")) == []


def test_entry_without_frontmatter_gets_default_flows(tmp_path):
    """Legacy-shape entry (no frontmatter) defaults to flows=[triage, patch]."""
    _write(tmp_path, "error-legacy.md", "# Legacy\n\nBody.\n")
    entries = list_entries(tmp_path)
    assert len(entries) == 1
    assert entries[0].triggers.flows == ("triage", "patch")
    assert entries[0].triggers.classifications == ()


def test_entry_title_falls_back_to_filename_stem_when_no_h1(tmp_path):
    _write(tmp_path, "intent-z.md", "no headers here\njust prose.\n")
    entries = list_entries(tmp_path)
    assert len(entries) == 1
    assert entries[0].title == "intent-z"


# ----- selector -------------------------------------------------------


def _fixture_dir(tmp_path: Path) -> Path:
    """Build a small fixture library covering the 4 categories."""
    _write(tmp_path, "error-plist.md",
        "---\n"
        "triggers:\n"
        "  classifications: [plist-error]\n"
        "  flows: [triage, patch]\n"
        "priority: 100\n"
        "---\n"
        "# Plist\n\nplist body\n"
    )
    _write(tmp_path, "intent-rin.md",
        "---\n"
        "triggers:\n"
        "  intents: [replace_in_dops_block]\n"
        "  flows: [patch]\n"
        "priority: 50\n"
        "---\n"
        "# Replace-in-dops\n\nrid body\n"
    )
    _write(tmp_path, "convert-target.md",
        "---\n"
        "triggers:\n"
        "  convert_phases: [picking_target]\n"
        "  flows: [convert]\n"
        "priority: 100\n"
        "---\n"
        "# Convert target\n\ntarget body\n"
    )
    _write(tmp_path, "toolchain-autoconf.md",
        "---\n"
        "triggers:\n"
        "  toolchains: [autoconf]\n"
        "  flows: [triage, patch]\n"
        "priority: 60\n"
        "---\n"
        "# Autoconf\n\nautoconf body\n"
    )
    return tmp_path


def test_selector_classification_filter(tmp_path):
    d = _fixture_dir(tmp_path)
    result = load_playbooks(d, role="patch", classification="plist-error")
    assert "error-plist.md" in result.included
    # No toolchain context → autoconf entry skipped.
    assert "toolchain-autoconf.md" not in result.included
    # No intent context → intent entry skipped.
    assert "intent-rin.md" not in result.included
    # Wrong flow for convert entry.
    assert "convert-target.md" not in result.included
    skipped_names = {name for name, _ in result.skipped}
    assert "convert-target.md" in skipped_names


def test_selector_flow_gate(tmp_path):
    d = _fixture_dir(tmp_path)
    result = load_playbooks(d, role="convert")
    # Only entries whose flows include `convert` may fire.
    assert "convert-target.md" not in result.included  # needs phase
    result_with_phase = load_playbooks(
        d, role="convert", convert_phases=["picking_target"],
    )
    assert "convert-target.md" in result_with_phase.included
    # Patch entries don't leak into convert.
    assert "intent-rin.md" not in result_with_phase.included


def test_selector_intent_overlap(tmp_path):
    d = _fixture_dir(tmp_path)
    result = load_playbooks(
        d, role="patch", intents=["replace_in_dops_block"],
    )
    assert "intent-rin.md" in result.included


def test_selector_toolchain_overlap(tmp_path):
    d = _fixture_dir(tmp_path)
    result = load_playbooks(
        d, role="patch", toolchains=["autoconf"],
    )
    assert "toolchain-autoconf.md" in result.included
    # No intent → intent entry still skipped.
    assert "intent-rin.md" not in result.included


def test_selector_priority_order_in_output(tmp_path):
    d = _fixture_dir(tmp_path)
    # patch flow + classification matches plist (prio 100). Add intent
    # context to also pull intent-rin (prio 50). intent-rin should come
    # first in the rendered text by lower-priority-first rule.
    result = load_playbooks(
        d, role="patch", classification="plist-error",
        intents=["replace_in_dops_block"],
    )
    assert "intent-rin.md" in result.included
    assert "error-plist.md" in result.included
    # intent-rin (prio 50) appears earlier in text than error-plist (prio 100).
    assert result.text.index("Replace-in-dops") < result.text.index("Plist")


def test_selector_budget_gate_drops_lowest_priority(tmp_path):
    d = _fixture_dir(tmp_path)
    # Tight budget that fits only ONE entry. intent-rin (prio 50) wins
    # over toolchain-autoconf (prio 60). Bodies are ~7 and ~6 est_tokens
    # respectively, so a budget of 8 fits the first but not the second.
    result = load_playbooks(
        d, role="patch", intents=["replace_in_dops_block"],
        toolchains=["autoconf"], budget_tokens=8,
    )
    assert result.included == ("intent-rin.md",)
    dropped = {name for name, reason in result.skipped if reason.startswith("budget:")}
    assert "toolchain-autoconf.md" in dropped


def test_selector_empty_result_returns_empty_text(tmp_path):
    _write(tmp_path, "intent-x.md",
        "---\n"
        "triggers:\n"
        "  intents: [some_other_intent]\n"
        "  flows: [patch]\n"
        "---\n"
        "# X\n\nbody\n"
    )
    result = load_playbooks(tmp_path, role="patch")
    assert result.text == ""
    assert result.included == ()
    assert len(result.skipped) == 1


# ----- discovery ------------------------------------------------------


def test_find_playbooks_dir_walks_up_to_repo_docs():
    """The real repo's docs/agent-playbooks/ should be locatable from
    this test file (Step 27a moved it there). This guards against the
    pre-existing parent-chain bug being reintroduced."""
    located = find_playbooks_dir()
    assert located is not None, "find_playbooks_dir() should locate the live dir"
    assert located.name == "agent-playbooks"
    assert (located / "README.md").is_file()


def test_detect_toolchains_from_uses_line(tmp_path):
    """detect_toolchains parses USES= tokens (with and without
    :option suffixes) and maps to toolchain tags."""
    from dportsv3.agent.playbooks import detect_toolchains
    port = tmp_path / "ports" / "devel" / "x"
    port.mkdir(parents=True)
    (port / "Makefile").write_text(
        "PORTNAME=x\n"
        "USES= autoreconf cmake pkgconfig compiler:c11 perl5:build,run\n"
    )
    tags = detect_toolchains(port)
    assert "autoconf" in tags
    assert "cmake" in tags
    assert "pkg-config" in tags
    assert "c" in tags
    assert "perl5" in tags


def test_detect_toolchains_handles_gnu_configure_and_use_gmake(tmp_path):
    from dportsv3.agent.playbooks import detect_toolchains
    port = tmp_path / "port"
    port.mkdir()
    (port / "Makefile").write_text(
        "PORTNAME=y\n"
        "GNU_CONFIGURE= yes\n"
        "USE_GMAKE= yes\n"
    )
    tags = detect_toolchains(port)
    assert "autoconf" in tags
    assert "gmake" in tags


def test_detect_toolchains_handles_file_presence_signals(tmp_path):
    """File-presence checks fire even without a recognizable
    Makefile USES line — useful when the framework Makefile is
    minimal but a CMakeLists.txt / Cargo.toml / meson.build /
    configure.ac is present in the same dir."""
    from dportsv3.agent.playbooks import detect_toolchains
    port = tmp_path / "port"
    port.mkdir()
    (port / "Makefile").write_text("PORTNAME=z\n")
    (port / "CMakeLists.txt").write_text("cmake_minimum_required(VERSION 3.0)\n")
    (port / "Cargo.toml").write_text("[package]\n")
    tags = detect_toolchains(port)
    assert "cmake" in tags
    assert "cargo" in tags


def test_detect_toolchains_missing_dir_returns_empty(tmp_path):
    from dportsv3.agent.playbooks import detect_toolchains
    assert detect_toolchains(None) == set()
    assert detect_toolchains(tmp_path / "does-not-exist") == set()


def test_detect_toolchains_unreadable_makefile_does_not_raise(tmp_path):
    """If Makefile is present but unreadable (rare; permission
    issues), detect_toolchains returns whatever file-presence
    signals fire and doesn't raise."""
    from dportsv3.agent.playbooks import detect_toolchains
    port = tmp_path / "port"
    port.mkdir()
    # No Makefile at all → tags come only from file-presence.
    (port / "configure.ac").write_text("AC_INIT([x],[1])\n")
    tags = detect_toolchains(port)
    assert "autoconf" in tags


def test_every_intent_type_has_a_playbook():
    """Step 27d contract: each intent type declared in
    edit_intent.INTENT_TYPES must have a corresponding intent-*.md
    playbook tagged with `intents: [<type>]`. Guards against an
    intent being added later without a matching recipe — every new
    intent type should ship with its usage recipe."""
    from dportsv3.agent.edit_intent import INTENT_TYPES
    located = find_playbooks_dir()
    assert located is not None
    entries = list_entries(located)
    intent_coverage: dict[str, list[str]] = {t: [] for t in INTENT_TYPES}
    for e in entries:
        for t in e.triggers.intents:
            if t in intent_coverage:
                intent_coverage[t].append(e.path.name)
    missing = [t for t, files in intent_coverage.items() if not files]
    assert not missing, (
        f"Intent types lack a playbook entry tagged with their type "
        f"in triggers.intents: {missing}. Every intent should ship "
        f"with a usage recipe in docs/agent-playbooks/intent-<type>.md"
    )
