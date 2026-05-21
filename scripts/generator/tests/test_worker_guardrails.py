"""Step 12 — worker-level guardrails against observed agent thrashing.

Smoke-surfaced patterns the prompt warned against but weaker models
violated anyway. These tests pin the worker-side enforcement so the
model sees a structured error result and adapts on the next turn.
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
    """A minimal env-paths layout so worker tools can locate
    writable/, deltaports/, etc. without a real dev-env mount."""
    from dportsv3.agent import worker

    writable = tmp_path / "writable"
    deltaports = writable / "work" / "DeltaPorts"
    dports = writable / "work" / "DPorts"
    obj = writable / "work" / "obj"
    dsynth_template = writable / "work" / "dsynth" / "build" / "Template"
    for d in (writable, deltaports, dports, obj, dsynth_template):
        d.mkdir(parents=True)

    # EnvPaths only carries env_dir + writable; deltaports/freebsd_ports
    # are derived properties off ``writable``.
    fake_paths = worker.EnvPaths(env_dir=tmp_path, writable=writable)
    monkeypatch.setattr(worker, "env_paths", lambda env: fake_paths)
    return writable


# --- put_file refuses /work/DPorts/ ---------------------------------------


def test_put_file_refuses_dports_root(env_dir):
    from dportsv3.agent import worker
    res = worker.put_file("env", "/work/DPorts/devel/foo/Makefile", "X")
    assert res["ok"] is False
    assert res["kind"] == "dports_write_refused"
    assert "/work/DeltaPorts" in res["error"]
    assert "materialize_dports" in res["error"]


def test_put_file_refuses_dports_deep_path(env_dir):
    from dportsv3.agent import worker
    res = worker.put_file(
        "env",
        "/work/DPorts/devel/foo/dragonfly/patch-Makefile.in",
        "diff body",
    )
    assert res["ok"] is False
    assert res["kind"] == "dports_write_refused"
    # The on-disk file is NOT created.
    target = env_dir / "work" / "DPorts" / "devel" / "foo" / "dragonfly" / \
        "patch-Makefile.in"
    assert not target.exists()


def test_put_file_allows_deltaports_root(env_dir):
    from dportsv3.agent import worker
    res = worker.put_file(
        "env",
        "/work/DeltaPorts/ports/devel/foo/dragonfly/patch-Makefile.in",
        "@@ -1 +1 @@\n",
    )
    assert res.get("ok") is not False
    assert res["sha256"]
    target = env_dir / "work" / "DeltaPorts" / "ports" / "devel" / "foo" / \
        "dragonfly" / "patch-Makefile.in"
    assert target.is_file()
    assert target.read_text() == "@@ -1 +1 @@\n"


def test_put_file_refusal_does_not_leak_into_other_paths(env_dir):
    """``/work/DPortsLookalike`` (not literally ``/work/DPorts/``)
    must still be allowed — the guard matches the exact prefix."""
    from dportsv3.agent import worker
    res = worker.put_file(
        "env",
        "/work/DeltaPorts/ports/devel/foo/.dpconfig",
        "ok",
    )
    assert res.get("ok") is not False


# --- list_dir / grep refuse dsynth scaffolding ----------------------------


def test_list_dir_refuses_dsynth_template(env_dir):
    from dportsv3.agent import worker
    res = worker.list_dir("env", "/work/dsynth/build/Template")
    assert res["ok"] is False
    assert res["kind"] == "scaffolding_refused"
    assert "Template" in res["error"]
    # Pointer to a useful path is in the error.
    assert "/work/obj/" in res["error"]


def test_list_dir_refuses_dsynth_template_subpath(env_dir):
    from dportsv3.agent import worker
    res = worker.list_dir("env", "/work/dsynth/build/Template/usr")
    assert res["ok"] is False
    assert res["kind"] == "scaffolding_refused"


def test_list_dir_allows_real_port_obj_dirs(env_dir):
    from dportsv3.agent import worker
    (env_dir / "work" / "obj" / "devel" / "foo").mkdir(parents=True)
    res = worker.list_dir("env", "/work/obj/devel/foo")
    assert res["ok"] is True
    assert res["entries"] == []


def test_grep_refuses_dsynth_template(env_dir):
    from dportsv3.agent import worker
    res = worker.grep("env", "Makefile", "/work/dsynth/build/Template")
    assert res["ok"] is False
    assert res["kind"] == "scaffolding_refused"
    # Grep refusal preserves the pattern + match_count=0 shape.
    assert res["pattern"] == "Makefile"
    assert res["match_count"] == 0


def test_grep_allows_obj_dirs(env_dir):
    from dportsv3.agent import worker
    obj = env_dir / "work" / "obj" / "devel" / "foo"
    obj.mkdir(parents=True)
    (obj / "Makefile").write_text("BUILD=yes\n")
    res = worker.grep("env", "BUILD", "/work/obj/devel/foo")
    assert res["ok"] is True


# --- prompt smoke ---------------------------------------------------------


def test_patch_prompt_mentions_version_mismatch():
    """Prompt instructs the agent to verify versions before chasing
    triage-suggested paths."""
    from dportsv3.agent.prompts import PATCH_SYSTEM
    assert "Version mismatch" in PATCH_SYSTEM
    assert "/work/obj/" in PATCH_SYSTEM
    assert "remove the stale patch" in PATCH_SYSTEM


def test_patch_prompt_documents_worker_refusals():
    """Prompt warns about the two worker-level refusals so the agent
    can read the rule before the tool error teaches it."""
    from dportsv3.agent.prompts import PATCH_SYSTEM
    assert "/work/DPorts/" in PATCH_SYSTEM
    assert "refused" in PATCH_SYSTEM
    assert "Template" in PATCH_SYSTEM
