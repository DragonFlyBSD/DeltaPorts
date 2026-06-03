"""Step 38a — target-scope plumbing through the intent layer.

The dops engine has supported per-target scoping end-to-end since the
grammar landed, but the intent layer ignored the dimension entirely —
the Translator constructor took no target, no schema carried scope,
every renderer appended at EOF under whatever the file's last
`target @X` directive was (in practice always `target @any`).

Step 38a is the minimum-viable plumbing: the Translator gains an
optional `target` kwarg, `worker` keeps a per-env target cache the
runner populates at attempt start (`runner.process_patch_job` /
`process_convert_job`), and `worker.apply_intent` threads the
cached value into the Translator at construction time. Renderers
ignore `t.target` until Step 38b wires the scope vocabulary; this
file pins the storage-and-routing surface so 38b can build on it
without re-litigating the plumbing.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from dportsv3.agent import worker
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
