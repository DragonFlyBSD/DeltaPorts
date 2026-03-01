from __future__ import annotations

import subprocess

from dportsv3.engine.oracle import OracleResult, run_bmake_oracle


def test_oracle_off_profile_skips(tmp_path) -> None:
    (tmp_path / "Makefile").write_text("PORTNAME= sample\n")

    result = run_bmake_oracle(tmp_path, profile="off")

    assert result.ok
    assert result.skipped
    assert result.checks_run == 0


def test_oracle_local_missing_bmake_warns(tmp_path, monkeypatch) -> None:
    (tmp_path / "Makefile").write_text("PORTNAME= sample\n")
    monkeypatch.setattr("dportsv3.engine.oracle.shutil.which", lambda _: None)

    result = run_bmake_oracle(tmp_path, profile="local")

    assert result.ok
    assert result.skipped
    assert result.unavailable
    assert result.warnings


def test_oracle_ci_missing_bmake_fails(tmp_path, monkeypatch) -> None:
    (tmp_path / "Makefile").write_text("PORTNAME= sample\n")
    monkeypatch.setattr("dportsv3.engine.oracle.shutil.which", lambda _: None)

    result = run_bmake_oracle(tmp_path, profile="ci")

    assert not result.ok
    assert result.unavailable
    assert result.failures


def test_oracle_command_failure_maps_to_failure(tmp_path) -> None:
    (tmp_path / "Makefile").write_text("PORTNAME= sample\n")

    def fake_run(cmd, cwd):
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="syntax error")

    result = run_bmake_oracle(
        tmp_path,
        profile="local",
        bmake_path="/usr/bin/bmake",
        run_command=fake_run,
    )

    assert not result.ok
    assert result.checks_run == 1
    assert any("syntax error" in failure for failure in result.failures)


def test_oracle_ci_runs_variable_probes(tmp_path) -> None:
    (tmp_path / "Makefile").write_text("PORTNAME= sample\n")
    calls: list[list[str]] = []

    def fake_run(cmd, cwd):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="ok\n", stderr="")

    result = run_bmake_oracle(
        tmp_path,
        profile="ci",
        bmake_path="/usr/bin/bmake",
        run_command=fake_run,
    )

    assert result.ok
    assert result.checks_run == 4
    assert len(calls) == 4
    assert any("-V" in call for call in calls)
