from __future__ import annotations

import pytest

from dportsv3.cli import main


def test_tracker_help_exits_zero() -> None:
    with pytest.raises(SystemExit) as exc:
        main(["tracker", "--help"])
    assert exc.value.code == 0


def test_tracker_missing_action_returns_1(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["tracker"])
    out = capsys.readouterr()

    assert code == 1
    assert "Missing tracker action" in out.err


def test_tracker_start_build_requires_server_url(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = main(["tracker", "start-build", "--target", "@main", "--type", "release"])
    out = capsys.readouterr()

    assert code == 1
    assert "Tracker server URL required" in out.err


def test_tracker_start_build_success_path(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from dportsv3.commands import tracker as tracker_command

    monkeypatch.setattr(
        tracker_command, "start_build", lambda server, target, build_type: 17
    )

    code = main(
        [
            "tracker",
            "start-build",
            "--target",
            "@main",
            "--type",
            "release",
            "--server",
            "http://tracker.test",
        ]
    )
    out = capsys.readouterr()

    assert code == 0
    assert "Started release build 17 for @main" in out.out


def test_tracker_compare_builds_text_output(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from dportsv3.commands import tracker as tracker_command

    monkeypatch.setattr(
        tracker_command,
        "compare_builds",
        lambda server, a, b: {
            "run_a": {
                "id": 3,
                "target": "@2026Q1",
                "build_type": "release",
                "started_at": "2026-03-10",
            },
            "run_b": {
                "id": 5,
                "target": "@2026Q1",
                "build_type": "release",
                "started_at": "2026-03-14",
            },
            "summary": {
                "new_successes": 12,
                "new_failures": 3,
                "still_failing": 45,
                "still_succeeding": 31200,
                "added": 8,
                "removed": 2,
                "version_changes": 340,
            },
            "new_failures": [
                {"origin": "devel/bar", "version_b": "2.0"},
                {"origin": "www/baz", "version_b": "1.5"},
            ],
        },
    )

    code = main(
        [
            "tracker",
            "compare-builds",
            "3",
            "5",
            "--server",
            "http://tracker.test",
        ]
    )
    out = capsys.readouterr()

    assert code == 0
    assert (
        "Comparing @2026Q1 release run 3 (2026-03-10) vs run 5 (2026-03-14)" in out.out
    )
    assert "New failures (regressions):" in out.out
    assert "devel/bar 2.0, www/baz 1.5" in out.out
