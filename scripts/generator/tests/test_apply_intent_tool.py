"""Tests for the apply_intent + intent_reference worker tools and
the DP_HARNESS_PATCH_USE_INTENT gate (plan Step 25c).

The tool layer is thin: apply_intent wraps the Translator from
25b, intent_reference wraps schema_for. The tests assert the
wire-format contract (what the LLM sees), the gate, and the
substrate-state guards that worker.apply_intent layers on top.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from dportsv3.agent import tools, worker
from dportsv3.agent.overlay_state import (
    OverlayAssessment, OverlayFacts, OverlayRuleResult,
)


# --------------------------------------------------------------------
# Fixture: a real workspace + monkeypatched worker.env_paths /
# worker.assess_dops so apply_intent runs end-to-end without a
# real chroot.
# --------------------------------------------------------------------


def _make_workspace(tmp_path: Path, origin: str = "devel/foo") -> Path:
    ws = tmp_path / "DeltaPorts"
    port = ws / "ports" / origin
    port.mkdir(parents=True)
    subprocess.run(["git", "-C", str(ws), "init", "-q", "-b", "main"],
                   check=True)
    subprocess.run(["git", "-C", str(ws), "config", "user.email", "t@t"],
                   check=True)
    subprocess.run(["git", "-C", str(ws), "config", "user.name", "t"],
                   check=True)
    (ws / "README").write_text("baseline\n")
    subprocess.run(["git", "-C", str(ws), "add", "README"], check=True)
    subprocess.run(["git", "-C", str(ws), "commit", "-qm", "init"],
                   check=True)
    return ws


def _stub_assess(state: str = "auto_safe_pending",
                 action: str = "defer_to_convert"):
    """Build an OverlayAssessment stub for worker.assess_dops."""
    return OverlayAssessment(
        state=state, action=action,
        rules=(OverlayRuleResult("test_stub"),),
        reasons=("stubbed for test",),
    )


@pytest.fixture
def env_with_workspace(tmp_path, monkeypatch):
    """A fake env named 'test-env' whose deltaports root is a real
    git repo at tmp_path/DeltaPorts. worker.env_paths and
    worker.assess_dops are stubbed so apply_intent operates fully
    in tmp_path without touching a real chroot."""
    ws = _make_workspace(tmp_path)
    paths = SimpleNamespace(
        env_dir=tmp_path, writable=tmp_path,
        deltaports=ws,
    )
    monkeypatch.setattr(worker, "env_paths", lambda env: paths)
    # Default assess: auto_safe_pending → compat mode (the common
    # patch-target shape).
    monkeypatch.setattr(worker, "assess_dops",
                        lambda env, origin: _stub_assess())
    # Drain any leftover log so the mode-drift guard doesn't
    # refuse this test based on the previous test's transaction
    # mode (tests share process state via worker._INTENT_LOGS).
    worker._INTENT_LOGS.clear()
    return ws


# --------------------------------------------------------------------
# apply_intent — happy paths
# --------------------------------------------------------------------


class TestApplyIntentHappy:

    def test_drop_patch_compat_mode(self, env_with_workspace, tmp_path):
        ws = env_with_workspace
        # Stage a patch file so drop_patch has something to remove.
        patch = ws / "ports/devel/foo/dragonfly/patch-old.c"
        patch.parent.mkdir(parents=True)
        patch.write_text("--- a/x\n+++ b/x\n")
        subprocess.run(
            ["git", "-C", str(ws), "add", str(patch.relative_to(ws))],
            check=True,
        )
        subprocess.run(["git", "-C", str(ws), "commit", "-qm", "add"],
                       check=True)

        result = worker.apply_intent("test-env", "devel/foo", {
            "type": "drop_patch",
            "target": "dragonfly/patch-old.c",
            "reason": "obsolete",
        })
        assert result["ok"] is True
        assert result["intent_type"] == "drop_patch"
        assert result["mode"] == "compat"
        assert not patch.exists()
        assert "deleted file" in result["substrate_diff"]

    def test_replace_in_patch_in_dops_mode(
        self, env_with_workspace, monkeypatch,
    ):
        # Flip assess_dops to return "converted" → dops mode.
        monkeypatch.setattr(worker, "assess_dops",
                            lambda e, o: _stub_assess(state="converted",
                                                     action="proceed_triage"))
        result = worker.apply_intent("test-env", "devel/foo", {
            "type": "replace_in_patch",
            "target": "dragonfly/patch-foo.c",
            "find": "OLD", "replace": "NEW",
        })
        assert result["ok"] is True
        assert result["mode"] == "dops"
        # In dops mode this writes overlay.dops.
        overlay = env_with_workspace / "ports/devel/foo/overlay.dops"
        assert overlay.exists()
        assert "text.replace_once" in overlay.read_text()


# --------------------------------------------------------------------
# apply_intent — refusal paths
# --------------------------------------------------------------------


class TestApplyIntentRefusals:

    def test_refuses_when_workspace_missing(self, monkeypatch, tmp_path):
        paths = SimpleNamespace(deltaports=tmp_path / "nope",
                                env_dir=tmp_path, writable=tmp_path)
        monkeypatch.setattr(worker, "env_paths", lambda e: paths)
        result = worker.apply_intent("test-env", "devel/foo", {
            "type": "drop_patch", "target": "x", "reason": "y",
        })
        assert result["ok"] is False
        assert "workspace not found" in result["stderr_tail"]

    def test_refuses_substrate_in_half_migrated_state(
        self, env_with_workspace, monkeypatch,
    ):
        """Surface_invariant should block intent transactions before
        the translator even sees them."""
        bad = OverlayAssessment(
            state="needs_judgment",
            action="surface_invariant",
            rules=(OverlayRuleResult("dops_with_unmigrated_makefile_dragonfly",
                                     severity="conversion_blocker"),),
            invariant_violations=("dops_with_unmigrated_makefile_dragonfly",),
            unmigrated_artifacts=("Makefile.DragonFly",),
        )
        monkeypatch.setattr(worker, "assess_dops",
                            lambda e, o: bad)
        result = worker.apply_intent("test-env", "devel/foo", {
            "type": "drop_patch", "target": "x", "reason": "y",
        })
        assert result["ok"] is False
        assert result["blocked_by"] == "substrate_invariant"
        assert "Makefile.DragonFly" in result["unmigrated_artifacts"]

    def test_refuses_not_in_scope(self, env_with_workspace, monkeypatch):
        monkeypatch.setattr(worker, "assess_dops",
                            lambda e, o: _stub_assess(state="not_in_scope",
                                                     action="proceed_triage"))
        result = worker.apply_intent("test-env", "devel/foo", {
            "type": "drop_patch", "target": "x", "reason": "y",
        })
        assert result["ok"] is False
        assert "not_in_scope" in result["blocked_by"]

    def test_refuses_invalid_intent_via_translator(self, env_with_workspace):
        # Translator-side validation: parse_intent raises IntentError
        # which the translator converts to ok=False.
        result = worker.apply_intent("test-env", "devel/foo", {
            "type": "drop_patch",
            "target": "x",
            # missing required 'reason'
        })
        assert result["ok"] is False
        assert "failed schema" in result["error"]

    def test_refuses_unknown_intent_type(self, env_with_workspace):
        result = worker.apply_intent("test-env", "devel/foo", {
            "type": "bogus_intent",
        })
        assert result["ok"] is False
        assert "unknown or missing" in result["error"]


# --------------------------------------------------------------------
# intent_reference
# --------------------------------------------------------------------


class TestIntentReference:

    def test_returns_schema_for_known_type(self):
        result = worker.intent_reference("test-env", "drop_patch")
        assert result["ok"] is True
        assert result["intent_type"] == "drop_patch"
        assert result["schema"]["title"] == "drop_patch"

    def test_unknown_type_returns_known_list(self):
        result = worker.intent_reference("test-env", "nope")
        assert result["ok"] is False
        assert "drop_patch" in result["known_intent_types"]
        assert "replace_in_patch" in result["known_intent_types"]


# --------------------------------------------------------------------
# Registry + gate
# --------------------------------------------------------------------


class TestRegistryGate:

    def test_schemas_include_intent_tools(self):
        # The schemas registry always carries both tools.
        all_names = tools.names()
        assert "apply_intent" in all_names
        assert "intent_reference" in all_names

    def test_patch_tool_names_default_excludes_intent_tools(
        self, monkeypatch,
    ):
        monkeypatch.delenv("DP_HARNESS_PATCH_USE_INTENT", raising=False)
        s = tools.patch_tool_names()
        assert "apply_intent" not in s
        assert "intent_reference" not in s
        # Original patch surface still present.
        assert "put_file" in s
        assert "install_patches" in s

    def test_patch_tool_names_with_flag_includes_intent_tools(
        self, monkeypatch,
    ):
        monkeypatch.setenv("DP_HARNESS_PATCH_USE_INTENT", "1")
        s = tools.patch_tool_names()
        assert "apply_intent" in s
        assert "intent_reference" in s

    @pytest.mark.parametrize("value", ["true", "yes", "on", "1", "TRUE"])
    def test_patch_tool_names_flag_truthy_variants(self, monkeypatch, value):
        monkeypatch.setenv("DP_HARNESS_PATCH_USE_INTENT", value)
        assert "apply_intent" in tools.patch_tool_names()

    @pytest.mark.parametrize("value", ["0", "false", "no", "off", ""])
    def test_patch_tool_names_flag_falsy_variants(self, monkeypatch, value):
        monkeypatch.setenv("DP_HARNESS_PATCH_USE_INTENT", value)
        assert "apply_intent" not in tools.patch_tool_names()

    def test_patch_use_intent_enabled_helper_unset(self, monkeypatch):
        """The shared gate helper (Step 25d-1) — used by both the
        tool-registry filter and the patch-flow lifecycle hooks.
        Default: OFF."""
        monkeypatch.delenv("DP_HARNESS_PATCH_USE_INTENT", raising=False)
        assert tools.patch_use_intent_enabled() is False

    @pytest.mark.parametrize("value", ["1", "true", "yes", "on", "TRUE", "Yes"])
    def test_patch_use_intent_enabled_helper_truthy(self, monkeypatch, value):
        monkeypatch.setenv("DP_HARNESS_PATCH_USE_INTENT", value)
        assert tools.patch_use_intent_enabled() is True

    @pytest.mark.parametrize("value", ["0", "false", "no", "off", "", "random"])
    def test_patch_use_intent_enabled_helper_falsy(self, monkeypatch, value):
        monkeypatch.setenv("DP_HARNESS_PATCH_USE_INTENT", value)
        assert tools.patch_use_intent_enabled() is False


# --------------------------------------------------------------------
# Step 25d-2: prompt selection + intent-gate put_file guardrail
# --------------------------------------------------------------------


class TestPatchPromptSelection:

    def test_patch_run_uses_legacy_prompt_when_gate_off(
        self, monkeypatch,
    ):
        """patch.run threads the system_prompt to attempt_loop.run.
        Default (gate off) is the legacy PATCH_SYSTEM."""
        from dportsv3.agent import patch, prompts, attempt_loop
        monkeypatch.delenv("DP_HARNESS_PATCH_USE_INTENT", raising=False)

        captured: dict = {}
        def _fake_run(payload, *, system_prompt=None, **kw):
            captured["system_prompt"] = system_prompt
            captured["tool_whitelist"] = kw.get("tool_whitelist")
            from dportsv3.agent.attempt_loop import PatchResult, Usage
            return PatchResult(status="success", final_text="",
                               proof={}, attempts=[], usage=Usage())
        monkeypatch.setattr(attempt_loop, "run", _fake_run)
        from dportsv3.agent.policy import Tier
        tier = Tier(name="AUTO", max_iterations=2, max_tokens=30000)
        patch.run(
            "(payload)", tier=tier, env="e", model="m",
        )
        assert captured["system_prompt"] is prompts.PATCH_SYSTEM
        assert "apply_intent" not in captured["tool_whitelist"]

    def test_patch_run_uses_intent_prompt_when_gate_on(
        self, monkeypatch,
    ):
        from dportsv3.agent import patch, prompts, attempt_loop
        monkeypatch.setenv("DP_HARNESS_PATCH_USE_INTENT", "1")

        captured: dict = {}
        def _fake_run(payload, *, system_prompt=None, **kw):
            captured["system_prompt"] = system_prompt
            captured["tool_whitelist"] = kw.get("tool_whitelist")
            from dportsv3.agent.attempt_loop import PatchResult, Usage
            return PatchResult(status="success", final_text="",
                               proof={}, attempts=[], usage=Usage())
        monkeypatch.setattr(attempt_loop, "run", _fake_run)
        from dportsv3.agent.policy import Tier
        tier = Tier(name="AUTO", max_iterations=2, max_tokens=30000)
        patch.run("(payload)", tier=tier, env="e", model="m")
        assert captured["system_prompt"] is prompts.PATCH_INTENT_SYSTEM
        # Intent tools visible.
        assert "apply_intent" in captured["tool_whitelist"]
        assert "intent_reference" in captured["tool_whitelist"]
        # Legacy port-subtree write tools NOT visible.
        assert "install_patches" not in captured["tool_whitelist"]
        assert "validate_dops" not in captured["tool_whitelist"]
        assert "emit_diff" not in captured["tool_whitelist"]
        assert "dops_reference" not in captured["tool_whitelist"]
        # put_file STAYS visible — WRKSRC writes via dupe/genpatch
        # need it. Worker-side guardrail handles port-subtree case.
        assert "put_file" in captured["tool_whitelist"]


class TestPutFileIntentGuardrail:
    """Step 25d-2 worker guardrail: when the intent gate is ON,
    put_file refuses port-subtree writes so the agent routes
    through apply_intent instead of bypassing the intent log."""

    def test_port_subtree_write_refused_when_gate_on(
        self, monkeypatch,
    ):
        from dportsv3.agent.worker import _reject_intent_path_put_file
        monkeypatch.setenv("DP_HARNESS_PATCH_USE_INTENT", "1")
        r = _reject_intent_path_put_file(
            "/work/DeltaPorts/ports/devel/foo/dragonfly/patch-x.c",
        )
        assert r is not None
        assert r["ok"] is False
        assert r["blocked_by"] == "intent_gate_port_subtree_write"
        assert "apply_intent" in r["error"]

    def test_port_subtree_write_allowed_when_gate_off(
        self, monkeypatch,
    ):
        from dportsv3.agent.worker import _reject_intent_path_put_file
        monkeypatch.delenv("DP_HARNESS_PATCH_USE_INTENT", raising=False)
        r = _reject_intent_path_put_file(
            "/work/DeltaPorts/ports/devel/foo/dragonfly/patch-x.c",
        )
        assert r is None

    def test_wrksrc_write_allowed_when_gate_on(self, monkeypatch):
        """WRKSRC writes via the dupe/genpatch flow stay legal so
        the agent can still snapshot + edit + genpatch + emit
        add_patch{from_dupe=true}."""
        from dportsv3.agent.worker import _reject_intent_path_put_file
        monkeypatch.setenv("DP_HARNESS_PATCH_USE_INTENT", "1")
        r = _reject_intent_path_put_file(
            "/work/obj/devel/foo/work/foo-1.2/src/main.c",
        )
        assert r is None

    def test_lock_root_write_still_refused_by_other_guard(
        self, monkeypatch,
    ):
        """The new gate doesn't disturb the existing lock-root /
        compose-root guardrails — those fire independently."""
        from dportsv3.agent.worker import _reject_dports_write
        r = _reject_dports_write("/work/DPorts/devel/foo/Makefile")
        assert r is not None
        assert "lock root" in r["error"]


# --------------------------------------------------------------------
# dispatch
# --------------------------------------------------------------------


class TestDispatch:

    def test_dispatch_apply_intent(self, env_with_workspace):
        result = tools.dispatch("apply_intent",
                                {"origin": "devel/foo", "intent": {
                                    "type": "drop_patch",
                                    "target": "missing.c",
                                    "reason": "x",
                                }},
                                env="test-env")
        # Missing patch file → ok=False with informative error
        # (worker.apply_intent doesn't raise; surfaces via dispatch).
        assert result["ok"] is False
        assert "does not exist" in (result.get("error") or "")

    def test_dispatch_intent_reference(self):
        result = tools.dispatch(
            "intent_reference", {"intent_type": "drop_patch"},
            env="test-env",
        )
        assert result["ok"] is True
        assert result["schema"]["title"] == "drop_patch"

    def test_dispatch_apply_intent_missing_required_origin(self):
        result = tools.dispatch(
            "apply_intent",
            {"intent": {"type": "drop_patch", "target": "x", "reason": "y"}},
            env="test-env",
        )
        assert result["ok"] is False
        assert "missing required" in result["error"]
