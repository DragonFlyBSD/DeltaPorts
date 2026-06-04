"""Step 38a + 38b — target-scope plumbing through the intent layer.

The dops engine has supported per-target scoping end-to-end since the
grammar landed, but the intent layer ignored the dimension entirely —
the Translator constructor took no target, no schema carried scope,
every renderer appended at EOF under whatever the file's last
`target @X` directive was (in practice always `target @any`).

Step 38a (covered below): the Translator gains an optional `target`
kwarg, `worker` keeps a per-env target cache the runner populates at
attempt start (`runner.process_patch_job` / `process_convert_job`),
and `worker.apply_intent` threads the cached value into the
Translator at construction time.

Step 38b (covered below): `_ensure_target_scope(overlay_text, scope,
statements)` placement helper. Pure function — takes overlay text +
resolved scope + statements, returns new overlay text with the
statements placed under the matching `target <scope>` section
(creating a new section at EOF if no match). Renderers will consume
the helper from 38d onward; this file pins the helper's behavior
standalone so the renderer integration can build on it without
re-litigating placement rules.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from dportsv3.agent import worker
from dportsv3.agent.edit_intent._dops import (
    _check_target_scope_order,
    _ensure_target_scope,
)
from dportsv3.agent.edit_intent.translator import Translator


# ---------------------------------------------------------------------
# Translator constructor — target kwarg
# ---------------------------------------------------------------------


def test_translator_target_defaults_to_none(tmp_path: Path) -> None:
    """Backward compatibility: existing callers that don't pass
    `target` get `None`, matching pre-38a behavior."""
    t = Translator(tmp_path, "devel/foo", "dops")
    assert t.target is None


def test_translator_target_kwarg_is_stored(tmp_path: Path) -> None:
    """Renderers in 38b will read `t.target` to resolve `@current`."""
    t = Translator(tmp_path, "devel/foo", "dops", target="@2026Q2")
    assert t.target == "@2026Q2"


def test_translator_target_independent_of_wrksrc(tmp_path: Path) -> None:
    """Two optional kwargs, each routed independently. Belt-and-braces
    against a copy/paste-style bug where a future helper conflates
    target with wrksrc."""
    t = Translator(
        tmp_path, "devel/foo", "dops",
        wrksrc="/work/obj/devel/foo/1.0",
        target="@2026Q3",
    )
    assert t.wrksrc == "/work/obj/devel/foo/1.0"
    assert t.target == "@2026Q3"


# ---------------------------------------------------------------------
# worker._TARGET_CACHE — setter / peeker
# ---------------------------------------------------------------------


def test_peek_env_target_miss_returns_none() -> None:
    """Cache miss is the @any fallback signal at the worker boundary."""
    worker._TARGET_CACHE.clear()
    assert worker.peek_env_target("env-not-seen") is None


def test_set_env_target_round_trips() -> None:
    worker._TARGET_CACHE.clear()
    try:
        worker.set_env_target("env-a", "@2026Q2")
        assert worker.peek_env_target("env-a") == "@2026Q2"
    finally:
        worker._TARGET_CACHE.clear()


def test_set_env_target_overwrites() -> None:
    """A re-invocation by the runner (e.g. between attempts) replaces
    the prior cached value rather than accumulating."""
    worker._TARGET_CACHE.clear()
    try:
        worker.set_env_target("env-a", "@2026Q2")
        worker.set_env_target("env-a", "@2026Q3")
        assert worker.peek_env_target("env-a") == "@2026Q3"
    finally:
        worker._TARGET_CACHE.clear()


def test_set_env_target_accepts_none() -> None:
    """A job with no `target` field (rare but possible — e.g. legacy
    queued jobs) yields target=None at the cache layer, matching the
    @any fallback when no scope is resolvable."""
    worker._TARGET_CACHE.clear()
    try:
        worker.set_env_target("env-a", None)
        assert worker.peek_env_target("env-a") is None
    finally:
        worker._TARGET_CACHE.clear()


def test_target_cache_is_env_scoped_not_origin_scoped() -> None:
    """Compose target is an env-level property (the dev-env is pinned
    to a build line), not an origin-level one — distinct origins on
    the same env share the same target."""
    worker._TARGET_CACHE.clear()
    try:
        worker.set_env_target("env-a", "@2026Q2")
        # peek_env_target signature is `(env: str)` — no origin arg,
        # documenting the scoping by the API shape itself.
        assert worker.peek_env_target("env-a") == "@2026Q2"
    finally:
        worker._TARGET_CACHE.clear()


# ---------------------------------------------------------------------
# worker.apply_intent threads cached target → Translator
# ---------------------------------------------------------------------


def test_apply_intent_threads_target_from_cache(tmp_path: Path) -> None:
    """The whole point of the cache: the agent's `apply_intent` tool
    call (which receives no scope from the LLM) must construct a
    Translator that knows the env's target. Without this, Step 38b's
    renderers can't resolve `@current` to anything."""
    worker._TARGET_CACHE.clear()
    worker.set_env_target("env-a", "@2026Q2")

    captured: dict = {}

    class _RecordingTranslator:
        def __init__(self, workspace, origin, mode, **kwargs):
            captured["workspace"] = workspace
            captured["origin"] = origin
            captured["mode"] = mode
            captured["target"] = kwargs.get("target")
            captured["wrksrc"] = kwargs.get("wrksrc")

        def apply(self, intent):
            from dportsv3.agent.edit_intent.translator import EditResult
            return EditResult(
                ok=True, intent_type="bump_portrevision",
                paths_changed=[], substrate_diff="",
            )

    # Wire just enough state for worker.apply_intent to reach the
    # Translator construction path. The four guard layers earlier in
    # the function (substrate_invariant, valid mode, mode-drift) all
    # short-circuit on env state we have to stand up; rather than
    # mock each, we patch the Translator symbol and a single
    # `assess_dops` so the function takes the happy path.
    fake_paths = type("P", (), {"deltaports": tmp_path})()
    (tmp_path / "ports" / "devel" / "foo").mkdir(parents=True)

    try:
        with patch.object(worker, "env_paths", return_value=fake_paths), \
             patch.object(worker, "assess_dops",
                          return_value=type("A", (), {
                              "action": "proceed_triage",
                              "state": "converted",
                          })()), \
             patch("dportsv3.agent.edit_intent.Translator",
                   _RecordingTranslator):
            result = worker.apply_intent(
                "env-a", "devel/foo",
                {"type": "bump_portrevision"},
            )
        assert result["ok"] is True, result
        assert captured["target"] == "@2026Q2", (
            "Translator did not receive the cached env target"
        )
    finally:
        worker._TARGET_CACHE.clear()


def test_apply_intent_falls_back_to_none_when_cache_empty(
    tmp_path: Path,
) -> None:
    """Backward compatibility: if the runner hasn't populated the
    cache (legacy code path, ad-hoc test invocation, missed
    set_env_target call), the Translator gets target=None, matching
    pre-38a behavior."""
    worker._TARGET_CACHE.clear()

    captured: dict = {}

    class _RecordingTranslator:
        def __init__(self, workspace, origin, mode, **kwargs):
            captured["target"] = kwargs.get("target")

        def apply(self, intent):
            from dportsv3.agent.edit_intent.translator import EditResult
            return EditResult(
                ok=True, intent_type="bump_portrevision",
                paths_changed=[], substrate_diff="",
            )

    fake_paths = type("P", (), {"deltaports": tmp_path})()
    (tmp_path / "ports" / "devel" / "foo").mkdir(parents=True)

    with patch.object(worker, "env_paths", return_value=fake_paths), \
         patch.object(worker, "assess_dops",
                      return_value=type("A", (), {
                          "action": "proceed_triage",
                          "state": "converted",
                      })()), \
         patch("dportsv3.agent.edit_intent.Translator",
               _RecordingTranslator):
        worker.apply_intent(
            "env-never-seen", "devel/foo",
            {"type": "bump_portrevision"},
        )

    assert captured["target"] is None


# ---------------------------------------------------------------------
# Step 38b — _ensure_target_scope placement helper
# ---------------------------------------------------------------------


_HEADER = (
    "target @any\n"
    "port devel/foo\n"
    "type port\n"
    'reason "x"\n'
    "\n"
)


def test_helper_appends_to_existing_any_section() -> None:
    """The common case today: overlay has only `@any`. Helper appends
    statements at the tail of the @any section."""
    overlay = _HEADER + 'mk set USES "tar:xz"\n'
    result = _ensure_target_scope(overlay, "@any", ["mk add USES ssl"])
    assert result == (
        _HEADER
        + 'mk set USES "tar:xz"\n'
        + "mk add USES ssl\n"
    )


def test_helper_creates_new_q_section_at_eof() -> None:
    """No @Q section exists; helper appends a fresh `target @Q` block
    at EOF, preceded by a blank-line separator."""
    overlay = _HEADER + 'mk set USES "tar:xz"\n'
    result = _ensure_target_scope(overlay, "@2026Q2", ["mk add CFLAGS -fA"])
    assert result == (
        _HEADER
        + 'mk set USES "tar:xz"\n'
        + "\n"
        + "target @2026Q2\n"
        + "mk add CFLAGS -fA\n"
    )


def test_helper_appends_into_existing_q_section() -> None:
    """When the @Q section already exists, statements append at its
    tail. Blank-line separator stays attached to the next section
    (if any) or EOF."""
    overlay = (
        _HEADER
        + 'mk set USES "tar:xz"\n'
        + "\n"
        + "target @2026Q2\n"
        + "mk add CFLAGS -fA\n"
    )
    result = _ensure_target_scope(overlay, "@2026Q2", ['mk set LDFLAGS "-lfoo"'])
    assert result == (
        _HEADER
        + 'mk set USES "tar:xz"\n'
        + "\n"
        + "target @2026Q2\n"
        + "mk add CFLAGS -fA\n"
        + 'mk set LDFLAGS "-lfoo"\n'
    )


def test_helper_any_insert_preserves_blank_before_next_q() -> None:
    """Inserting into @any with a @Q section after: the blank-line
    separator stays attached to the @Q block, not orphaned at the
    end of the @any insertion."""
    overlay = (
        _HEADER
        + 'mk set USES "tar:xz"\n'
        + "\n"
        + "target @2026Q2\n"
        + "mk add CFLAGS -fA\n"
    )
    result = _ensure_target_scope(overlay, "@any", ["mk add EXTRA -f"])
    assert result == (
        _HEADER
        + 'mk set USES "tar:xz"\n'
        + "mk add EXTRA -f\n"
        + "\n"
        + "target @2026Q2\n"
        + "mk add CFLAGS -fA\n"
    )


def test_helper_match_in_middle_section_keeps_neighbors_intact() -> None:
    """Three sections (@any, @2026Q2, @2026Q3); helper inserts into
    the middle one without disturbing the others."""
    overlay = (
        _HEADER
        + 'mk set USES "tar:xz"\n'
        + "\n"
        + "target @2026Q2\n"
        + "mk add A B\n"
        + "\n"
        + "target @2026Q3\n"
        + "mk add C D\n"
    )
    result = _ensure_target_scope(overlay, "@2026Q2", ['mk set Z "w"'])
    assert result == (
        _HEADER
        + 'mk set USES "tar:xz"\n'
        + "\n"
        + "target @2026Q2\n"
        + "mk add A B\n"
        + 'mk set Z "w"\n'
        + "\n"
        + "target @2026Q3\n"
        + "mk add C D\n"
    )


def test_helper_multiple_statements_in_one_call() -> None:
    """Statements list with N entries lands all of them under the
    matching section in order."""
    overlay = _HEADER + 'mk set USES "tar:xz"\n'
    result = _ensure_target_scope(
        overlay, "@any",
        ["mk add USES ssl", 'mk set LICENSE "BSD2CLAUSE"', "mk add CFLAGS -fA"],
    )
    assert result == (
        _HEADER
        + 'mk set USES "tar:xz"\n'
        + "mk add USES ssl\n"
        + 'mk set LICENSE "BSD2CLAUSE"\n'
        + "mk add CFLAGS -fA\n"
    )


def test_helper_no_match_with_empty_statements_emits_nothing() -> None:
    """Issue A guard: empty statements + no matching section must
    NOT emit a bare `target @X` directive. The overlay should be
    returned essentially unchanged."""
    overlay = _HEADER + 'mk set USES "tar:xz"\n'
    result = _ensure_target_scope(overlay, "@2026Q2", [])
    assert result == overlay


def test_helper_match_with_empty_statements_is_noop() -> None:
    """Empty statements list against a matching section: also a no-op
    (helper appends nothing, returns the overlay unchanged)."""
    overlay = _HEADER + 'mk set USES "tar:xz"\n'
    result = _ensure_target_scope(overlay, "@any", [])
    assert result == overlay


def test_helper_preserves_trailing_newline() -> None:
    """If input ends with newline, output ends with newline. Convention
    matches `_append_overlay`."""
    overlay = _HEADER + 'mk set USES "tar:xz"\n'
    assert overlay.endswith("\n")
    result = _ensure_target_scope(overlay, "@any", ["mk add USES ssl"])
    assert result.endswith("\n")


def test_helper_normalizes_missing_trailing_newline() -> None:
    """Input without trailing newline gets one in the output —
    matches the existing `_append_overlay` normalization, which
    convert + worker both rely on."""
    overlay = _HEADER + 'mk set USES "tar:xz"'  # no trailing nl
    result = _ensure_target_scope(overlay, "@any", ["mk add USES ssl"])
    assert result.endswith("\n")


def test_helper_rstrips_statement_trailing_whitespace() -> None:
    """Caller may pass statements with stray trailing whitespace
    (e.g. `mk add X  ` from sloppy formatting); helper normalizes
    to a single trailing newline per statement."""
    overlay = _HEADER
    result = _ensure_target_scope(overlay, "@any", ["mk add USES ssl   "])
    assert "mk add USES ssl\n" in result
    assert "mk add USES ssl   " not in result


def test_helper_does_not_match_comma_separated_targets() -> None:
    """Documented limitation: a `target @2026Q4,@2026Q1` directive
    in the overlay is NOT matched even if scope is `@2026Q4`. Helper
    treats it as a distinct (unmatched) section and appends a new
    `target @2026Q4` block at EOF.

    The intent flow never emits multi-target directives, so this
    only matters for hand-edited overlays. Pinning the behavior
    here so 38c knows what to address if the gap surfaces."""
    overlay = (
        _HEADER
        + "target @2026Q4,@2026Q1\n"
        + "mk add A B\n"
    )
    result = _ensure_target_scope(overlay, "@2026Q4", ["mk add C D"])
    # New section appended at EOF, comma-separated directive untouched.
    assert "target @2026Q4,@2026Q1\nmk add A B" in result
    assert result.rstrip().endswith("target @2026Q4\nmk add C D")


def test_helper_idempotency_within_section() -> None:
    """Two consecutive calls with the same statements append both
    times — the helper has no dedup logic, by design. The
    no-implicit-cleanup principle (from the intent gaps plan) is
    upheld: cleanup is the caller's explicit responsibility."""
    overlay = _HEADER
    once = _ensure_target_scope(overlay, "@any", ["mk add USES ssl"])
    twice = _ensure_target_scope(once, "@any", ["mk add USES ssl"])
    assert twice.count("mk add USES ssl") == 2


def test_helper_output_parses_through_engine() -> None:
    """End-to-end: the helper's output must round-trip through the
    dops parser. Catches any grammar drift in the placement logic
    (e.g. accidentally emitting `target @X` without a newline,
    mis-quoting, etc.)."""
    from dportsv3.engine.api import parse_dsl

    overlay = _HEADER + 'mk set USES "tar:xz"\n'
    result = _ensure_target_scope(
        overlay, "@2026Q2",
        ["mk add CFLAGS -fA", 'mk set LDFLAGS "-lfoo"'],
    )
    parsed = parse_dsl(result)
    assert parsed.ok, (
        f"helper output did not parse: "
        f"{[d.code for d in parsed.diagnostics]}"
    )


# ---------------------------------------------------------------------
# Step 38c — _check_target_scope_order invariant checker
# ---------------------------------------------------------------------


def test_checker_clean_any_only_returns_none() -> None:
    """The common case today: overlays produced by convert and the
    initial header are @any-only. Invariant holds trivially."""
    overlay = _HEADER + 'mk set USES "tar:xz"\n'
    assert _check_target_scope_order(overlay) is None


def test_checker_clean_any_then_q_returns_none() -> None:
    """The shape Step 38d will produce: @any first, then @Q sections.
    Engine declaration order makes @Q override @any naturally."""
    overlay = (
        _HEADER
        + 'mk set USES "tar:xz"\n'
        + "\n"
        + "target @2026Q2\n"
        + 'mk set USES "tar:lzma"\n'
    )
    assert _check_target_scope_order(overlay) is None


def test_checker_flags_any_after_q() -> None:
    """The actual violation: a `target @any` directive following any
    non-@any directive. The error message names both lines so the
    operator can locate the malformed section."""
    overlay = (
        "target @2026Q2\n"
        "mk add A B\n"
        "\n"
        "target @any\n"
        'mk set USES "tar:xz"\n'
    )
    err = _check_target_scope_order(overlay)
    assert err is not None
    assert "line 4" in err
    assert "@2026Q2" in err
    assert "line 1" in err


def test_checker_header_only_returns_none() -> None:
    """Single `target @any` in the header — the seeded state — passes."""
    assert _check_target_scope_order(_HEADER) is None


def test_checker_empty_overlay_returns_none() -> None:
    """Empty overlay text has no directives → no violations possible."""
    assert _check_target_scope_order("") is None


def test_checker_no_target_directive_returns_none() -> None:
    """Engine defaults to `@any` when no `target` directive exists
    (semantic.py:358). Implicit-@any overlays are clean."""
    overlay = 'mk set USES "tar:xz"\n'
    assert _check_target_scope_order(overlay) is None


def test_checker_multiple_consecutive_any_returns_none() -> None:
    """Redundant but legal: the engine treats consecutive `target @any`
    as no-op re-bindings."""
    overlay = (
        "target @any\n"
        "target @any\n"
        'mk set USES "tar:xz"\n'
    )
    assert _check_target_scope_order(overlay) is None


def test_checker_multiple_q_sections_in_any_order_returns_none() -> None:
    """No ordering constraint between @Q sections — they don't conflict
    (each filters to a different build)."""
    overlay = (
        _HEADER
        + "target @2026Q3\n"
        + "mk add A B\n"
        + "\n"
        + "target @2026Q2\n"
        + "mk add C D\n"
    )
    assert _check_target_scope_order(overlay) is None


def test_checker_target_main_only_returns_none() -> None:
    """A legitimate (if unusual) @main-only overlay: no @any to follow
    a non-@any directive, so the invariant trivially holds."""
    overlay = (
        "target @main\n"
        "port devel/foo\n"
        'mk set USES "tar:xz"\n'
    )
    assert _check_target_scope_order(overlay) is None


def test_checker_tolerates_leading_whitespace() -> None:
    """Indented `target` directives (unusual but possible from
    operator hand-edits) are still recognized."""
    overlay = (
        "    target @2026Q2\n"
        "    mk add A B\n"
        "\n"
        "  target @any\n"
        'mk set USES "tar:xz"\n'
    )
    err = _check_target_scope_order(overlay)
    assert err is not None
    assert "@2026Q2" in err


def test_checker_ignores_comment_lines() -> None:
    """`# target @2026Q2` is a comment, not a directive."""
    overlay = (
        "target @any\n"
        "# target @2026Q2 — this is a comment, not a directive\n"
        "mk set X \"Y\"\n"
    )
    assert _check_target_scope_order(overlay) is None


def test_checker_comma_separated_q_is_non_any() -> None:
    """A `target @2026Q4,@2026Q1` directive is treated as non-@any
    (the engine itself would reject mixing with @any). If a later
    `target @any` appears, that's a violation."""
    overlay = (
        "target @2026Q4,@2026Q1\n"
        "mk add A B\n"
        "\n"
        "target @any\n"
        'mk set USES "tar:xz"\n'
    )
    err = _check_target_scope_order(overlay)
    assert err is not None
    assert "@2026Q4,@2026Q1" in err


def test_checker_returns_first_violation_only() -> None:
    """When multiple violations exist, the checker reports the FIRST
    one and stops. The operator fixes that and re-runs to surface the
    next, rather than getting a flood of related diagnostics."""
    overlay = (
        "target @2026Q2\n"
        "mk add A\n"
        "target @any\n"
        "mk set X \"Y\"\n"
        "target @2026Q3\n"
        "mk add B\n"
        "target @any\n"
        "mk set Z \"W\"\n"
    )
    err = _check_target_scope_order(overlay)
    assert err is not None
    # Names the first violation (line 3 = @any after line 1 = @2026Q2).
    assert "line 3" in err
    assert "line 1" in err
    # The second violation (line 7) is not mentioned.
    assert "line 7" not in err


# ---------------------------------------------------------------------
# Step 38c — _append_overlay gate integration
# ---------------------------------------------------------------------


def _make_translator_with_overlay(tmp_path: Path, overlay_text: str) -> Translator:
    """Spin up a Translator pointing at a port whose overlay.dops
    already contains `overlay_text`. Used to drive the
    `_append_overlay` gate from outside the helper level."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    port_dir = workspace / "ports" / "devel" / "foo"
    port_dir.mkdir(parents=True)
    (port_dir / "overlay.dops").write_text(overlay_text)
    return Translator(workspace, "devel/foo", "dops")


def test_append_overlay_gate_refuses_violating_overlay(tmp_path: Path) -> None:
    """End-to-end: a renderer attempting to write into an overlay
    that already violates the invariant gets `ok=False` with the
    checker's error message. The substrate is untouched."""
    bad = (
        "target @2026Q2\n"
        "mk add A B\n"
        "\n"
        "target @any\n"
        "port devel/foo\n"
    )
    t = _make_translator_with_overlay(tmp_path, bad)

    # Use bump_portrevision as the test vehicle — the simplest
    # renderer that calls _append_overlay.
    result = t.apply({"type": "bump_portrevision"})

    assert result.ok is False
    assert "@any-first invariant" in (result.error or "")
    # Substrate untouched.
    assert (
        (tmp_path / "ws" / "ports" / "devel" / "foo" / "overlay.dops").read_text()
        == bad
    )


def test_append_overlay_gate_passes_clean_overlay(tmp_path: Path) -> None:
    """Regression: clean overlays continue to accept writes. The gate
    must NOT false-positive on convert-shaped output."""
    clean = (
        _HEADER
        + 'mk set USES "tar:xz"\n'
        + "\n"
        + "target @2026Q2\n"
        + "mk add CFLAGS -fA\n"
    )
    t = _make_translator_with_overlay(tmp_path, clean)

    result = t.apply({"type": "bump_portrevision"})

    assert result.ok is True, result.error
    # Substrate was modified (the PORTREVISION write landed).
    written = (
        tmp_path / "ws" / "ports" / "devel" / "foo" / "overlay.dops"
    ).read_text()
    assert "mk set PORTREVISION" in written


def test_append_overlay_gate_passes_fresh_overlay(tmp_path: Path) -> None:
    """Regression: when no overlay.dops exists yet, the seeded header
    is invariant-clean. The gate must allow the very first write."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "ports" / "devel" / "foo").mkdir(parents=True)
    t = Translator(workspace, "devel/foo", "dops")

    result = t.apply({"type": "bump_portrevision"})

    assert result.ok is True, result.error


# ---------------------------------------------------------------------
# Step 38d-1 — fresh-header blank-line preservation
# ---------------------------------------------------------------------


def test_helper_preserves_header_blank_on_first_statement() -> None:
    """Regression caught during 38d planning re-review: routing the
    first statement on a fresh header through `_ensure_target_scope`
    must preserve the blank line between port/type/reason metadata
    and the operation. The pre-38d walk-back-blanks logic ate that
    blank because it ran unconditionally on the EOF case."""
    result = _ensure_target_scope(_HEADER, "@any", ['mk set USES "tar:xz"'])
    assert result == _HEADER + 'mk set USES "tar:xz"\n'
    # Explicit check on the blank line — survives between
    # `reason "x"` and the first `mk set`.
    assert '\n\nmk set USES "tar:xz"' in result


def test_helper_at_eof_does_not_walk_back_blanks() -> None:
    """38d-1 precise behavior: at EOF the helper appends without
    walking back blanks. (In the next-target case it does walk back,
    so the separator stays attached to the next block — covered by
    `test_helper_any_insert_preserves_blank_before_next_q`.)"""
    overlay = (
        "target @any\n"
        "port devel/foo\n"
        "\n"
        "mk set X \"Y\"\n"
        "\n"
    )
    result = _ensure_target_scope(overlay, "@any", ["mk add Z"])
    # Blank line between port and first stmt preserved; trailing
    # blank before EOF also preserved (it was there in input).
    assert (
        result
        == "target @any\nport devel/foo\n\nmk set X \"Y\"\n\nmk add Z\n"
    )


# ---------------------------------------------------------------------
# Step 38d-2 — legacy @any-no-match places at top of operations
# ---------------------------------------------------------------------


def test_helper_legacy_main_only_inserts_any_at_top() -> None:
    """A legacy or operator-hand-edited overlay has `target @main` as
    its first directive but no `target @any`. An @any op via the
    helper must NOT append at EOF (would violate the @any-first
    invariant); instead it inserts a fresh `target @any` block at
    the very top, just before the existing directive."""
    legacy = (
        "target @main\n"
        "port devel/foo\n"
        "type port\n"
        'mk set X "Y"\n'
    )
    result = _ensure_target_scope(legacy, "@any", ["mk add USES ssl"])
    assert result == (
        "target @any\n"
        "mk add USES ssl\n"
        "\n"
        "target @main\n"
        "port devel/foo\n"
        "type port\n"
        'mk set X "Y"\n'
    )


def test_helper_legacy_multi_q_inserts_any_at_top() -> None:
    """Same shape with multiple existing @Q sections — @any still
    lands at the very top, before the first @Q directive."""
    legacy = (
        "target @2026Q2\n"
        "mk add A B\n"
        "\n"
        "target @2026Q3\n"
        "mk add C D\n"
    )
    result = _ensure_target_scope(legacy, "@any", ['mk set USES "tar:xz"'])
    assert result == (
        "target @any\n"
        'mk set USES "tar:xz"\n'
        "\n"
        "target @2026Q2\n"
        "mk add A B\n"
        "\n"
        "target @2026Q3\n"
        "mk add C D\n"
    )


def test_helper_legacy_fix_output_passes_invariant_check() -> None:
    """End-to-end: the @any-at-top placement must satisfy the
    @any-first invariant that the checker (38c) enforces. Without
    this, the next intent's gate would refuse on the helper's own
    output."""
    legacy = (
        "target @main\n"
        "port devel/foo\n"
        'mk set X "Y"\n'
    )
    result = _ensure_target_scope(legacy, "@any", ["mk add Z"])
    assert _check_target_scope_order(result) is None


def test_helper_legacy_fix_output_parses_through_engine() -> None:
    """The reformatted overlay must round-trip through the dops parser.
    Catches any grammar drift in the new insertion path."""
    from dportsv3.engine.api import parse_dsl

    legacy = (
        "target @main\n"
        "port devel/foo\n"
        "type port\n"
        'mk set X "Y"\n'
    )
    result = _ensure_target_scope(legacy, "@any", ["mk add USES ssl"])
    parsed = parse_dsl(result)
    assert parsed.ok, (
        f"38d-2 helper output did not parse: "
        f"{[d.code for d in parsed.diagnostics]}"
    )


def test_helper_legacy_fix_skipped_when_statements_empty() -> None:
    """The empty-statements guard (Issue A from 38b review) takes
    precedence over the legacy @any-no-match path. Helper returns
    the overlay unchanged rather than emitting an empty @any block."""
    legacy = (
        "target @main\n"
        'mk set X "Y"\n'
    )
    result = _ensure_target_scope(legacy, "@any", [])
    assert result == legacy


def test_helper_no_directive_overlay_falls_back_to_eof_append() -> None:
    """Edge case: an overlay with NO target directive at all (engine
    treats this as implicit @any). The @any-no-match-at-top path
    requires `target_positions` to be non-empty, so this falls
    through to the generic EOF-append branch."""
    no_directive = 'port devel/foo\nmk set X "Y"\n'
    result = _ensure_target_scope(no_directive, "@any", ["mk add Z"])
    # Generic EOF-append behavior: adds a `target @any` block at EOF
    # (preceded by blank separator). Not invariant-violating because
    # there are no @Q sections to come after.
    assert "target @any\nmk add Z" in result


# ---------------------------------------------------------------------
# Step 38d-3 — `_append_overlay` scope arg + @current resolution
# ---------------------------------------------------------------------


def _make_seeded_translator(
    tmp_path: Path, target: str | None = None,
) -> Translator:
    """Build a Translator pointing at a port whose workspace is git-
    initialized (needed for `diff_from_before` in EditResult). The
    overlay.dops is NOT pre-created — the first _append_overlay call
    will seed it via `_initial_overlay_header`."""
    import subprocess
    ws = tmp_path / "ws"
    ws.mkdir()
    subprocess.run(["git", "-C", str(ws), "init", "-q"], check=True)
    subprocess.run(
        ["git", "-C", str(ws), "config", "user.email", "t@t"], check=True,
    )
    subprocess.run(
        ["git", "-C", str(ws), "config", "user.name", "t"], check=True,
    )
    (ws / "README").write_text("baseline\n")
    subprocess.run(["git", "-C", str(ws), "add", "README"], check=True)
    subprocess.run(
        ["git", "-C", str(ws), "commit", "-qm", "init"], check=True,
    )
    (ws / "ports" / "devel" / "foo").mkdir(parents=True)
    return Translator(ws, "devel/foo", "dops", target=target)


def test_append_overlay_scope_none_is_backward_compat(tmp_path: Path) -> None:
    """`scope=None` (the default) must preserve the pre-38d-3
    dumb-append behavior. The 5 existing renderers all pass this
    shape today; they continue working without modification."""
    from dportsv3.agent.edit_intent._dops import _append_overlay

    t = _make_seeded_translator(tmp_path)
    r = _append_overlay(t, "change_makefile", ['mk set USES "tar:xz"'])

    assert r.ok, r.error
    written = t.port_path("overlay.dops").read_text()
    # Header was seeded; stmt landed under the @any section.
    assert "target @any\n" in written
    assert 'mk set USES "tar:xz"\n' in written


def test_append_overlay_scope_any_routes_through_helper(tmp_path: Path) -> None:
    """Explicit `scope='@any'` is the same effect as None on a fresh
    overlay — both produce a statement under the header's @any
    section. (Internally, scope='@any' takes the helper path.)"""
    from dportsv3.agent.edit_intent._dops import _append_overlay

    t = _make_seeded_translator(tmp_path)
    r = _append_overlay(
        t, "change_makefile", ['mk add USES ssl'], scope="@any",
    )

    assert r.ok, r.error
    written = t.port_path("overlay.dops").read_text()
    assert "target @any" in written
    assert "mk add USES ssl" in written
    # No spurious `target @any` re-emission (helper found the section
    # in the seeded header).
    assert written.count("target @any\n") == 1


def test_append_overlay_scope_explicit_q_creates_new_section(
    tmp_path: Path,
) -> None:
    """`scope='@2026Q2'` on a fresh overlay routes through the helper
    and creates a fresh `target @2026Q2` block after the header's
    @any section."""
    from dportsv3.agent.edit_intent._dops import _append_overlay

    t = _make_seeded_translator(tmp_path)
    r = _append_overlay(
        t, "change_makefile", ["mk add CFLAGS -fA"], scope="@2026Q2",
    )

    assert r.ok, r.error
    written = t.port_path("overlay.dops").read_text()
    # @any section (from header) precedes the new @2026Q2 section.
    any_pos = written.index("target @any")
    q_pos = written.index("target @2026Q2")
    assert any_pos < q_pos, written
    assert "mk add CFLAGS -fA" in written
    # The @any-first invariant holds.
    from dportsv3.agent.edit_intent._dops import _check_target_scope_order
    assert _check_target_scope_order(written) is None


def test_append_overlay_scope_current_resolves_from_t_target(
    tmp_path: Path,
) -> None:
    """`scope='@current'` must resolve from `t.target` at apply time.
    A Translator built with target='@2026Q2' translates the agent's
    @current request into a literal `target @2026Q2` block."""
    from dportsv3.agent.edit_intent._dops import _append_overlay

    t = _make_seeded_translator(tmp_path, target="@2026Q2")
    r = _append_overlay(
        t, "change_makefile", ["mk add CFLAGS -fA"], scope="@current",
    )

    assert r.ok, r.error
    written = t.port_path("overlay.dops").read_text()
    # The resolved scope landed in the substrate, NOT the literal
    # `@current` (which is engine-grammar-invalid).
    assert "target @2026Q2" in written
    assert "@current" not in written


def test_append_overlay_scope_current_refuses_when_target_none(
    tmp_path: Path,
) -> None:
    """If the agent requests `@current` but the runner failed to
    populate the env-target cache (`t.target is None`), the call is
    refused with a specific error pointing at the runner-side bug.
    This is the surfacing path for "caller forgot set_env_target"."""
    from dportsv3.agent.edit_intent._dops import _append_overlay

    t = _make_seeded_translator(tmp_path, target=None)
    r = _append_overlay(
        t, "change_makefile", ["mk add CFLAGS -fA"], scope="@current",
    )

    assert r.ok is False
    assert "@current" in (r.error or "")
    assert "set_env_target" in (r.error or "")
    # Substrate untouched.
    assert not t.port_path("overlay.dops").exists()


def test_append_overlay_scope_current_refuses_when_target_empty_string(
    tmp_path: Path,
) -> None:
    """`t.target == ""` is the same as None for resolution purposes
    (an empty string is falsy in the cache layer too). Same refusal."""
    from dportsv3.agent.edit_intent._dops import _append_overlay

    t = _make_seeded_translator(tmp_path, target="")
    r = _append_overlay(
        t, "change_makefile", ["mk add CFLAGS -fA"], scope="@current",
    )

    assert r.ok is False
    assert "@current" in (r.error or "")


def test_append_overlay_invalid_scope_refused(tmp_path: Path) -> None:
    """Malformed scope strings (typos, hand-constructed values that
    don't match the engine grammar) refuse with a clear error
    naming the expected forms."""
    from dportsv3.agent.edit_intent._dops import _append_overlay

    t = _make_seeded_translator(tmp_path)
    r = _append_overlay(
        t, "change_makefile", ["mk add X"], scope="@bogus",
    )

    assert r.ok is False
    assert "invalid scope" in (r.error or "")
    assert "@any, @main, or @YYYYQ" in (r.error or "")
    assert not t.port_path("overlay.dops").exists()


def test_append_overlay_invariant_gate_runs_before_scope_resolution(
    tmp_path: Path,
) -> None:
    """Order matters: if the existing overlay is malformed (38c
    violation), the gate refuses BEFORE scope resolution runs. A
    scope-specific error would obscure the underlying file-layout
    problem."""
    from dportsv3.agent.edit_intent._dops import _append_overlay

    t = _make_seeded_translator(tmp_path)
    # Hand-write a malformed overlay (@any-after-@Q).
    t.port_path("overlay.dops").write_text(
        "target @2026Q2\nmk add A\ntarget @any\nport devel/foo\n"
    )

    # Call with scope=@current, but t.target is None — would normally
    # refuse with the @current error. Invariant violation should
    # take precedence.
    r = _append_overlay(
        t, "change_makefile", ["mk add X"], scope="@current",
    )

    assert r.ok is False
    assert "@any-first invariant" in (r.error or "")
    # The @current error should NOT appear — gate fired first.
    assert "set_env_target" not in (r.error or "")


def test_append_overlay_scope_with_prefilter(tmp_path: Path) -> None:
    """When both `scope` and `prefilter` are passed, the prefilter
    still applies before placement. Documents that the two
    parameters compose. (Becomes moot once 38e removes the only
    existing prefilter, but the dispatch ordering must be right.)"""
    from dportsv3.agent.edit_intent._dops import _append_overlay

    t = _make_seeded_translator(tmp_path)
    # Seed an overlay with an existing `mk set USES` line.
    t.port_path("overlay.dops").write_text(
        "target @any\nport devel/foo\n\n"
        'mk set USES "old"\n'
    )

    # Prefilter that strips the existing `mk set USES` line.
    def _strip_uses(text: str) -> str:
        return "\n".join(
            line for line in text.split("\n")
            if "mk set USES" not in line
        )

    r = _append_overlay(
        t, "change_makefile",
        ['mk set USES "new"'],
        prefilter=_strip_uses,
        scope="@any",
    )

    assert r.ok, r.error
    written = t.port_path("overlay.dops").read_text()
    assert 'mk set USES "old"' not in written
    assert 'mk set USES "new"' in written


def test_append_overlay_scope_arg_does_not_break_existing_callers(
    tmp_path: Path,
) -> None:
    """The 5 existing renderers (replace_in_patch, add_patch,
    add_file, change_makefile, bump_portrevision) call
    _append_overlay without `scope`. End-to-end through a Translator
    + bump_portrevision intent: the unchanged dumb-append path still
    works."""
    t = _make_seeded_translator(tmp_path)
    r = t.apply({"type": "bump_portrevision"})

    assert r.ok, r.error
    written = t.port_path("overlay.dops").read_text()
    assert "mk set PORTREVISION" in written
