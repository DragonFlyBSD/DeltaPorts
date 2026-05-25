"""Plan Step 11b Slice 3 — `dportsv3 verify-fix BUNDLE_ID` orchestrator.

Glues the substrate primitive (Slice 1) and the tracker endpoint
(Slice 2). Tests drive end-to-end with injected hook functions so
no real tracker, env, or chroot is needed.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from dportsv3 import verify_fix


def _ab_dict(**fields) -> dict:
    """Render an apply_and_build result dict."""
    base = {
        "ok": True, "env": "verify-env", "origin": "devel/foo",
        "applied_diff_sha256": None,
        "apply_exit": 0, "reapply_exit": 0, "dsynth_exit": 0,
        "log_path": "/tmp/verify.log",
    }
    base.update(fields)
    return base


def _fake_ab_factory(*, result_fields=None, raise_with=None):
    """Build a fake apply_and_build that records its calls and
    returns a result dict (or raises)."""
    calls: list[dict] = []

    def _fake(env_name, origin, *, diff_path=None):
        calls.append({"env": env_name, "origin": origin, "diff_path": diff_path})
        if raise_with is not None:
            raise raise_with
        return _ab_dict(**(result_fields or {}))

    return _fake, calls


def _stub_get_bundle(bundle_id: str = "b-1", origin: str = "devel/foo",
                     target: str = "@2026Q2"):
    def _get_json(url: str, timeout: int = 10):
        if url.endswith(f"/api/bundles/{bundle_id}"):
            return {"bundle_id": bundle_id, "origin": origin,
                    "target": target}
        raise AssertionError(f"unexpected GET {url}")
    return _get_json


def _stub_get_diff(diff_bytes: bytes):
    """Stub that serves changes.diff and 404s the intent_log URL
    (the orchestrator probes intent_log first, falls back to diff
    on 404 — Step 25e)."""
    import urllib.error

    def _get_bytes(url: str, timeout: int = 20):
        if "/artifacts/analysis/intent_log.json" in url:
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
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
    fake_ab, calls = _fake_ab_factory(result_fields={
        "ok": True, "applied_diff_sha256": expected_sha, "dsynth_exit": 0,
    })
    posts: list = []

    result = verify_fix.run_verify_fix(
        bundle_id="b-1", env="verify-env",
        tracker_url="http://t",
        _get_json=_stub_get_bundle(),
        _get_bytes=_stub_get_diff(diff),
        _post_json=_stub_post(posts),
        _apply_and_build=fake_ab,
    )

    assert result.ok is True
    assert result.origin == "devel/foo"
    assert result.applied_diff_sha256 == expected_sha
    assert result.posted is True
    # apply_and_build invoked in-process with the right args.
    assert calls == [{"env": "verify-env", "origin": "devel/foo",
                      "diff_path": calls[0]["diff_path"]}]
    assert calls[0]["diff_path"].endswith(".diff")
    # POST body includes ok + sha + dsynth_exit.
    url, body = posts[0]
    assert url == "http://t/api/bundles/b-1/verification"
    assert body == {
        "ok": True, "applied_diff_sha256": expected_sha, "dsynth_exit": 0,
    }


def test_verify_fix_failed_posts_ok_false():
    diff = b"--- a/x\n+++ b/x\n"
    fake_ab, _ = _fake_ab_factory(result_fields={
        "ok": False, "dsynth_exit": 1,
        "applied_diff_sha256": hashlib.sha256(diff).hexdigest(),
    })
    posts: list = []

    result = verify_fix.run_verify_fix(
        bundle_id="b-1", env="verify-env", tracker_url="http://t",
        _get_json=_stub_get_bundle(),
        _get_bytes=_stub_get_diff(diff),
        _post_json=_stub_post(posts),
        _apply_and_build=fake_ab,
    )

    assert result.ok is False
    assert result.dsynth_exit == 1
    _, body = posts[0]
    assert body["ok"] is False


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


def test_verify_fix_missing_origin_raises():
    fake_ab, _ = _fake_ab_factory()

    def _bundle_no_origin(url: str, timeout: int = 10):
        return {"bundle_id": "b-1"}  # no origin

    with pytest.raises(verify_fix.VerifyFixError, match="has no origin"):
        verify_fix.run_verify_fix(
            bundle_id="b-1", env="verify-env", tracker_url="http://t",
            _get_json=_bundle_no_origin,
            _get_bytes=_stub_get_diff(b"diff"),
            _post_json=_stub_post([]),
            _apply_and_build=fake_ab,
        )


def test_verify_fix_empty_diff_raises():
    fake_ab, _ = _fake_ab_factory()

    with pytest.raises(verify_fix.VerifyFixError, match="is empty"):
        verify_fix.run_verify_fix(
            bundle_id="b-1", env="verify-env", tracker_url="http://t",
            _get_json=_stub_get_bundle(),
            _get_bytes=_stub_get_diff(b"   \n"),
            _post_json=_stub_post([]),
            _apply_and_build=fake_ab,
        )


def test_verify_fix_diff_404_raises():
    import urllib.error

    fake_ab, _ = _fake_ab_factory()

    def _missing_diff(url: str, timeout: int = 20):
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)

    with pytest.raises(verify_fix.VerifyFixError, match="has neither.*intent_log.*nor.*changes.diff"):
        verify_fix.run_verify_fix(
            bundle_id="b-1", env="verify-env", tracker_url="http://t",
            _get_json=_stub_get_bundle(),
            _get_bytes=_missing_diff,
            _post_json=_stub_post([]),
            _apply_and_build=fake_ab,
        )


def test_verify_fix_apply_and_build_raises_wraps_as_verify_fix_error():
    """If the substrate primitive raises (env not found, root not
    mounted, etc.) the orchestrator surfaces it as a VerifyFixError
    so the runner's `except Exception` catches it."""
    fake_ab, _ = _fake_ab_factory(raise_with=RuntimeError("env not ready"))

    with pytest.raises(verify_fix.VerifyFixError, match="env not ready"):
        verify_fix.run_verify_fix(
            bundle_id="b-1", env="verify-env", tracker_url="http://t",
            _get_json=_stub_get_bundle(),
            _get_bytes=_stub_get_diff(b"diff"),
            _post_json=_stub_post([]),
            _apply_and_build=fake_ab,
        )


def test_verify_fix_post_failure_does_not_raise():
    """Tracker outage shouldn't lose the verification result — the
    primitive already ran; we surface posted=False on the dataclass
    and let the caller decide."""
    fake_ab, _ = _fake_ab_factory(result_fields={"ok": True, "dsynth_exit": 0})

    def _failing_post(url: str, body: dict, timeout: int = 10):
        raise RuntimeError("tracker down")

    result = verify_fix.run_verify_fix(
        bundle_id="b-1", env="verify-env", tracker_url="http://t",
        _get_json=_stub_get_bundle(),
        _get_bytes=_stub_get_diff(b"diff"),
        _post_json=_failing_post,
        _apply_and_build=fake_ab,
    )
    assert result.ok is True
    assert result.posted is False


# ---------------------------------------------------------------------------
# Log retention
# ---------------------------------------------------------------------------


def test_verify_fix_drops_log_on_success_by_default(tmp_path):
    log = tmp_path / "verify.log"
    log.write_text("dsynth output here\n")
    fake_ab, _ = _fake_ab_factory(result_fields={
        "ok": True, "dsynth_exit": 0, "log_path": str(log),
    })

    result = verify_fix.run_verify_fix(
        bundle_id="b-1", env="verify-env", tracker_url="http://t",
        _get_json=_stub_get_bundle(),
        _get_bytes=_stub_get_diff(b"diff"),
        _post_json=_stub_post([]),
        _apply_and_build=fake_ab,
    )
    assert result.log_path is None
    assert not log.exists()


def test_verify_fix_keep_log_preserves_on_success(tmp_path):
    log = tmp_path / "verify.log"
    log.write_text("dsynth output here\n")
    fake_ab, _ = _fake_ab_factory(result_fields={
        "ok": True, "dsynth_exit": 0, "log_path": str(log),
    })

    result = verify_fix.run_verify_fix(
        bundle_id="b-1", env="verify-env", tracker_url="http://t",
        keep_log=True,
        _get_json=_stub_get_bundle(),
        _get_bytes=_stub_get_diff(b"diff"),
        _post_json=_stub_post([]),
        _apply_and_build=fake_ab,
    )
    assert result.log_path == str(log)
    assert log.exists()


def test_verify_fix_keeps_log_on_failure_unconditionally(tmp_path):
    log = tmp_path / "verify.log"
    log.write_text("error: build failed\n")
    fake_ab, _ = _fake_ab_factory(result_fields={
        "ok": False, "dsynth_exit": 1, "log_path": str(log),
    })

    result = verify_fix.run_verify_fix(
        bundle_id="b-1", env="verify-env", tracker_url="http://t",
        _get_json=_stub_get_bundle(),
        _get_bytes=_stub_get_diff(b"diff"),
        _post_json=_stub_post([]),
        _apply_and_build=fake_ab,
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
