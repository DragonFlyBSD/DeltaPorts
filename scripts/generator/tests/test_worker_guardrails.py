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


# --- put_file refuses Makefile.DragonFly on a dops port -------------------


def test_put_file_refuses_makefile_dragonfly_when_overlay_present(env_dir):
    """Writing Makefile.DragonFly next to an existing overlay.dops
    produces the half-migrated state assess_dops rejects. The guard
    fires at the put_file boundary so the patch agent — which edits
    overlay.dops free-hand — sees a structured refusal."""
    from dportsv3.agent import worker
    port = env_dir / "work" / "DeltaPorts" / "ports" / "devel" / "foo"
    port.mkdir(parents=True)
    (port / "overlay.dops").write_text(
        'port devel/foo\ntype port\ntarget @any\nreason "x"\n'
    )
    res = worker.put_file(
        "env",
        "/work/DeltaPorts/ports/devel/foo/Makefile.DragonFly",
        "USES=ssl\n",
    )
    assert res["ok"] is False
    assert res["blocked_by"] == "dragonfly_on_dops_port"
    assert "mk` directives" in res["error"]
    # The file is NOT created.
    assert not (port / "Makefile.DragonFly").exists()


def test_put_file_allows_makefile_dragonfly_without_overlay(env_dir):
    """No overlay.dops yet → the port isn't a dops port, so a
    Makefile.DragonFly write is the legitimate compat shape. Allowed."""
    from dportsv3.agent import worker
    port = env_dir / "work" / "DeltaPorts" / "ports" / "devel" / "bar"
    port.mkdir(parents=True)
    res = worker.put_file(
        "env",
        "/work/DeltaPorts/ports/devel/bar/Makefile.DragonFly",
        "USES=ssl\n",
    )
    assert res.get("ok") is not False
    assert (port / "Makefile.DragonFly").is_file()


def test_put_file_allows_non_dragonfly_file_on_dops_port(env_dir):
    """The guard is scoped to Makefile.DragonFly* — other writes into a
    dops port (e.g. a dragonfly/ patch) are untouched by it."""
    from dportsv3.agent import worker
    port = env_dir / "work" / "DeltaPorts" / "ports" / "devel" / "baz"
    port.mkdir(parents=True)
    (port / "overlay.dops").write_text(
        'port devel/baz\ntype port\ntarget @any\nreason "x"\n'
    )
    res = worker.put_file(
        "env",
        "/work/DeltaPorts/ports/devel/baz/dragonfly/patch-x",
        "@@ -1 +1 @@\n",
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
    """worker.make_extract must invoke make against ``$DPORTS_COMPOSE_ROOT``
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
    res = worker.make_extract("env", "devel/libuv")
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
    res = worker.make_extract("env", "devel/foo")
    assert "/work/DPorts" in res["summary"]   # mentions the wrong path
    assert "lock root" in res["summary"]      # by name
    assert res["wrksrc"] in res["summary"]    # and the right path


def test_make_patch_runs_patch_target_against_compose_root(monkeypatch):
    """worker.make_patch must run the `patch` make target (do-patch,
    NOT extract) against ``$DPORTS_COMPOSE_ROOT`` via sh -c. do-patch is
    what applies files/patch-* then dragonfly/* into WRKSRC; running it
    against the lock root (/work/DPorts) would patch stale versions."""
    from dportsv3.agent import worker
    import subprocess

    captured = []

    def fake_exec(env, *argv, **kw):
        captured.append(argv)
        return subprocess.CompletedProcess(
            args=argv, returncode=0, stdout="===>  Patching for foo-1.0\n",
            stderr="",
        )

    monkeypatch.setattr(worker, "_exec", fake_exec)
    res = worker.make_patch("env", "devel/foo")
    assert res["ok"] is True

    # Single shell-out (no -V query like make_extract needs).
    assert len(captured) == 1
    call = captured[0]
    assert call[0] == "/bin/sh" and call[1] == "-c"
    payload = call[2]
    # The `patch` target, batched, against the compose root — not extract,
    # not a hardcoded lock-root path.
    assert "BATCH=yes patch" in payload
    assert "$DPORTS_COMPOSE_ROOT" in payload
    assert "/work/DPorts" not in payload
    # Success summary must tell the agent the tree is now build-state so
    # it knows dupe/genpatch will baseline correctly.
    assert "dupe" in res["summary"]


def test_make_patch_failure_surfaces_rejecting_patch(monkeypatch):
    """On a do-patch reject, make_patch must report ok=False, preserve
    the patch tool's `Hunk #N ... FAILED` output (so the agent sees
    WHICH patch rejected), and warn against duping the now half-patched
    WRKSRC — a stale .orig baseline would poison genpatch."""
    from dportsv3.agent import worker
    import subprocess

    reject_out = (
        "===>  Patching for foo-1.0\n"
        "===>  Applying dragonfly patches for foo-1.0\n"
        "1 out of 3 hunks failed--saving rejects to file src/foo.c.rej\n"
        "Hunk #2 failed at 412.\n"
    )

    def fake_exec(env, *argv, **kw):
        return subprocess.CompletedProcess(
            args=argv, returncode=1, stdout=reject_out,
            stderr="*** Error code 1\n",
        )

    monkeypatch.setattr(worker, "_exec", fake_exec)
    res = worker.make_patch("env", "devel/foo")

    assert res["ok"] is False
    assert res["rc"] == 1
    # The rejecting-patch detail survives into the tails the agent reads.
    assert "Hunk #2 failed at 412." in res["stdout_tail"]
    # Summary steers the agent away from a poisoned baseline.
    assert "half-patched" in res["summary"]
    assert "do NOT dupe" in res["summary"] or "do not dupe" in res["summary"].lower()


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


def test_dops_reference_is_not_in_playbooks_directory():
    """The cheat-sheet must NOT live under docs/agent-playbooks/ —
    otherwise the playbook selector could attach it to payloads,
    defeating the on-demand goal."""
    from pathlib import Path
    repo_root = Path(__file__).resolve().parents[3]
    cheatsheet = repo_root / "scripts/generator/dportsv3/agent/dops_quickref.md"
    assert cheatsheet.is_file(), "dops_quickref.md must be co-located with the agent module"
    # Confirm it is NOT under docs/agent-playbooks/ (the legacy
    # docs/kedb/ location is also checked since the constraint is the
    # same — neither directory should host the quickref).
    for candidate in (
        repo_root / "docs/agent-playbooks/dops_quickref.md",
        repo_root / "docs/kedb/dops_quickref.md",
    ):
        assert not candidate.exists(), (
            f"dops quick-reference must not live under {candidate.parent} — "
            "that would auto-load it into every payload"
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


def test_prompt_fail_fasts_on_extract_failure():
    """p5-Math-GSL smoke showed the agent burning 1M tokens after an
    extract FAILED — the prompt didn't tell it to stop. With this
    directive the agent gave-ups immediately, producing a useful
    manual handoff instead of thrashing."""
    from dportsv3.agent.prompts import PATCH_SYSTEM
    # Must explicitly mention extract failure and the stop directive.
    assert "extract" in PATCH_SYSTEM
    assert "STOP" in PATCH_SYSTEM or "stop" in PATCH_SYSTEM
    # Names the three known cause-classes so the agent can write a
    # useful handoff.
    assert "fetch-error" in PATCH_SYSTEM
    assert "missing-dep" in PATCH_SYSTEM
    # Tells the agent what to do (gave-up + handoff content).
    assert "gave-up" in PATCH_SYSTEM
