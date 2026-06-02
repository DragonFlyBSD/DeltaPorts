"""Per-intent state-gate relaxation for `not_in_scope` ports.

The patch agent's `apply_intent` previously refused unconditionally
when `assess_dops` returned `not_in_scope` — meaning a vanilla
FreeBSD port that needs its first DragonFly patch couldn't be
serviced by the intent surface. The agent had to fall back to the
legacy `dupe`/`genpatch` path (which produces empty changes.diff)
or escalate MANUAL.

Fix: split the seven intents into creation vs modification. Creation
intents (add_patch / add_file / change_makefile / bump_portrevision)
naturally bootstrap overlay.dops via `_append_overlay`'s header
fallback on first write, so they're allowed on `not_in_scope`.
Modification intents (replace_in_patch / drop_patch /
replace_in_dops_block) still refuse — they presuppose existing
substrate to modify.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from dportsv3.agent import worker
from dportsv3.agent.overlay_state import OverlayAssessment, OverlayRuleResult


def _make_workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "DeltaPorts"
    ws.mkdir()
    subprocess.run(["git", "-C", str(ws), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(ws), "config", "user.email", "t@t"],
                   check=True)
    subprocess.run(["git", "-C", str(ws), "config", "user.name", "t"],
                   check=True)
    (ws / "README").write_text("baseline\n")
    subprocess.run(["git", "-C", str(ws), "add", "README"], check=True)
    subprocess.run(["git", "-C", str(ws), "commit", "-qm", "init"],
                   check=True)
    return ws


def _stub_assess(state="not_in_scope", action="proceed_triage"):
    return OverlayAssessment(
        state=state, action=action,
        rules=(OverlayRuleResult("test_stub"),),
        reasons=("stubbed for test",),
    )


@pytest.fixture
def env_not_in_scope(tmp_path, monkeypatch):
    """A workspace with a port subtree that has no overlay yet —
    the classic skalibs-shaped starting state."""
    ws = _make_workspace(tmp_path)
    # Create an empty port subtree but no overlay.dops / dragonfly/.
    port = ws / "ports/devel/foo"
    port.mkdir(parents=True)
    (port / "Makefile").write_text("# vanilla FreeBSD port\n")
    subprocess.run(["git", "-C", str(ws), "add", "ports"], check=True)
    subprocess.run(["git", "-C", str(ws), "commit", "-qm", "port"],
                   check=True)

    paths = SimpleNamespace(env_dir=tmp_path, writable=tmp_path,
                            deltaports=ws)
    monkeypatch.setattr(worker, "env_paths", lambda env: paths)
    monkeypatch.setattr(worker, "assess_dops",
                        lambda env, origin: _stub_assess())
    worker._INTENT_LOGS.clear()
    return ws


# ---------------------------------------------------------------------
# Creation intents are allowed on not_in_scope (bootstrap path)
# ---------------------------------------------------------------------


def test_add_patch_inline_bootstraps_overlay(env_not_in_scope):
    """add_patch with an inline diff lands; overlay.dops is created
    with the minimal header + a `patch apply` directive."""
    ws = env_not_in_scope
    result = worker.apply_intent("test-env", "devel/foo", {
        "type": "add_patch",
        "target": "dragonfly/patch-src_main.c",
        "diff": "--- src/main.c.orig\n+++ src/main.c\n@@ -1,1 +1,1 @@\n-old\n+new\n",
    })
    assert result["ok"] is True, result
    assert result["intent_type"] == "add_patch"
    assert result["mode"] == "dops"

    overlay = ws / "ports/devel/foo/overlay.dops"
    assert overlay.is_file()
    text = overlay.read_text()
    # Initial header was synthesized.
    assert "port devel/foo" in text
    assert "type port" in text
    # The new patch-apply directive is present.
    assert "patch apply dragonfly/patch-src_main.c" in text


def test_change_makefile_set_bootstraps_overlay(env_not_in_scope):
    """change_makefile op=set lands on not_in_scope; overlay.dops
    created with header + a single mk-set directive."""
    ws = env_not_in_scope
    result = worker.apply_intent("test-env", "devel/foo", {
        "type": "change_makefile",
        "path": "Makefile",
        "key": "USES",
        "value": "ssl",
        "op": "set",
    })
    assert result["ok"] is True, result
    assert result["mode"] == "dops"

    overlay_text = (ws / "ports/devel/foo/overlay.dops").read_text()
    assert "port devel/foo" in overlay_text
    assert 'mk set USES "ssl"' in overlay_text


def test_bump_portrevision_bootstraps_overlay(env_not_in_scope):
    ws = env_not_in_scope
    result = worker.apply_intent("test-env", "devel/foo", {
        "type": "bump_portrevision",
    })
    assert result["ok"] is True, result
    assert result["mode"] == "dops"

    overlay = ws / "ports/devel/foo/overlay.dops"
    assert overlay.is_file()
    assert "port devel/foo" in overlay.read_text()


def test_add_file_resource_bootstraps_overlay(env_not_in_scope):
    """add_file (kind=resource) inline content lands; overlay.dops
    created with header + a `file copy` directive."""
    ws = env_not_in_scope
    result = worker.apply_intent("test-env", "devel/foo", {
        "type": "add_file",
        "dest": "files/extra-patch-foo",
        "kind": "resource",
        "content": "--- a\n+++ b\n",
    })
    assert result["ok"] is True, result
    assert result["mode"] == "dops"
    overlay = ws / "ports/devel/foo/overlay.dops"
    assert overlay.is_file()
    assert "port devel/foo" in overlay.read_text()


# ---------------------------------------------------------------------
# Modification intents still refuse on not_in_scope
# ---------------------------------------------------------------------


@pytest.mark.parametrize("intent", [
    {"type": "replace_in_patch", "target": "dragonfly/patch-x",
     "find": "a", "replace": "b"},
    {"type": "drop_patch", "target": "dragonfly/patch-x",
     "reason": "obsolete"},
    {"type": "replace_in_dops_block", "block_name": "dfly-patch",
     "find": "a", "replace": "b"},
])
def test_modification_intents_still_refuse_on_not_in_scope(
    env_not_in_scope, intent,
):
    result = worker.apply_intent("test-env", "devel/foo", intent)
    assert result["ok"] is False
    assert result["blocked_by"] == "state:not_in_scope"
    # New refusal message names the intent type and points to the
    # creation intents that would scaffold the overlay first.
    err = result["error"]
    assert intent["type"] in err, err
    assert "not_in_scope" in err
    assert "add_patch" in err  # actionable pointer to creation intents


# ---------------------------------------------------------------------
# Other refused states unchanged
# ---------------------------------------------------------------------


@pytest.mark.parametrize("bad_state", [
    "auto_safe_pending", "needs_judgment", "stale",
])
def test_other_states_refuse_with_convert_routing_message(
    env_not_in_scope, monkeypatch, bad_state,
):
    """States other than `not_in_scope` and `converted` still
    refuse with the convert-routing message — they should have been
    handled upstream by the triage→convert deferral."""
    monkeypatch.setattr(worker, "assess_dops",
                        lambda e, o: _stub_assess(state=bad_state))
    result = worker.apply_intent("test-env", "devel/foo", {
        "type": "add_patch", "target": "dragonfly/patch-x",
        "diff": "--- a\n+++ b\n",
    })
    assert result["ok"] is False
    assert result["blocked_by"] == f"state:{bad_state}"
    # The non-not_in_scope branch routes through the
    # convert-or-MANUAL message, not the scaffold-creation pointer.
    assert "convert" in result["error"]


# ---------------------------------------------------------------------
# Followup intent works against the now-bootstrapped overlay
# ---------------------------------------------------------------------


def test_creation_then_modification_in_sequence(env_not_in_scope, monkeypatch):
    """Once a creation intent has bootstrapped overlay.dops, the
    port is dops-mode and modification intents should work on it.

    Simulates the realistic flow: agent adds a patch on a fresh
    port, then immediately wants to make a follow-up edit (here, a
    change_makefile). Between the two calls, the stub assess_dops
    must flip from not_in_scope → converted to reflect the substrate
    change.

    Note: replace_in_patch on a dragonfly/ target is refused by the
    validator — patch files are output artifacts; to update a patch
    use drop_patch + add_patch, not replace_in_patch. The follow-up
    here is change_makefile so the test exercises the substrate-gate
    flip without depending on the dragonfly/ refusal rule.
    """
    state = {"value": "not_in_scope"}
    monkeypatch.setattr(
        worker, "assess_dops",
        lambda e, o: _stub_assess(state=state["value"]),
    )

    r1 = worker.apply_intent("test-env", "devel/foo", {
        "type": "add_patch",
        "target": "dragonfly/patch-src_foo.c",
        "diff": "--- a/src/foo.c\n+++ b/src/foo.c\n@@ -1 +1 @@\n-x\n+y\n",
    })
    assert r1["ok"] is True

    # Substrate has flipped — simulate that.
    state["value"] = "converted"

    r2 = worker.apply_intent("test-env", "devel/foo", {
        "type": "change_makefile",
        "path": "Makefile",
        "key": "USES",
        "value": "pkgconfig",
        "op": "append",
    })
    assert r2["ok"] is True, r2
    assert r2["intent_type"] == "change_makefile"


# ---------------------------------------------------------------------
# Intent type via JSON-string form (edge case in apply_intent signature)
# ---------------------------------------------------------------------


def test_creation_intent_as_json_string_also_bootstraps(env_not_in_scope):
    """apply_intent's signature is `intent: dict | str`. The string
    form is rare but supported; the new gate must extract the type
    from either form."""
    import json
    payload = json.dumps({
        "type": "bump_portrevision",
    })
    result = worker.apply_intent("test-env", "devel/foo", payload)
    assert result["ok"] is True, result
    assert result["intent_type"] == "bump_portrevision"
