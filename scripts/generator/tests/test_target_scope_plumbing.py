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
    ws.mkdir(parents=True)
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


# ---------------------------------------------------------------------
# Step 38d-4 + 38d-5 — schema + dataclass scope field
# ---------------------------------------------------------------------


# (intent_type, valid-payload-without-scope) tuples for the 5 intents
# that gained the scope field in 38d-4. Used as parametrize sources
# below to avoid copy-paste across 5 intent types.
_SCOPE_BEARING_INTENTS = [
    (
        "replace_in_patch",
        {"target": "files/extra-config.in", "find": "OLD", "replace": "NEW"},
    ),
    (
        "add_patch",
        {
            "target": "dragonfly/patch-src_x.c",
            "diff": "--- a\n+++ b\n@@ -1 +1 @@\n-x\n+y\n",
        },
    ),
    (
        "add_file",
        {"dest": "files/x", "kind": "resource", "content": "hello"},
    ),
    (
        "change_makefile",
        {"path": "Makefile", "key": "USES", "op": "set", "value": "cmake"},
    ),
    (
        "bump_portrevision",
        {},
    ),
    (
        "drop_mk_directive",
        {"kind": "unset", "key": "LICENSE_FILE"},
    ),
    (
        "drop_file",
        {"target": "files/x", "reason": "obsolete"},
    ),
    (
        "drop_target_block",
        {"block_name": "do-build", "reason": "obsolete"},
    ),
]


def test_scope_omitted_defaults_to_any() -> None:
    """38d-5 dataclass default: omitting `scope` from the wire payload
    yields `intent.scope == "@any"` after parse. JSON Schema's
    `default` field is informational only (jsonschema.validate doesn't
    inject); the dataclass default is what actually fires."""
    import pytest as _pytest  # noqa: F401
    from dportsv3.agent.edit_intent.validator import parse_intent

    for intent_type, extra in _SCOPE_BEARING_INTENTS:
        intent = parse_intent({"type": intent_type, **extra})
        assert intent.scope == "@any", (
            f"{intent_type} default scope should be @any, got "
            f"{intent.scope!r}"
        )


def test_scope_explicit_any_parses() -> None:
    """Explicit `scope='@any'` passes the schema enum and lands in
    the dataclass field."""
    from dportsv3.agent.edit_intent.validator import parse_intent

    for intent_type, extra in _SCOPE_BEARING_INTENTS:
        intent = parse_intent({"type": intent_type, "scope": "@any", **extra})
        assert intent.scope == "@any"


def test_scope_explicit_current_parses() -> None:
    """Explicit `scope='@current'` passes the schema enum and lands
    in the dataclass field. Resolution to a concrete @YYYYQX
    happens later in `_append_overlay` (38d-3), not at parse."""
    from dportsv3.agent.edit_intent.validator import parse_intent

    for intent_type, extra in _SCOPE_BEARING_INTENTS:
        intent = parse_intent(
            {"type": intent_type, "scope": "@current", **extra},
        )
        assert intent.scope == "@current"


def test_scope_rejects_literal_quarter() -> None:
    """38d's locked decision: the agent vocabulary is @any/@current
    only. A literal @YYYYQX in the payload is refused at validation
    time. The runner injects t.target on the renderer side; the
    agent never names the target directly."""
    import pytest
    from dportsv3.agent.edit_intent.validator import IntentError, parse_intent

    for intent_type, extra in _SCOPE_BEARING_INTENTS:
        with pytest.raises(IntentError, match="enum|@2026Q2"):
            parse_intent(
                {"type": intent_type, "scope": "@2026Q2", **extra},
            )


def test_scope_rejects_invalid_values() -> None:
    """Schema enum is exhaustive — null, empty string, and miscased
    variants all refuse. (Refusal mode is JSON Schema's enum
    diagnostic; we don't assert on the exact message.)"""
    import pytest
    from dportsv3.agent.edit_intent.validator import IntentError, parse_intent

    bad_scopes = ["", "@", "@CURRENT", "current", "@any "]
    for intent_type, extra in _SCOPE_BEARING_INTENTS:
        for bad in bad_scopes:
            with pytest.raises(IntentError):
                parse_intent(
                    {"type": intent_type, "scope": bad, **extra},
                )


def test_drop_patch_rejects_scope_field() -> None:
    """drop_patch operates on a named entity (the patch path) — there's
    only one of any given patch. Scope is irrelevant; the schema
    rejects the field via `additionalProperties: false`."""
    import pytest
    from dportsv3.agent.edit_intent.validator import IntentError, parse_intent

    with pytest.raises(IntentError):
        parse_intent({
            "type": "drop_patch",
            "target": "dragonfly/patch-x.c",
            "reason": "obsolete",
            "scope": "@any",   # not allowed
        })


def test_replace_in_dops_block_rejects_scope_field() -> None:
    """replace_in_dops_block operates on a named heredoc block —
    block-name-collision-across-scopes is a separate gap, not 38d's
    concern. The schema rejects `scope` via `additionalProperties:
    false`."""
    import pytest
    from dportsv3.agent.edit_intent.validator import IntentError, parse_intent

    with pytest.raises(IntentError):
        parse_intent({
            "type": "replace_in_dops_block",
            "block_name": "dfly-patch",
            "find": "old",
            "replace": "new",
            "scope": "@any",   # not allowed
        })


def test_schema_for_surfaces_scope_field() -> None:
    """The `intent_reference` tool returns the JSON schema for a
    given intent type. Agents read the schema to pick up field
    shapes including scope. Verify the field appears in each
    scope-bearing intent's schema."""
    from dportsv3.agent.edit_intent.validator import schema_for

    for intent_type, _extra in _SCOPE_BEARING_INTENTS:
        s = schema_for(intent_type)
        assert "scope" in s["properties"], (
            f"{intent_type} schema must expose `scope` in properties"
        )
        scope_def = s["properties"]["scope"]
        assert scope_def["enum"] == ["@any", "@current"]
        assert scope_def["default"] == "@any"


def test_schema_for_drop_patch_does_not_include_scope() -> None:
    """drop_patch's schema does NOT carry scope — verifies the
    asymmetric coverage (8 of 10 intents have scope, 2 do not)."""
    from dportsv3.agent.edit_intent.validator import schema_for

    assert "scope" not in schema_for("drop_patch")["properties"]
    assert "scope" not in schema_for("replace_in_dops_block")["properties"]


# ---------------------------------------------------------------------
# Step 38d-6 — renderers wire intent.scope into _append_overlay
# ---------------------------------------------------------------------


# Per-renderer test fixtures. Each tuple is (intent_type, payload_extra,
# stmt_substring-that-should-appear-in-the-emitted-overlay-line). The
# stmt substring matches the dops grammar emission for the intent.
_SCOPE_RENDERER_FIXTURES = [
    (
        "replace_in_patch",
        {"target": "files/extra-config.in", "find": "OLD", "replace": "NEW"},
        "text replace-once file files/extra-config.in",
    ),
    (
        "add_patch",
        {
            "target": "dragonfly/patch-src_x.c",
            "diff": "--- a\n+++ b\n@@ -1 +1 @@\n-x\n+y\n",
        },
        "patch apply dragonfly/patch-src_x.c",
    ),
    (
        "add_file",
        {"dest": "files/extra-config.in", "kind": "resource", "content": "hi"},
        "file copy files/extra-config.in",
    ),
    (
        "change_makefile",
        {"path": "Makefile", "key": "USES", "op": "append", "value": "pkgconfig"},
        'mk add USES "pkgconfig"',
    ),
    (
        "bump_portrevision",
        {},
        "mk set PORTREVISION",
    ),
]


# ---- Backward-compat per call site ----------------------------------


def test_renderer_default_scope_lands_in_any_section(tmp_path: Path) -> None:
    """Default scope (omitted in payload, dataclass fills @any) on a
    fresh overlay: each renderer's statement lands inside the @any
    section, preserving the header blank-line separator. Pre-38d-6
    behavior used dumb-append; post-38d-6 routes through the helper
    with byte-identical output on clean overlays."""
    for intent_type, extra, stmt_substring in _SCOPE_RENDERER_FIXTURES:
        t = _make_seeded_translator(tmp_path / intent_type)
        r = t.apply({"type": intent_type, **extra})
        assert r.ok, (intent_type, r.error)
        written = t.port_path("overlay.dops").read_text()
        # Statement landed under the @any section.
        assert stmt_substring in written, (intent_type, written)
        # No spurious target directive — no @Q section emitted.
        assert written.count("target @") == 1, written
        # The header blank-line separator (between `reason "..."` and
        # the first statement) is preserved.
        assert "\n\n" in written, written


def test_renderer_explicit_any_matches_default(tmp_path: Path) -> None:
    """Explicit `scope='@any'` produces the same substrate as omitting
    `scope` (the dataclass default). Confirms the agent has two
    equivalent ways to express "universal fix": omit or explicit."""
    for intent_type, extra, _stmt in _SCOPE_RENDERER_FIXTURES:
        # Build two parallel ports — one with omitted scope, one with
        # explicit @any. Diffs should match.
        t_omitted = _make_seeded_translator(tmp_path / f"{intent_type}_omit")
        t_explicit = _make_seeded_translator(tmp_path / f"{intent_type}_any")
        r1 = t_omitted.apply({"type": intent_type, **extra})
        r2 = t_explicit.apply({"type": intent_type, "scope": "@any", **extra})
        assert r1.ok and r2.ok, (intent_type, r1.error, r2.error)
        assert (
            t_omitted.port_path("overlay.dops").read_text()
            == t_explicit.port_path("overlay.dops").read_text()
        )


def test_change_makefile_unset_branch_passes_scope(tmp_path: Path) -> None:
    """The change_makefile renderer has TWO _append_overlay call
    sites — one for op=unset, one for set/append/remove. Both got
    wired. Verifies the unset branch."""
    t = _make_seeded_translator(tmp_path, target="@2026Q2")
    r = t.apply({
        "type": "change_makefile", "path": "Makefile", "key": "USES",
        "op": "unset", "scope": "@current",
    })
    assert r.ok, r.error
    written = t.port_path("overlay.dops").read_text()
    assert "target @2026Q2" in written
    assert "mk unset USES" in written


def test_add_file_materialize_branch_passes_scope(tmp_path: Path) -> None:
    """The add_file renderer has TWO _append_overlay call sites —
    one for kind=resource (writes a file + emits `file copy`), one
    for kind=materialize (just emits `file materialize`). Both got
    wired. Verifies the materialize branch."""
    t = _make_seeded_translator(tmp_path, target="@2026Q3")
    r = t.apply({
        "type": "add_file",
        "dest": "dragonfly/patch-foo",
        "kind": "materialize",
        "source": "dragonfly/patch-foo",
        "scope": "@current",
    })
    assert r.ok, r.error
    written = t.port_path("overlay.dops").read_text()
    assert "target @2026Q3" in written
    assert "file materialize dragonfly/patch-foo -> dragonfly/patch-foo" in written


# ---- End-to-end @current resolution per intent ---------------------


def test_renderer_current_scope_resolves_to_t_target(tmp_path: Path) -> None:
    """`scope=@current` resolves to `t.target` at apply time and the
    statement lands under a fresh `target @2026Q2` section. The
    agent-facing `@current` literal never appears in the substrate
    (engine grammar wouldn't accept it)."""
    for intent_type, extra, stmt_substring in _SCOPE_RENDERER_FIXTURES:
        t = _make_seeded_translator(tmp_path / intent_type, target="@2026Q2")
        r = t.apply({"type": intent_type, "scope": "@current", **extra})
        assert r.ok, (intent_type, r.error)
        written = t.port_path("overlay.dops").read_text()
        assert "target @2026Q2" in written, (intent_type, written)
        assert stmt_substring in written, (intent_type, written)
        # Statement appears AFTER the @2026Q2 directive (in the new
        # section), not under @any.
        q_pos = written.index("target @2026Q2")
        stmt_pos = written.index(stmt_substring)
        assert q_pos < stmt_pos, (intent_type, written)
        # `@current` literal never reaches the substrate.
        assert "@current" not in written, (intent_type, written)


# ---- @current with no t.target refused per intent -------------------


def test_renderer_current_refused_when_t_target_none(tmp_path: Path) -> None:
    """When the agent requests `@current` but the runner failed to
    populate `t.target` (cache miss), the call refuses with the 38d-3
    error. Substrate untouched. Each renderer must propagate this
    refusal cleanly (no half-applied state)."""
    for intent_type, extra, _stmt in _SCOPE_RENDERER_FIXTURES:
        t = _make_seeded_translator(tmp_path / intent_type)  # target=None
        r = t.apply({"type": intent_type, "scope": "@current", **extra})
        assert r.ok is False, (intent_type, r)
        assert "@current" in (r.error or ""), (intent_type, r.error)
        assert "set_env_target" in (r.error or ""), (intent_type, r.error)


# ---- Side-effect rollback on scope refusal --------------------------


def test_add_patch_rollback_on_scope_refusal(tmp_path: Path) -> None:
    """add_patch writes the patch file BEFORE calling _append_overlay.
    If the overlay write refuses (e.g. @current with no t.target),
    the existing rollback path must delete the patch file. No
    orphan side-effects from a scope refusal."""
    t = _make_seeded_translator(tmp_path)  # target=None
    patch_path = t.port_path("dragonfly/patch-foo.c")

    r = t.apply({
        "type": "add_patch",
        "target": "dragonfly/patch-foo.c",
        "diff": "--- a\n+++ b\n@@ -1 +1 @@\n-x\n+y\n",
        "scope": "@current",
    })

    assert r.ok is False
    # The patch file was written but then rolled back.
    assert not patch_path.exists(), "add_patch left an orphan patch file"


def test_add_file_resource_rollback_on_scope_refusal(tmp_path: Path) -> None:
    """add_file kind=resource also writes the resource file before
    calling _append_overlay. Same rollback expectation as add_patch
    on scope refusal."""
    t = _make_seeded_translator(tmp_path)  # target=None
    resource_path = t.port_path("files/extra-config.in")

    r = t.apply({
        "type": "add_file",
        "dest": "files/extra-config.in",
        "kind": "resource",
        "content": "hello",
        "scope": "@current",
    })

    assert r.ok is False
    assert not resource_path.exists(), "add_file left an orphan resource file"


# ---- drop_patch and replace_in_dops_block regression ----------------


def test_drop_patch_unaffected_by_38d_6(tmp_path: Path) -> None:
    """drop_patch operates on a named patch — no scope field on the
    intent, no scope handling in the renderer (it bypasses
    _append_overlay entirely). 38d-6 must leave it unchanged."""
    t = _make_seeded_translator(tmp_path)
    # Seed an overlay with a patch apply directive to drop.
    t.port_path("overlay.dops").write_text(
        "target @any\n"
        "port devel/foo\n"
        "\n"
        "patch apply dragonfly/patch-old.c\n"
    )
    # Also create the patch file on disk so drop_patch can delete it.
    patch_file = t.port_path("dragonfly/patch-old.c")
    patch_file.parent.mkdir(parents=True, exist_ok=True)
    patch_file.write_text("dummy")

    r = t.apply({
        "type": "drop_patch",
        "target": "dragonfly/patch-old.c",
        "reason": "obsolete",
    })
    assert r.ok, r.error
    assert "patch apply dragonfly/patch-old.c" not in (
        t.port_path("overlay.dops").read_text()
    )


def test_replace_in_dops_block_unaffected_by_38d_6(tmp_path: Path) -> None:
    """replace_in_dops_block bypasses _append_overlay. No scope field
    on the intent. 38d-6 leaves it unchanged."""
    t = _make_seeded_translator(tmp_path)
    t.port_path("overlay.dops").write_text(
        "target @any\nport devel/foo\n\n"
        "mk target set dfly-patch <<MK\n"
        "\t@echo old\n"
        "MK\n"
    )
    r = t.apply({
        "type": "replace_in_dops_block",
        "block_name": "dfly-patch",
        "find": "@echo old",
        "replace": "@echo new",
    })
    assert r.ok, r.error
    written = t.port_path("overlay.dops").read_text()
    assert "@echo new" in written
    assert "@echo old" not in written


# ---------------------------------------------------------------------
# Step 38f — get_effective_overlay agent tool
# ---------------------------------------------------------------------


def _seed_workspace(tmp_path: Path) -> Path:
    """Create a tmp workspace with a `ports/devel/foo/` skeleton ready
    to host an overlay.dops. Returns the workspace root (used to
    monkeypatch `env_paths`)."""
    root = tmp_path / "ws"
    root.mkdir()
    (root / "ports" / "devel" / "foo").mkdir(parents=True)
    return root


def _stub_env_paths(monkeypatch, root: Path) -> None:
    """Point `worker.env_paths(env)` at our synthetic workspace so
    `get_effective_overlay` reads from the test fixture."""
    class _FakePaths:
        def __init__(self, r):
            self.deltaports = r
    monkeypatch.setattr(worker, "env_paths", lambda env: _FakePaths(root))


def test_get_effective_overlay_no_target_cached_refuses(tmp_path: Path) -> None:
    """38f's `@current`-style refusal: when the runner failed to call
    `set_env_target`, the tool surfaces a calling-context-bug error
    instead of silently falling back."""
    worker._TARGET_CACHE.clear()
    r = worker.get_effective_overlay("never-seen-env", "devel/foo")
    assert r["ok"] is False
    assert "no compose target cached" in r["error"]
    assert "set_env_target" in r["error"]


def test_get_effective_overlay_workspace_missing_refuses(
    tmp_path: Path, monkeypatch,
) -> None:
    """A non-existent env workspace refuses cleanly rather than
    returning empty lists. Mirrors `apply_intent`'s shape."""
    worker._TARGET_CACHE.clear()
    worker.set_env_target("test", "@2026Q2")
    _stub_env_paths(monkeypatch, tmp_path / "does-not-exist")
    r = worker.get_effective_overlay("test", "devel/foo")
    assert r["ok"] is False
    assert "workspace does not exist" in r["error"]


def test_get_effective_overlay_no_overlay_file_returns_empty(
    tmp_path: Path, monkeypatch,
) -> None:
    """On a port that's never had agent edits, `overlay.dops` doesn't
    exist yet. That's a legitimate state — empty result, not error."""
    worker._TARGET_CACHE.clear()
    worker.set_env_target("test", "@2026Q2")
    root = _seed_workspace(tmp_path)
    _stub_env_paths(monkeypatch, root)

    r = worker.get_effective_overlay("test", "devel/foo")
    assert r["ok"] is True
    assert r["target"] == "@2026Q2"
    assert r["effective_ops"] == []
    assert r["filtered_out"] == []
    assert r["overlay_path"].endswith("overlay.dops")


def test_get_effective_overlay_any_only_overlay(
    tmp_path: Path, monkeypatch,
) -> None:
    """All ops scoped to @any → all in effective_ops; nothing filtered.
    The common case today."""
    worker._TARGET_CACHE.clear()
    worker.set_env_target("test", "@2026Q2")
    root = _seed_workspace(tmp_path)
    _stub_env_paths(monkeypatch, root)
    (root / "ports" / "devel" / "foo" / "overlay.dops").write_text(
        'target @any\nport devel/foo\ntype port\nreason "x"\n\n'
        'mk add USES "pkgconfig"\n'
        'mk set LICENSE "BSD2CLAUSE"\n'
    )

    r = worker.get_effective_overlay("test", "devel/foo")
    assert r["ok"] is True
    kinds = [op["kind"] for op in r["effective_ops"]]
    assert "mk.var.token_add" in kinds
    assert "mk.var.set" in kinds
    for op in r["effective_ops"]:
        assert op["scope"] == "@any"
    assert r["filtered_out"] == []


def test_get_effective_overlay_mixed_scope_partitions_correctly(
    tmp_path: Path, monkeypatch,
) -> None:
    """The core feature: a multi-target overlay produces
    effective_ops (@any + env target) and filtered_out (other
    targets, with reasons). Order is declaration order."""
    worker._TARGET_CACHE.clear()
    worker.set_env_target("test", "@2026Q2")
    root = _seed_workspace(tmp_path)
    _stub_env_paths(monkeypatch, root)
    (root / "ports" / "devel" / "foo" / "overlay.dops").write_text(
        'target @any\nport devel/foo\ntype port\nreason "x"\n\n'
        'mk add USES "pkgconfig"\n'
        '\n'
        'target @2026Q2\n'
        'mk set USES "cmake"\n'
        '\n'
        'target @2026Q3\n'
        'mk set USES "meson"\n'
    )

    r = worker.get_effective_overlay("test", "devel/foo")
    assert r["ok"] is True
    assert r["target"] == "@2026Q2"

    # Effective ops: @any first, then @2026Q2.
    eff_scopes = [op["scope"] for op in r["effective_ops"]]
    assert eff_scopes == ["@any", "@2026Q2"]
    # Values preserved.
    eff_values = [op.get("value") for op in r["effective_ops"]]
    assert "pkgconfig" in eff_values
    assert "cmake" in eff_values

    # Filtered: the @2026Q3 op with a reason.
    assert len(r["filtered_out"]) == 1
    fop = r["filtered_out"][0]
    assert fop["scope"] == "@2026Q3"
    assert fop["value"] == "meson"
    assert "@2026Q3" in fop["reason"]
    assert "@2026Q2" in fop["reason"]


def test_get_effective_overlay_target_field_distinct_from_op_scope(
    tmp_path: Path, monkeypatch,
) -> None:
    """The agent-facing response carefully separates the env's build
    target (top-level `target`) from each op's binding scope
    (per-op `scope`). Confirms the alias from PlanOp.target →
    response `scope`."""
    worker._TARGET_CACHE.clear()
    worker.set_env_target("test", "@2026Q3")
    root = _seed_workspace(tmp_path)
    _stub_env_paths(monkeypatch, root)
    (root / "ports" / "devel" / "foo" / "overlay.dops").write_text(
        'target @any\nport devel/foo\ntype port\nreason "x"\n\n'
        'mk add USES "ssl"\n'
    )

    r = worker.get_effective_overlay("test", "devel/foo")
    assert r["target"] == "@2026Q3"   # env's build target
    assert r["effective_ops"][0]["scope"] == "@any"
    # No `target` key on op dicts — `target` would collide with
    # the response's top-level meaning.
    assert "target" not in r["effective_ops"][0]


def test_get_effective_overlay_malformed_overlay_refuses(
    tmp_path: Path, monkeypatch,
) -> None:
    """An overlay that fails parse/semantic refuses with the engine's
    first diagnostic. Lets the agent see the file is broken rather
    than getting silently empty results."""
    worker._TARGET_CACHE.clear()
    worker.set_env_target("test", "@2026Q2")
    root = _seed_workspace(tmp_path)
    _stub_env_paths(monkeypatch, root)
    # Invalid: `mk` without an action.
    (root / "ports" / "devel" / "foo" / "overlay.dops").write_text(
        "target @any\nmk\n"
    )

    r = worker.get_effective_overlay("test", "devel/foo")
    assert r["ok"] is False
    assert "engine refused" in r["error"]


def test_get_effective_overlay_target_field_in_no_overlay_branch(
    tmp_path: Path, monkeypatch,
) -> None:
    """Even on the empty-overlay path, the `target` field is populated
    — agents can see which build the empty result is for."""
    worker._TARGET_CACHE.clear()
    worker.set_env_target("test", "@2026Q4")
    root = _seed_workspace(tmp_path)
    _stub_env_paths(monkeypatch, root)
    r = worker.get_effective_overlay("test", "devel/foo")
    assert r["target"] == "@2026Q4"


def test_get_effective_overlay_tool_registered() -> None:
    """The dispatcher exposes the tool so the LLM can call it via
    `get_effective_overlay(origin=...)`. End-to-end through
    `tools.dispatch` confirms registration + routing."""
    from dportsv3.agent import tools

    assert "get_effective_overlay" in tools.names()
    # Verify dispatch path with a deliberate refusal (no target
    # cached) so we don't have to stand up an env.
    worker._TARGET_CACHE.clear()
    r = tools.dispatch(
        "get_effective_overlay",
        {"origin": "devel/foo"},
        env="never-seen-env",
    )
    assert r["ok"] is False
    assert "no compose target cached" in r["error"]


def test_get_effective_overlay_filtered_out_includes_op_payload(
    tmp_path: Path, monkeypatch,
) -> None:
    """Filtered ops carry their full payload (so the agent can still
    inspect what's out there for other build lines) in addition to
    the `reason` string."""
    worker._TARGET_CACHE.clear()
    worker.set_env_target("test", "@2026Q2")
    root = _seed_workspace(tmp_path)
    _stub_env_paths(monkeypatch, root)
    (root / "ports" / "devel" / "foo" / "overlay.dops").write_text(
        'target @any\nport devel/foo\ntype port\nreason "x"\n\n'
        'target @2026Q3\n'
        'mk set USES "meson"\n'
    )

    r = worker.get_effective_overlay("test", "devel/foo")
    assert len(r["filtered_out"]) == 1
    fop = r["filtered_out"][0]
    # Full payload, scope, kind, AND reason.
    assert fop["kind"] == "mk.var.set"
    assert fop["scope"] == "@2026Q3"
    assert fop.get("value") == "meson"
    assert "reason" in fop


# ---------------------------------------------------------------------
# Step 39a — drop_mk_directive renderer
# ---------------------------------------------------------------------


def _seed_overlay(t: Translator, body: str) -> None:
    """Write an overlay.dops with the standard @any header + `body`."""
    t.port_path("overlay.dops").write_text(
        "target @any\n"
        "port devel/foo\n"
        "type port\n"
        'reason "x"\n'
        "\n"
        + body
    )


def test_drop_mk_directive_removes_add_line(tmp_path: Path) -> None:
    """The core dmidecode case: an `mk add` the agent regrets is
    removed cleanly, leaving no add+remove residue."""
    t = _make_seeded_translator(tmp_path)
    _seed_overlay(t, 'mk add USES "alias"\nmk set GNU_CONFIGURE "yes"\n')
    r = t.apply({
        "type": "drop_mk_directive", "kind": "add",
        "key": "USES", "value": "alias",
    })
    assert r.ok, r.error
    written = t.port_path("overlay.dops").read_text()
    assert 'mk add USES "alias"' not in written
    # Unrelated line untouched.
    assert 'mk set GNU_CONFIGURE "yes"' in written


def test_drop_mk_directive_matches_unquoted_on_disk_form(
    tmp_path: Path,
) -> None:
    """Regression: convert emits whitespace-free `mk add` values
    bare (`mk add USES alias`), while change_makefile/drop emit the
    quoted form. The engine treats both identically, so the matcher
    must too — matching by parsed token value, not byte spelling.
    Before the fix, `drop_mk_directive(value="alias")` reconstructed
    `mk add USES "alias"` and missed the bare on-disk line, forcing
    the agent into an add+remove counter-directive thrash."""
    t = _make_seeded_translator(tmp_path)
    _seed_overlay(t, "mk add USES alias\nmk set GNU_CONFIGURE \"yes\"\n")
    r = t.apply({
        "type": "drop_mk_directive", "kind": "add",
        "key": "USES", "value": "alias",
    })
    assert r.ok, r.error
    written = t.port_path("overlay.dops").read_text()
    assert "mk add USES alias" not in written
    assert 'mk set GNU_CONFIGURE "yes"' in written


def test_drop_mk_directive_matches_line_with_on_missing_clause(
    tmp_path: Path,
) -> None:
    """A directive carrying a trailing `on-missing` clause parses to an
    MkOpNode whose `token` still equals the value — the clause lands in
    its own AST field. The parser-based matcher matches on action/var/
    token and removes the whole line, clause included."""
    t = _make_seeded_translator(tmp_path)
    _seed_overlay(t, "mk add USES alias on-missing error\n")
    r = t.apply({
        "type": "drop_mk_directive", "kind": "add",
        "key": "USES", "value": "alias",
    })
    assert r.ok, r.error
    assert "USES" not in t.port_path("overlay.dops").read_text()


def test_drop_mk_directive_removes_unset_line(tmp_path: Path) -> None:
    t = _make_seeded_translator(tmp_path)
    _seed_overlay(t, "mk unset LICENSE_FILE\n")
    r = t.apply({
        "type": "drop_mk_directive", "kind": "unset", "key": "LICENSE_FILE",
    })
    assert r.ok, r.error
    assert "mk unset LICENSE_FILE" not in t.port_path("overlay.dops").read_text()


def test_drop_mk_directive_set_matches_by_key_ignoring_value(
    tmp_path: Path,
) -> None:
    """`kind=set` matches on the key alone — the `value` field is
    ignored (and may be omitted), so the agent doesn't have to echo
    the exact on-disk value to remove a set line."""
    t = _make_seeded_translator(tmp_path)
    _seed_overlay(t, 'mk set PORTREVISION "1"\n')
    r = t.apply({
        "type": "drop_mk_directive", "kind": "set", "key": "PORTREVISION",
    })
    assert r.ok, r.error
    assert "PORTREVISION" not in t.port_path("overlay.dops").read_text()


def test_drop_mk_directive_set_prefix_is_key_boundary_safe(
    tmp_path: Path,
) -> None:
    """`kind=set key=USE` must NOT strip `mk set USES ...` — the key
    match is whole-token, not a substring prefix."""
    t = _make_seeded_translator(tmp_path)
    _seed_overlay(t, 'mk set USES "tar:xz"\n')
    r = t.apply({
        "type": "drop_mk_directive", "kind": "set", "key": "USE",
    })
    assert r.ok is False
    assert "no `" in (r.error or "")
    # USES line survives.
    assert 'mk set USES "tar:xz"' in t.port_path("overlay.dops").read_text()


def test_drop_mk_directive_zero_match_refuses(tmp_path: Path) -> None:
    """A line that doesn't exist is a signal the agent's model is
    wrong — refuse rather than silently no-op."""
    t = _make_seeded_translator(tmp_path)
    _seed_overlay(t, 'mk add USES "alias"\n')
    r = t.apply({
        "type": "drop_mk_directive", "kind": "unset", "key": "NOPE",
    })
    assert r.ok is False
    assert "no `mk unset NOPE`" in (r.error or "")


def test_drop_mk_directive_ambiguous_refuses_and_leaves_substrate(
    tmp_path: Path,
) -> None:
    """Two matching lines at the same scope → hard refuse, substrate
    untouched (no partial removal)."""
    t = _make_seeded_translator(tmp_path)
    before = 'mk add USES "alias"\nmk add USES "alias"\n'
    _seed_overlay(t, before)
    full_before = t.port_path("overlay.dops").read_text()
    r = t.apply({
        "type": "drop_mk_directive", "kind": "add",
        "key": "USES", "value": "alias",
    })
    assert r.ok is False
    assert "ambiguous" in (r.error or "")
    # Nothing removed.
    assert t.port_path("overlay.dops").read_text() == full_before


def test_drop_mk_directive_scope_filters_to_section(tmp_path: Path) -> None:
    """The same line under @any and @2026Q2: dropping @any leaves the
    @2026Q2 instance intact."""
    t = _make_seeded_translator(tmp_path)
    t.port_path("overlay.dops").write_text(
        "target @any\n"
        "port devel/foo\n"
        "type port\n"
        'reason "x"\n'
        "\n"
        'mk add USES "alias"\n'
        "\n"
        "target @2026Q2\n"
        'mk add USES "alias"\n'
    )
    r = t.apply({
        "type": "drop_mk_directive", "kind": "add",
        "key": "USES", "value": "alias", "scope": "@any",
    })
    assert r.ok, r.error
    written = t.port_path("overlay.dops").read_text()
    # Exactly one occurrence remains — the @2026Q2 one.
    assert written.count('mk add USES "alias"') == 1
    q_pos = written.index("target @2026Q2")
    assert written.index('mk add USES "alias"') > q_pos


def test_drop_mk_directive_current_resolves_to_t_target(
    tmp_path: Path,
) -> None:
    """`scope=@current` resolves to `t.target` and only strips within
    that build line's section."""
    t = _make_seeded_translator(tmp_path, target="@2026Q2")
    t.port_path("overlay.dops").write_text(
        "target @any\n"
        "port devel/foo\n"
        "type port\n"
        'reason "x"\n'
        "\n"
        'mk add USES "alias"\n'
        "\n"
        "target @2026Q2\n"
        'mk add USES "alias"\n'
    )
    r = t.apply({
        "type": "drop_mk_directive", "kind": "add",
        "key": "USES", "value": "alias", "scope": "@current",
    })
    assert r.ok, r.error
    written = t.port_path("overlay.dops").read_text()
    # The @any instance survives; only @2026Q2's was stripped.
    assert written.count('mk add USES "alias"') == 1
    any_section = written.split("target @2026Q2")[0]
    assert 'mk add USES "alias"' in any_section
    assert "@current" not in written


def test_drop_mk_directive_current_refused_when_no_target(
    tmp_path: Path,
) -> None:
    t = _make_seeded_translator(tmp_path)  # target=None
    _seed_overlay(t, 'mk add USES "alias"\n')
    r = t.apply({
        "type": "drop_mk_directive", "kind": "add",
        "key": "USES", "value": "alias", "scope": "@current",
    })
    assert r.ok is False
    assert "@current" in (r.error or "")
    assert "escalate" in (r.error or "")


def test_drop_mk_directive_skips_heredoc_body(tmp_path: Path) -> None:
    """A decoy `mk add USES "alias"` line inside an `mk target set`
    heredoc body must NOT be matched — only top-level directives."""
    t = _make_seeded_translator(tmp_path)
    t.port_path("overlay.dops").write_text(
        "target @any\n"
        "port devel/foo\n"
        "type port\n"
        'reason "x"\n'
        "\n"
        "mk target set dfly-patch <<MK\n"
        '\tmk add USES "alias"\n'
        "MK\n"
    )
    full_before = t.port_path("overlay.dops").read_text()
    r = t.apply({
        "type": "drop_mk_directive", "kind": "add",
        "key": "USES", "value": "alias",
    })
    # No top-level match → refuse, heredoc body untouched.
    assert r.ok is False
    assert t.port_path("overlay.dops").read_text() == full_before


def test_drop_mk_directive_no_overlay_refuses(tmp_path: Path) -> None:
    t = _make_seeded_translator(tmp_path)  # overlay.dops not created
    r = t.apply({
        "type": "drop_mk_directive", "kind": "unset", "key": "FOO",
    })
    assert r.ok is False
    assert "does not exist" in (r.error or "")


def test_drop_mk_directive_roundtrips_change_makefile(tmp_path: Path) -> None:
    """End-to-end: a `change_makefile op=append` then a matching
    `drop_mk_directive(kind=add)` leaves the overlay byte-identical to
    the pre-append state (no residue), and the result parses through
    the engine."""
    from dportsv3.engine.api import parse_dsl

    t = _make_seeded_translator(tmp_path)
    # First append seeds the overlay header + the line.
    t.apply({
        "type": "change_makefile", "path": "Makefile", "key": "USES",
        "op": "append", "value": "alias",
    })
    after_add = t.port_path("overlay.dops").read_text()
    assert 'mk add USES "alias"' in after_add

    r = t.apply({
        "type": "drop_mk_directive", "kind": "add",
        "key": "USES", "value": "alias",
    })
    assert r.ok, r.error
    after_drop = t.port_path("overlay.dops").read_text()
    assert 'mk add USES "alias"' not in after_drop
    assert parse_dsl(after_drop).ok


def test_drop_mk_directive_add_requires_value_schema() -> None:
    """Schema gate: kind=add/remove must carry `value`."""
    from dportsv3.agent.edit_intent.validator import parse_intent
    from dportsv3.agent.edit_intent.grammar import DropMkDirective

    ok = parse_intent({
        "type": "drop_mk_directive", "kind": "add",
        "key": "USES", "value": "alias",
    })
    assert isinstance(ok, DropMkDirective)
    try:
        parse_intent({
            "type": "drop_mk_directive", "kind": "add", "key": "USES",
        })
        raise AssertionError("expected schema refusal for add without value")
    except Exception as exc:  # IntentError
        assert "value" in str(exc).lower() or "required" in str(exc).lower()


# ---------------------------------------------------------------------
# Step 39b — drop_file renderer
# ---------------------------------------------------------------------


def test_drop_file_removes_copy_and_deletes_resource(tmp_path: Path) -> None:
    """`file copy` (kind=resource) install: the directive line is
    stripped AND the on-disk resource under ports/<origin>/ deleted."""
    t = _make_seeded_translator(tmp_path)
    (t.port_dir / "files").mkdir(parents=True)
    (t.port_dir / "files" / "m.dragonfly").write_text("hi\n")
    _seed_overlay(t, "file copy files/m.dragonfly -> files/m.dragonfly\n")
    r = t.apply({
        "type": "drop_file", "target": "files/m.dragonfly", "reason": "stale",
    })
    assert r.ok, r.error
    assert "file copy" not in t.port_path("overlay.dops").read_text()
    assert not (t.port_dir / "files" / "m.dragonfly").exists()
    # The deleted resource is reported, not just the overlay.
    assert "ports/devel/foo/files/m.dragonfly" in r.paths_changed


def test_drop_file_removes_materialize_no_ondisk_file(tmp_path: Path) -> None:
    """`file materialize ... -> <dest>` where the dest is a build-tree
    path (not a port-subtree file): line stripped, no unlink attempted,
    only the overlay reported."""
    t = _make_seeded_translator(tmp_path)
    _seed_overlay(t, "file materialize dragonfly/extra.h -> include/extra.h\n")
    r = t.apply({
        "type": "drop_file", "target": "include/extra.h", "reason": "stale",
    })
    assert r.ok, r.error
    assert "file materialize" not in t.port_path("overlay.dops").read_text()
    assert r.paths_changed == ["ports/devel/foo/overlay.dops"]


def test_drop_file_refuses_patch_shaped_target(tmp_path: Path) -> None:
    """Patch-shaped destinations are owned by drop_patch; drop_file
    refuses them so the two intents never overlap."""
    t = _make_seeded_translator(tmp_path)
    _seed_overlay(
        t, "file materialize dragonfly/patch-foo -> dragonfly/patch-foo\n",
    )
    r = t.apply({
        "type": "drop_file", "target": "dragonfly/patch-foo", "reason": "x",
    })
    assert r.ok is False
    assert "drop_patch" in (r.error or "")
    # Substrate untouched.
    assert "patch-foo" in t.port_path("overlay.dops").read_text()


def test_drop_file_zero_match_refuses(tmp_path: Path) -> None:
    t = _make_seeded_translator(tmp_path)
    _seed_overlay(t, "file copy files/a -> files/a\n")
    r = t.apply({
        "type": "drop_file", "target": "files/missing", "reason": "x",
    })
    assert r.ok is False
    assert "files/missing" in (r.error or "")


def test_drop_file_ambiguous_refuses_and_leaves_substrate(
    tmp_path: Path,
) -> None:
    t = _make_seeded_translator(tmp_path)
    _seed_overlay(
        t,
        "file copy files/a -> files/a\n"
        "file materialize src -> files/a\n",
    )
    full_before = t.port_path("overlay.dops").read_text()
    r = t.apply({
        "type": "drop_file", "target": "files/a", "reason": "x",
    })
    assert r.ok is False
    assert "ambiguous" in (r.error or "")
    assert t.port_path("overlay.dops").read_text() == full_before


def test_drop_file_scope_filters_to_section(tmp_path: Path) -> None:
    t = _make_seeded_translator(tmp_path)
    t.port_path("overlay.dops").write_text(
        "target @any\n"
        "port devel/foo\n"
        "type port\n"
        'reason "x"\n'
        "\n"
        "file copy files/a -> files/a\n"
        "\n"
        "target @2026Q2\n"
        "file copy files/a -> files/a\n"
    )
    r = t.apply({
        "type": "drop_file", "target": "files/a", "reason": "x",
        "scope": "@any",
    })
    assert r.ok, r.error
    written = t.port_path("overlay.dops").read_text()
    assert written.count("file copy files/a -> files/a") == 1
    assert written.index("file copy") > written.index("target @2026Q2")


def test_drop_file_current_resolves_to_t_target(tmp_path: Path) -> None:
    t = _make_seeded_translator(tmp_path, target="@2026Q2")
    t.port_path("overlay.dops").write_text(
        "target @any\n"
        "port devel/foo\n"
        "type port\n"
        'reason "x"\n'
        "\n"
        "file copy files/a -> files/a\n"
        "\n"
        "target @2026Q2\n"
        "file copy files/a -> files/a\n"
    )
    r = t.apply({
        "type": "drop_file", "target": "files/a", "reason": "x",
        "scope": "@current",
    })
    assert r.ok, r.error
    written = t.port_path("overlay.dops").read_text()
    assert written.count("file copy files/a -> files/a") == 1
    assert "file copy files/a -> files/a" in written.split("target @2026Q2")[0]
    assert "@current" not in written


def test_drop_file_current_refused_when_no_target(tmp_path: Path) -> None:
    t = _make_seeded_translator(tmp_path)  # target=None
    _seed_overlay(t, "file copy files/a -> files/a\n")
    r = t.apply({
        "type": "drop_file", "target": "files/a", "reason": "x",
        "scope": "@current",
    })
    assert r.ok is False
    assert "@current" in (r.error or "")
    assert "escalate" in (r.error or "")


def test_drop_file_no_overlay_refuses(tmp_path: Path) -> None:
    t = _make_seeded_translator(tmp_path)  # overlay.dops not created
    r = t.apply({
        "type": "drop_file", "target": "files/a", "reason": "x",
    })
    assert r.ok is False
    assert "does not exist" in (r.error or "")


def test_drop_file_roundtrips_add_file_resource(tmp_path: Path) -> None:
    """End-to-end: add_file kind=resource writes a file + emits
    `file copy`; a matching drop_file removes both, and the overlay
    parses through the engine afterward."""
    from dportsv3.engine.api import parse_dsl

    t = _make_seeded_translator(tmp_path)
    t.apply({
        "type": "add_file", "dest": "files/pkg-message.dragonfly",
        "kind": "resource", "content": "hello\n",
    })
    assert (t.port_dir / "files" / "pkg-message.dragonfly").is_file()
    assert "file copy" in t.port_path("overlay.dops").read_text()

    r = t.apply({
        "type": "drop_file", "target": "files/pkg-message.dragonfly",
        "reason": "obsolete",
    })
    assert r.ok, r.error
    after = t.port_path("overlay.dops").read_text()
    assert "file copy" not in after
    assert not (t.port_dir / "files" / "pkg-message.dragonfly").exists()
    assert parse_dsl(after).ok


# --- drop_target_block (Step 39c) ---------------------------------------

def test_drop_target_block_removes_set_block(tmp_path: Path) -> None:
    """A whole `mk target set NAME <<TAG ... TAG` block is removed —
    open line, body, and close tag — leaving unrelated lines intact."""
    t = _make_seeded_translator(tmp_path)
    _seed_overlay(
        t,
        "mk target set do-build <<MK\n"
        "\tcd ${WRKSRC} && make\n"
        "MK\n"
        'mk set CFLAGS "-O2"\n',
    )
    r = t.apply({
        "type": "drop_target_block", "block_name": "do-build",
        "reason": "obsolete recipe",
    })
    assert r.ok, r.error
    written = t.port_path("overlay.dops").read_text()
    assert "mk target set do-build" not in written
    assert "cd ${WRKSRC} && make" not in written
    assert "MK" not in written
    # Unrelated line survives.
    assert 'mk set CFLAGS "-O2"' in written


def test_drop_target_block_removes_append_block(tmp_path: Path) -> None:
    """`append`-action blocks are matched too, not just `set`."""
    t = _make_seeded_translator(tmp_path)
    _seed_overlay(
        t,
        "mk target append post-install <<MK\n"
        "\t${RM} ${STAGEDIR}/junk\n"
        "MK\n",
    )
    r = t.apply({
        "type": "drop_target_block", "block_name": "post-install",
        "reason": "x",
    })
    assert r.ok, r.error
    assert "post-install" not in t.port_path("overlay.dops").read_text()


def test_drop_target_block_zero_match_refuses(tmp_path: Path) -> None:
    """A block name that doesn't exist signals the agent's model is
    wrong — refuse rather than silently no-op."""
    t = _make_seeded_translator(tmp_path)
    _seed_overlay(t, "mk target set do-build <<MK\n\tmake\nMK\n")
    r = t.apply({
        "type": "drop_target_block", "block_name": "do-install",
        "reason": "x",
    })
    assert r.ok is False
    assert "no `mk target set/append do-install" in (r.error or "")


def test_drop_target_block_ambiguous_refuses_and_leaves_substrate(
    tmp_path: Path,
) -> None:
    """Two same-name blocks at the same scope → hard refuse, substrate
    untouched (no partial removal)."""
    t = _make_seeded_translator(tmp_path)
    _seed_overlay(
        t,
        "mk target set do-build <<MK\n\ta\nMK\n"
        "mk target append do-build <<MK\n\tb\nMK\n",
    )
    before = t.port_path("overlay.dops").read_text()
    r = t.apply({
        "type": "drop_target_block", "block_name": "do-build",
        "reason": "x",
    })
    assert r.ok is False
    assert "ambiguous" in (r.error or "")
    assert t.port_path("overlay.dops").read_text() == before


def test_drop_target_block_scope_filters_to_section(tmp_path: Path) -> None:
    """Same block name under @any and @2026Q2: dropping @any leaves the
    @2026Q2 block intact. This is the property replace_in_dops_block
    lacks (its scope-blindness is parked for Step 40d)."""
    t = _make_seeded_translator(tmp_path)
    t.port_path("overlay.dops").write_text(
        "target @any\n"
        "port devel/foo\n"
        "type port\n"
        'reason "x"\n'
        "\n"
        "mk target set do-build <<MK\n"
        "\tmake\n"
        "MK\n"
        "\n"
        "target @2026Q2\n"
        "mk target set do-build <<MK\n"
        "\tgmake\n"
        "MK\n"
    )
    r = t.apply({
        "type": "drop_target_block", "block_name": "do-build",
        "reason": "x", "scope": "@any",
    })
    assert r.ok, r.error
    written = t.port_path("overlay.dops").read_text()
    # Only the @2026Q2 block survives.
    assert written.count("mk target set do-build") == 1
    assert "gmake" in written
    assert "make\n" not in written.split("target @2026Q2")[0]


def test_drop_target_block_current_resolves_to_t_target(
    tmp_path: Path,
) -> None:
    t = _make_seeded_translator(tmp_path, target="@2026Q2")
    t.port_path("overlay.dops").write_text(
        "target @any\n"
        "port devel/foo\n"
        "type port\n"
        'reason "x"\n'
        "\n"
        "mk target set do-build <<MK\n"
        "\tmake\n"
        "MK\n"
        "\n"
        "target @2026Q2\n"
        "mk target set do-build <<MK\n"
        "\tgmake\n"
        "MK\n"
    )
    r = t.apply({
        "type": "drop_target_block", "block_name": "do-build",
        "reason": "x", "scope": "@current",
    })
    assert r.ok, r.error
    written = t.port_path("overlay.dops").read_text()
    # The @2026Q2 block went; the @any one stays.
    assert written.count("mk target set do-build") == 1
    assert "make" in written.split("target @2026Q2")[0]
    assert "gmake" not in written


def test_drop_target_block_current_refused_when_no_target(
    tmp_path: Path,
) -> None:
    t = _make_seeded_translator(tmp_path)  # target=None
    _seed_overlay(t, "mk target set do-build <<MK\n\tmake\nMK\n")
    r = t.apply({
        "type": "drop_target_block", "block_name": "do-build",
        "reason": "x", "scope": "@current",
    })
    assert r.ok is False
    assert "@current" in (r.error or "")
    assert "escalate" in (r.error or "")


def test_drop_target_block_no_overlay_refuses(tmp_path: Path) -> None:
    t = _make_seeded_translator(tmp_path)  # overlay.dops not created
    r = t.apply({
        "type": "drop_target_block", "block_name": "do-build",
        "reason": "x",
    })
    assert r.ok is False
    assert "does not exist" in (r.error or "")


def test_drop_target_block_unbounded_block_refuses(tmp_path: Path) -> None:
    """A heredoc that opens but never closes is a corrupt overlay —
    refuse rather than removing to EOF on a guess."""
    t = _make_seeded_translator(tmp_path)
    _seed_overlay(t, "mk target set do-build <<MK\n\tmake with no close tag\n")
    r = t.apply({
        "type": "drop_target_block", "block_name": "do-build",
        "reason": "x",
    })
    assert r.ok is False
    assert "corrupt" in (r.error or "")


def test_drop_target_block_roundtrips_through_engine(tmp_path: Path) -> None:
    """After removing a block the remaining overlay parses cleanly."""
    from dportsv3.engine.api import parse_dsl

    t = _make_seeded_translator(tmp_path)
    _seed_overlay(
        t,
        "mk target set do-build <<MK\n"
        "\tcd ${WRKSRC} && make\n"
        "MK\n"
        'mk set CFLAGS "-O2"\n',
    )
    r = t.apply({
        "type": "drop_target_block", "block_name": "do-build",
        "reason": "x",
    })
    assert r.ok, r.error
    assert parse_dsl(t.port_path("overlay.dops").read_text()).ok
