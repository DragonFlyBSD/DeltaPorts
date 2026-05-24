"""Plan Step 11b Slice 3 — `dportsv3 verify-fix BUNDLE_ID` orchestrator.

Glues the substrate primitive (Slice 1) and the tracker endpoint
(Slice 2). Tests drive end-to-end with injected hook functions so
no real tracker, env, or chroot is needed.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from dportsv3 import verify_fix


def _fake_run_factory(stdout: str, stderr: str = "", returncode: int = 0):
    """Build a fake subprocess.run that records its argv and returns
    a CompletedProcess shaped like apply-and-build would."""
    calls: list[list[str]] = []

    def _fake_run(argv, capture_output=False, text=False, check=False):
        calls.append(list(argv))
        return subprocess.CompletedProcess(argv, returncode, stdout, stderr)

    return _fake_run, calls


def _ab_json(**fields) -> str:
    """Render an apply-and-build JSON record."""
    base = {
        "ok": True, "env": "verify-env", "origin": "devel/foo",
        "applied_diff_sha256": None,
        "apply_exit": 0, "reapply_exit": 0, "dsynth_exit": 0,
        "log_path": "/tmp/verify.log",
    }
    base.update(fields)
    return json.dumps(base)


def _stub_get_bundle(bundle_id: str = "b-1", origin: str = "devel/foo",
                     target: str = "@2026Q2"):
    def _get_json(url: str, timeout: int = 10):
        if url.endswith(f"/api/bundles/{bundle_id}"):
            return {"bundle_id": bundle_id, "origin": origin,
                    "target": target}
        raise AssertionError(f"unexpected GET {url}")
    return _get_json


def _stub_get_diff(diff_bytes: bytes):
    def _get_bytes(url: str, timeout: int = 20):
        assert "/artifacts/analysis/changes.diff" in url
        return diff_bytes
    return _get_bytes


def _stub_post(captures: list):
    def _post_json(url: str, body: dict, timeout: int = 10):
        captures.append((url, body))
        return {"ok": True}
    return _post_json


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


def test_verify_fix_verified_posts_ok_true():
    diff = b"--- a/x\n+++ b/x\n@@ -1 +1 @@\n-1\n+2\n"
    expected_sha = hashlib.sha256(diff).hexdigest()
    fake_run, calls = _fake_run_factory(_ab_json(
        ok=True, applied_diff_sha256=expected_sha, dsynth_exit=0,
    ))
    posts: list = []

    result = verify_fix.run_verify_fix(
        bundle_id="b-1", env="verify-env",
        tracker_url="http://t",
        _get_json=_stub_get_bundle(),
        _get_bytes=_stub_get_diff(diff),
        _post_json=_stub_post(posts),
        _run=fake_run,
    )

    assert result.ok is True
    assert result.origin == "devel/foo"
    assert result.applied_diff_sha256 == expected_sha
    assert result.posted is True
    # apply-and-build invoked with the right argv shape.
    assert calls and calls[0][0:3] == ["dportsv3", "dev-env", "apply-and-build"]
    assert "verify-env" in calls[0] and "devel/foo" in calls[0]
    assert "--diff" in calls[0] and "--json" in calls[0]
    # POST body includes ok + sha + dsynth_exit.
    url, body = posts[0]
    assert url == "http://t/api/bundles/b-1/verification"
    assert body == {
        "ok": True, "applied_diff_sha256": expected_sha, "dsynth_exit": 0,
    }


def test_verify_fix_failed_posts_ok_false():
    diff = b"--- a/x\n+++ b/x\n"
    fake_run, _ = _fake_run_factory(_ab_json(
        ok=False, dsynth_exit=1, applied_diff_sha256=hashlib.sha256(diff).hexdigest(),
    ))
    posts: list = []

    result = verify_fix.run_verify_fix(
        bundle_id="b-1", env="verify-env", tracker_url="http://t",
        _get_json=_stub_get_bundle(),
        _get_bytes=_stub_get_diff(diff),
        _post_json=_stub_post(posts),
        _run=fake_run,
    )

    assert result.ok is False
    assert result.dsynth_exit == 1
    _, body = posts[0]
    assert body["ok"] is False


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


def test_verify_fix_missing_origin_raises():
    fake_run, _ = _fake_run_factory(_ab_json())

    def _bundle_no_origin(url: str, timeout: int = 10):
        return {"bundle_id": "b-1"}  # no origin

    with pytest.raises(SystemExit, match="has no origin"):
        verify_fix.run_verify_fix(
            bundle_id="b-1", env="verify-env", tracker_url="http://t",
            _get_json=_bundle_no_origin,
            _get_bytes=_stub_get_diff(b"diff"),
            _post_json=_stub_post([]),
            _run=fake_run,
        )


def test_verify_fix_empty_diff_raises():
    fake_run, _ = _fake_run_factory(_ab_json())

    with pytest.raises(SystemExit, match="is empty"):
        verify_fix.run_verify_fix(
            bundle_id="b-1", env="verify-env", tracker_url="http://t",
            _get_json=_stub_get_bundle(),
            _get_bytes=_stub_get_diff(b"   \n"),
            _post_json=_stub_post([]),
            _run=fake_run,
        )


def test_verify_fix_diff_404_raises():
    import urllib.error

    fake_run, _ = _fake_run_factory(_ab_json())

    def _missing_diff(url: str, timeout: int = 20):
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)

    with pytest.raises(SystemExit, match="has no analysis/changes.diff"):
        verify_fix.run_verify_fix(
            bundle_id="b-1", env="verify-env", tracker_url="http://t",
            _get_json=_stub_get_bundle(),
            _get_bytes=_missing_diff,
            _post_json=_stub_post([]),
            _run=fake_run,
        )


def test_verify_fix_unparseable_ab_output_raises():
    fake_run, _ = _fake_run_factory("not json\n", returncode=2)

    with pytest.raises(SystemExit, match="no JSON on stdout"):
        verify_fix.run_verify_fix(
            bundle_id="b-1", env="verify-env", tracker_url="http://t",
            _get_json=_stub_get_bundle(),
            _get_bytes=_stub_get_diff(b"diff"),
            _post_json=_stub_post([]),
            _run=fake_run,
        )


def test_verify_fix_post_failure_does_not_raise():
    """Tracker outage shouldn't lose the verification result — the
    primitive already ran; we surface posted=False on the dataclass
    and let the caller decide."""
    fake_run, _ = _fake_run_factory(_ab_json(ok=True, dsynth_exit=0))

    def _failing_post(url: str, body: dict, timeout: int = 10):
        raise RuntimeError("tracker down")

    result = verify_fix.run_verify_fix(
        bundle_id="b-1", env="verify-env", tracker_url="http://t",
        _get_json=_stub_get_bundle(),
        _get_bytes=_stub_get_diff(b"diff"),
        _post_json=_failing_post,
        _run=fake_run,
    )
    assert result.ok is True
    assert result.posted is False


# ---------------------------------------------------------------------------
# Log retention
# ---------------------------------------------------------------------------


def test_verify_fix_drops_log_on_success_by_default(tmp_path):
    log = tmp_path / "verify.log"
    log.write_text("dsynth output here\n")
    fake_run, _ = _fake_run_factory(_ab_json(
        ok=True, dsynth_exit=0, log_path=str(log),
    ))

    result = verify_fix.run_verify_fix(
        bundle_id="b-1", env="verify-env", tracker_url="http://t",
        _get_json=_stub_get_bundle(),
        _get_bytes=_stub_get_diff(b"diff"),
        _post_json=_stub_post([]),
        _run=fake_run,
    )
    assert result.log_path is None
    assert not log.exists()


def test_verify_fix_keep_log_preserves_on_success(tmp_path):
    log = tmp_path / "verify.log"
    log.write_text("dsynth output here\n")
    fake_run, _ = _fake_run_factory(_ab_json(
        ok=True, dsynth_exit=0, log_path=str(log),
    ))

    result = verify_fix.run_verify_fix(
        bundle_id="b-1", env="verify-env", tracker_url="http://t",
        keep_log=True,
        _get_json=_stub_get_bundle(),
        _get_bytes=_stub_get_diff(b"diff"),
        _post_json=_stub_post([]),
        _run=fake_run,
    )
    assert result.log_path == str(log)
    assert log.exists()


def test_verify_fix_keeps_log_on_failure_unconditionally(tmp_path):
    log = tmp_path / "verify.log"
    log.write_text("error: build failed\n")
    fake_run, _ = _fake_run_factory(_ab_json(
        ok=False, dsynth_exit=1, log_path=str(log),
    ))

    result = verify_fix.run_verify_fix(
        bundle_id="b-1", env="verify-env", tracker_url="http://t",
        _get_json=_stub_get_bundle(),
        _get_bytes=_stub_get_diff(b"diff"),
        _post_json=_stub_post([]),
        _run=fake_run,
    )
    assert result.log_path == str(log)
    assert log.exists()


# ---------------------------------------------------------------------------
# CLI smoke
# ---------------------------------------------------------------------------


def test_verify_fix_cli_subcommand_registers():
    """`dportsv3 verify-fix --help` should not blow up."""
    from dportsv3.cli import create_parser

    parser = create_parser()
    args = parser.parse_args(["verify-fix", "b-1", "--env", "e", "--json"])
    assert args.command == "verify-fix"
    assert args.bundle_id == "b-1"
    assert args.env == "e"
    assert args.json is True
    assert args.keep_log is False
