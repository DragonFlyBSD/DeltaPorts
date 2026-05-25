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


# --------------------------------------------------------------------
# Agentic read subcommands (get-bundle / list-jobs / get-activity / etc.)
# --------------------------------------------------------------------


def test_tracker_get_bundle_text(monkeypatch, capsys):
    from dportsv3.commands import tracker as t
    monkeypatch.setattr(t, "get_bundle", lambda server, bundle_id, *, include_jobs=False: {
        "bundle_id": "b-1", "origin": "devel/foo", "target": "@main",
        "result": "failure", "resolution": "agent_fixed",
        "last_seen_at": "2026-05-26T10:00:00Z",
        "artifacts": [
            {"relpath": "analysis/triage.md", "size": 1234},
            {"relpath": "analysis/changes.diff", "size": 5678},
        ],
    })
    code = main(["tracker", "get-bundle", "b-1", "--server", "http://t"])
    out = capsys.readouterr().out
    assert code == 0
    assert "Bundle:     b-1" in out
    assert "Origin:     devel/foo" in out
    assert "Resolution: agent_fixed" in out
    assert "analysis/triage.md" in out
    assert "1234B" in out


def test_tracker_get_bundle_json(monkeypatch, capsys):
    from dportsv3.commands import tracker as t
    monkeypatch.setattr(t, "get_bundle", lambda server, bundle_id, *, include_jobs=False: {
        "bundle_id": "b-1", "origin": "devel/foo", "artifacts": [],
    })
    code = main(["tracker", "get-bundle", "b-1",
                 "--server", "http://t", "--json"])
    import json as _json
    payload = _json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["bundle_id"] == "b-1"


def test_tracker_list_bundles_by_origin_routes_to_port_endpoint(
    monkeypatch, capsys,
):
    """When --origin is given, the CLI should hit list_port_bundles
    (the origin-scoped endpoint) rather than list_bundles."""
    from dportsv3.commands import tracker as t
    called = {}
    def fake_port(server, origin, *, target=None, limit=50):
        called["origin"] = origin; called["limit"] = limit
        return [{"bundle_id": "b-1", "origin": origin,
                 "target": "@main", "result": "fail",
                 "resolution": None, "last_seen_at": "now"}]
    def fake_unscoped(server, *, target=None, origin=None, limit=100):
        called["unscoped"] = True
        return []
    monkeypatch.setattr(t, "list_port_bundles", fake_port)
    monkeypatch.setattr(t, "list_bundles", fake_unscoped)

    code = main(["tracker", "list-bundles",
                 "--origin", "devel/foo", "--limit", "3",
                 "--server", "http://t"])
    out = capsys.readouterr().out
    assert code == 0
    assert called == {"origin": "devel/foo", "limit": 3}
    assert "b-1" in out
    assert "unscoped" not in called


def test_tracker_get_job_text(monkeypatch, capsys):
    from dportsv3.commands import tracker as t
    monkeypatch.setattr(t, "get_job", lambda server, job_id: {
        "job_id": "j-1", "state": "done", "origin": "devel/foo",
        "target": "@main", "bundle_id": "b-1",
        "updated_at": "2026-05-26T11:00:00Z",
        "retire_reason": "patch_ok",
    })
    code = main(["tracker", "get-job", "j-1", "--server", "http://t"])
    out = capsys.readouterr().out
    assert code == 0
    assert "Job:        j-1" in out
    assert "State:      done" in out
    assert "Retired:    patch_ok" in out


def test_tracker_list_jobs_text(monkeypatch, capsys):
    from dportsv3.commands import tracker as t
    monkeypatch.setattr(t, "list_jobs", lambda server, **kw: [
        {"job_id": "j-1", "state": "queued", "origin": "x/y",
         "target": "@main", "updated_at": "now"},
    ])
    code = main(["tracker", "list-jobs", "--state", "queued",
                 "--server", "http://t"])
    out = capsys.readouterr().out
    assert code == 0
    assert "j-1" in out and "queued" in out


def test_tracker_get_activity_text(monkeypatch, capsys):
    from dportsv3.commands import tracker as t
    monkeypatch.setattr(t, "get_activity", lambda server, **kw: [
        {"ts": "2026-05-26T12:00Z", "stage": "tool:get_file",
         "message": "/work/x ok"},
    ])
    code = main(["tracker", "get-activity", "--job", "j-1",
                 "--server", "http://t"])
    out = capsys.readouterr().out
    assert code == 0
    assert "tool:get_file" in out
    assert "/work/x ok" in out


def test_tracker_fetch_artifact_streams_raw_bytes(monkeypatch, capsys):
    from dportsv3.commands import tracker as t
    monkeypatch.setattr(
        t, "fetch_artifact",
        lambda server, bundle_id, relpath: b"raw bytes \x00\x01",
    )
    code = main(["tracker", "fetch-artifact", "b-1", "logs/errors.txt",
                 "--server", "http://t"])
    # capsys captures bytes via .out — confirm the data made it through.
    captured = capsys.readouterr()
    assert code == 0
    assert "raw bytes" in captured.out


def test_tracker_get_bundle_uses_env_var_server_when_omitted(
    monkeypatch, capsys,
):
    from dportsv3.commands import tracker as t
    captured = {}
    def fake(server, bundle_id, *, include_jobs=False):
        captured["server"] = server
        return {"bundle_id": bundle_id, "artifacts": []}
    monkeypatch.setattr(t, "get_bundle", fake)
    monkeypatch.setenv("DPORTSV3_TRACKER_URL", "http://from-env:9999")
    code = main(["tracker", "get-bundle", "b-1"])  # no --server
    assert code == 0
    assert captured["server"] == "http://from-env:9999"


def test_tracker_get_bundle_errors_without_server(capsys):
    code = main(["tracker", "get-bundle", "b-1"])
    err = capsys.readouterr().err
    assert code == 1
    assert "Tracker server URL required" in err


def test_tracker_get_bundle_with_jobs_flag(monkeypatch, capsys):
    """--jobs surfaces the related job IDs in the text rendering."""
    from dportsv3.commands import tracker as t
    captured = {}
    def fake_get(server, bundle_id, *, include_jobs=False):
        captured["include_jobs"] = include_jobs
        return {
            "bundle_id": bundle_id, "origin": "devel/foo",
            "target": "@main", "result": "fail",
            "resolution": "agent_gave_up",
            "last_seen_at": "2026-05-26T10:00Z",
            "artifacts": [],
            "jobs": [
                {"job_id": "triage-1", "type": "triage", "state": "done",
                 "created_ts_utc": "2026-05-26T09:00Z"},
                {"job_id": "convert-1", "type": "convert", "state": "done",
                 "created_ts_utc": "2026-05-26T09:30Z"},
                {"job_id": "patch-1", "type": "patch", "state": "dead",
                 "created_ts_utc": "2026-05-26T09:31Z"},
            ],
        }
    monkeypatch.setattr(t, "get_bundle", fake_get)

    code = main(["tracker", "get-bundle", "b-1", "--jobs",
                 "--server", "http://t"])
    out = capsys.readouterr().out
    assert code == 0
    assert captured["include_jobs"] is True
    assert "Jobs:       3" in out
    assert "triage-1" in out
    assert "convert-1" in out
    assert "patch-1" in out


def test_tracker_get_bundle_without_jobs_flag_omits_section(monkeypatch, capsys):
    """No --jobs → no jobs section + include_jobs not requested."""
    from dportsv3.commands import tracker as t
    captured = {}
    def fake_get(server, bundle_id, *, include_jobs=False):
        captured["include_jobs"] = include_jobs
        return {"bundle_id": bundle_id, "artifacts": []}
    monkeypatch.setattr(t, "get_bundle", fake_get)

    code = main(["tracker", "get-bundle", "b-1", "--server", "http://t"])
    out = capsys.readouterr().out
    assert code == 0
    assert captured["include_jobs"] is False
    assert "Jobs:" not in out
