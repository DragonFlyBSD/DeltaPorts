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
    assert res["kind"] == "regenerated_tree_write_refused"
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
    assert res["kind"] == "regenerated_tree_write_refused"
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


def test_put_file_refuses_compose_root(env_dir):
    """The compose root at /work/artifacts/compose/<target>/ is
    materialize_dports' output — wiped on every materialize. Writes
    there evaporate silently. The guard refuses them with the same
    error mechanism as the lock root."""
    from dportsv3.agent import worker
    res = worker.put_file(
        "env",
        "/work/artifacts/compose/@2026Q2/devel/foo/Makefile",
        "X",
    )
    assert res["ok"] is False
    assert res["kind"] == "regenerated_tree_write_refused"
    assert "compose root" in res["error"]
    assert "/work/DeltaPorts" in res["error"]


def test_put_file_allows_compose_lookalike(env_dir):
    """``/work/artifacts/compose-unrelated`` must not be caught by
    the compose-root prefix."""
    from dportsv3.agent import worker
    (env_dir / "work" / "artifacts" / "compose-unrelated").mkdir(parents=True)
    res = worker.put_file(
        "env",
        "/work/DeltaPorts/ports/devel/foo/notes.md",
        "y",
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


def test_patch_prompt_mentions_extract_wrksrc_authority():
    """Prompt instructs the agent to trust the extract tool's wrksrc
    field over constructed paths. (The earlier 'list_dir
    /work/obj/<origin>/' advice was wrong because the obj tree
    contains stale leftovers from prior version-bumps.)"""
    from dportsv3.agent.prompts import PATCH_SYSTEM
    assert "wrksrc" in PATCH_SYSTEM
    assert "/work/obj/" in PATCH_SYSTEM


def test_patch_prompt_documents_worker_refusals():
    """Prompt warns about the two worker-level refusals so the agent
    can read the rule before the tool error teaches it."""
    from dportsv3.agent.prompts import PATCH_SYSTEM
    assert "/work/DPorts/" in PATCH_SYSTEM
    assert "refused" in PATCH_SYSTEM
    assert "Template" in PATCH_SYSTEM


def test_patch_prompt_tells_agent_to_use_extract_wrksrc():
    """The libuv smoke surfaced the agent inventing path shapes
    (``/work/obj/<origin>/<name>-<version>/``) instead of using
    extract's response's wrksrc (``/work/obj/<origin>/work/...``).
    Prompt must steer toward the response field."""
    from dportsv3.agent.prompts import PATCH_SYSTEM
    assert "wrksrc" in PATCH_SYSTEM
    assert "stale" in PATCH_SYSTEM
    # The "delete the patch" advice must be the LAST resort, not first.
    assert "not as the first move" in PATCH_SYSTEM \
        or "Don't reach for" in PATCH_SYSTEM


def test_extract_targets_compose_root_via_sh(monkeypatch):
    """worker.extract must invoke make against ``$DPORTS_COMPOSE_ROOT``
    (the materialized port tree for this target), NOT ``/work/DPorts/``
    (the lock root with stale versions). Uses sh -c so the env var
    is expanded in-chroot."""
    from dportsv3.agent import worker

    captured = []

    def fake_exec(env, *argv, **kw):
        captured.append(argv)
        import subprocess
        # First call = make extract; second call = make -V WRKDIR/WRKSRC.
        if "-V" in " ".join(argv):
            return subprocess.CompletedProcess(
                args=argv, returncode=0,
                stdout="/work/obj/work\n/work/obj/work/libuv-1.52.0\n",
                stderr="",
            )
        return subprocess.CompletedProcess(
            args=argv, returncode=0, stdout="", stderr="",
        )

    monkeypatch.setattr(worker, "_exec", fake_exec)
    res = worker.extract("env", "devel/libuv")
    assert res["wrksrc"] == "/work/obj/work/libuv-1.52.0"

    # Both invocations must shell out so $DPORTS_COMPOSE_ROOT expands.
    assert all(call[0] == "/bin/sh" for call in captured)
    assert all(call[1] == "-c" for call in captured)
    # The shell payload must reference $DPORTS_COMPOSE_ROOT, NOT a
    # hardcoded /work/DPorts path.
    extract_payload, query_payload = captured[0][2], captured[1][2]
    for payload in (extract_payload, query_payload):
        assert "$DPORTS_COMPOSE_ROOT" in payload
        assert "/work/DPorts" not in payload


def test_extract_summary_warns_against_lock_root(monkeypatch):
    """The extract response's summary must explicitly tell the LLM
    NOT to look under /work/DPorts/ — that's the lock root with
    stale versions."""
    from dportsv3.agent import worker
    import subprocess

    def fake_exec(env, *argv, **kw):
        if "-V" in " ".join(argv):
            return subprocess.CompletedProcess(
                args=argv, returncode=0,
                stdout="/work/obj/work\n/work/obj/work/foo-1.0\n", stderr="",
            )
        return subprocess.CompletedProcess(
            args=argv, returncode=0, stdout="", stderr="",
        )

    monkeypatch.setattr(worker, "_exec", fake_exec)
    res = worker.extract("env", "devel/foo")
    assert "/work/DPorts" in res["summary"]   # mentions the wrong path
    assert "lock root" in res["summary"]      # by name
    assert res["wrksrc"] in res["summary"]    # and the right path


# --- dops_reference (on-demand tool) -----------------------------------------


def test_dops_reference_returns_quickref_content():
    """The dops_reference tool returns the co-located quick-reference
    file. Should be roughly the size of the markdown file on disk and
    include identifiable dops keywords."""
    from dportsv3.agent import worker
    res = worker.dops_reference("any-env")
    assert res["ok"] is True
    body = res["content"]
    for keyword in ("port", "type", "target", "mk set", "mk replace-if",
                    "mk target", "text replace-once", "file copy",
                    "patch apply", "overlay.dops"):
        assert keyword in body, f"dops quick-ref missing keyword {keyword!r}"
    # Mention of the full grammar source so operators/agents know
    # where to look for the long-form spec.
    assert "dsl-v0.md" in body


def test_dops_reference_is_not_in_kedb_directory():
    """The cheat-sheet must NOT live under docs/kedb/ — otherwise
    the runner's KEDB auto-loader would ship it in every payload,
    defeating the on-demand goal."""
    from pathlib import Path
    repo_root = Path(__file__).resolve().parents[3]
    cheatsheet = repo_root / "scripts/generator/dportsv3/agent/dops_quickref.md"
    assert cheatsheet.is_file(), "dops_quickref.md must be co-located with the agent module"
    # Confirm it is NOT under docs/kedb/.
    kedb_copy = repo_root / "docs/kedb/dops_quickref.md"
    assert not kedb_copy.exists(), (
        "dops quick-reference must not live under docs/kedb/ — that would "
        "auto-load it into every payload"
    )


def test_dops_reference_tool_registered():
    """The tool must be discoverable in the registry the harness
    passes to litellm. Otherwise the LLM can't call it even if the
    prompt mentions it."""
    from dportsv3.agent.tools import names, schemas
    assert "dops_reference" in names()
    spec = next(s for s in schemas()
                if s["function"]["name"] == "dops_reference")
    # Zero required params — calling it doesn't need an argument.
    assert spec["function"]["parameters"]["required"] == []


# --- prompt updates ----------------------------------------------------------


def test_prompt_documents_compose_vs_lock_distinction():
    """Smoke surfaced an entire patch loop wasted on this confusion.
    The prompt must explicitly distinguish the four trees."""
    from dportsv3.agent.prompts import PATCH_SYSTEM
    assert "LOCK ROOT" in PATCH_SYSTEM or "lock root" in PATCH_SYSTEM
    assert "COMPOSE ROOT" in PATCH_SYSTEM or "compose root" in PATCH_SYSTEM
    assert "/work/artifacts/compose" in PATCH_SYSTEM


def test_prompt_directs_overlay_dops_check_first():
    """First probe on patch-error should be checking whether
    overlay.dops exists — that decides the entire fix strategy."""
    from dportsv3.agent.prompts import PATCH_SYSTEM
    assert "overlay.dops" in PATCH_SYSTEM
    assert "dops_reference" in PATCH_SYSTEM
