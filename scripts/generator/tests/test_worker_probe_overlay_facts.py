"""Tests for the dev-env overlay fact probe parser.

The real probe runs inside a DragonFly dev-env chroot. These tests mock
``worker._exec`` so we cover the Python parsing and the second Makefile
read without requiring an env.
"""

from __future__ import annotations

import subprocess


def test_probe_overlay_facts_missing_port(monkeypatch) -> None:
    from dportsv3.agent import worker

    def fake_exec(env, *argv, cwd="/work/DeltaPorts", input_text=None, timeout=None):
        return subprocess.CompletedProcess(argv, 0, "MISSING=1\n", "")

    monkeypatch.setattr(worker, "_exec", fake_exec)

    facts = worker.probe_overlay_facts("test-env", "devel/missing")

    assert not facts.port_exists
    assert not facts.overlay_dops


def test_probe_overlay_facts_parses_files_and_targeted_makefiles(monkeypatch) -> None:
    from dportsv3.agent import worker

    stdout = "\n".join([
        "MISSING=0",
        "DOPS=1",
        "MKDFLY=Makefile.DragonFly.@any",
        "TARGET_MKDFLY=Makefile.DragonFly.@2026Q2",
        "DRAGONFLY_FILE=dragonfly/patch with space",
        "DIFF_FILE=diffs/fix.diff",
        "NEWPORT=1",
        "",
    ])

    def fake_exec(env, *argv, cwd="/work/DeltaPorts", input_text=None, timeout=None):
        return subprocess.CompletedProcess(argv, 0, stdout, "")

    monkeypatch.setattr(worker, "_exec", fake_exec)

    facts = worker.probe_overlay_facts("test-env", "devel/shape")

    assert facts.port_exists
    assert facts.overlay_dops
    assert facts.makefile_dragonfly == ("Makefile.DragonFly.@any",)
    assert facts.targeted_makefile_dragonfly == ("Makefile.DragonFly.@2026Q2",)
    assert facts.dragonfly_files == ("dragonfly/patch with space",)
    assert facts.diff_files == ("diffs/fix.diff",)
    assert facts.newport
    assert not facts.auto_safe_makefile
    assert facts.makefile_reasons == ("targeted_or_multiple_makefile_dragonfly",)


def test_probe_overlay_facts_reads_plain_makefile_for_auto_safe(monkeypatch) -> None:
    from dportsv3.agent import worker

    calls: list[tuple[str, ...]] = []

    def fake_exec(env, *argv, cwd="/work/DeltaPorts", input_text=None, timeout=None):
        calls.append(tuple(argv))
        if argv[0:3] == ("/bin/sh", "-c", 'cat "$DELTAPORTS_ROOT/ports/$1/Makefile.DragonFly"'):
            return subprocess.CompletedProcess(argv, 0, "USES+=pkgconfig\n", "")
        return subprocess.CompletedProcess(
            argv,
            0,
            "MISSING=0\nDOPS=0\nMKDFLY=Makefile.DragonFly\nNEWPORT=0\n",
            "",
        )

    monkeypatch.setattr(worker, "_exec", fake_exec)

    facts = worker.probe_overlay_facts("test-env", "devel/auto")

    assert len(calls) == 2
    assert facts.makefile_dragonfly == ("Makefile.DragonFly",)
    assert facts.auto_safe_makefile
    assert facts.makefile_reasons == ("supported_makefile_dragonfly_pattern",)


def test_probe_overlay_facts_marks_conditional_makefile_not_auto_safe(monkeypatch) -> None:
    from dportsv3.agent import worker

    def fake_exec(env, *argv, cwd="/work/DeltaPorts", input_text=None, timeout=None):
        if argv[0:3] == ("/bin/sh", "-c", 'cat "$DELTAPORTS_ROOT/ports/$1/Makefile.DragonFly"'):
            return subprocess.CompletedProcess(
                argv, 0, ".if ${OPSYS} == DragonFly\nUSES+=pkgconfig\n.endif\n", ""
            )
        return subprocess.CompletedProcess(
            argv,
            0,
            "MISSING=0\nDOPS=0\nMKDFLY=Makefile.DragonFly\nNEWPORT=0\n",
            "",
        )

    monkeypatch.setattr(worker, "_exec", fake_exec)

    facts = worker.probe_overlay_facts("test-env", "devel/conditional")

    assert not facts.auto_safe_makefile
    assert facts.makefile_reasons == ("conditional_block_present",)
