from __future__ import annotations

import io
from urllib import error

import pytest

from dportsv3.tracker import client


class _FakeResponse:
    def __init__(self, payload: str) -> None:
        self.payload = payload.encode("utf-8")

    def read(self) -> bytes:
        return self.payload

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False


def test_start_build_posts_target_and_build_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def _fake_urlopen(req):
        captured["url"] = req.full_url
        captured["method"] = req.get_method()
        captured["body"] = req.data.decode("utf-8")
        return _FakeResponse('{"id": 42}')

    monkeypatch.setattr(client.request, "urlopen", _fake_urlopen)

    run_id = client.start_build("http://tracker.test", "@main", "release")

    assert run_id == 42
    assert captured == {
        "url": "http://tracker.test/api/builds",
        "method": "POST",
        "body": '{"build_type": "release", "target": "@main"}',
    }


def test_finish_build_sends_patch_with_commit_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def _fake_urlopen(req):
        captured["url"] = req.full_url
        captured["method"] = req.get_method()
        captured["body"] = req.data.decode("utf-8")
        return _FakeResponse('{"ok": true}')

    monkeypatch.setattr(client.request, "urlopen", _fake_urlopen)

    client.finish_build(
        "http://tracker.test",
        7,
        finished_at="2026-03-17T18:00:00+00:00",
        commit_sha="abc123",
        commit_branch="main",
        commit_pushed_at="2026-03-17T18:10:00+00:00",
    )

    assert captured == {
        "url": "http://tracker.test/api/builds/7",
        "method": "PATCH",
        "body": '{"commit_branch": "main", "commit_pushed_at": "2026-03-17T18:10:00+00:00", "commit_sha": "abc123", "finished_at": "2026-03-17T18:00:00+00:00"}',
    }


def test_get_status_builds_query_string(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def _fake_urlopen(req):
        captured["url"] = req.full_url
        captured["method"] = req.get_method()
        return _FakeResponse('[{"origin": "devel/foo"}]')

    monkeypatch.setattr(client.request, "urlopen", _fake_urlopen)

    payload = client.get_status(
        "http://tracker.test",
        target="@main",
        origin="devel/foo",
    )

    assert payload == [{"origin": "devel/foo"}]
    assert captured == {
        "url": "http://tracker.test/api/status?target=%40main&origin=devel%2Ffoo",
        "method": "GET",
    }


def test_compare_builds_raises_clear_error_on_http_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _fake_urlopen(req):
        raise error.HTTPError(
            req.full_url,
            409,
            "Conflict",
            hdrs=None,
            fp=io.BytesIO(b'{"detail": "active build exists"}'),
        )

    monkeypatch.setattr(client.request, "urlopen", _fake_urlopen)

    with pytest.raises(RuntimeError) as exc:
        client.compare_builds("http://tracker.test", 1, 2)

    assert "Tracker API error (409)" in str(exc.value)
    assert "active build exists" in str(exc.value)


# --------------------------------------------------------------------
# Agentic-side read helpers (get-bundle / list-jobs / etc.)
# --------------------------------------------------------------------


def test_get_bundle_hits_bundle_detail_endpoint(monkeypatch):
    captured = {}
    def _fake(req):
        captured["url"] = req.full_url
        captured["method"] = req.get_method()
        return _FakeResponse('{"bundle_id":"b-1","origin":"devel/foo"}')
    monkeypatch.setattr(client.request, "urlopen", _fake)
    out = client.get_bundle("http://t", "b-1")
    assert captured["url"] == "http://t/api/bundles/b-1"
    assert captured["method"] == "GET"
    assert out == {"bundle_id": "b-1", "origin": "devel/foo"}


def test_list_bundles_compacts_query(monkeypatch):
    captured = {}
    def _fake(req):
        captured["url"] = req.full_url
        return _FakeResponse('[]')
    monkeypatch.setattr(client.request, "urlopen", _fake)
    client.list_bundles("http://t", origin="devel/foo", limit=5)
    assert "origin=devel%2Ffoo" in captured["url"]
    assert "limit=5" in captured["url"]
    # target was None → omitted from query
    assert "target" not in captured["url"]


def test_list_port_bundles_path_encodes_origin(monkeypatch):
    captured = {}
    def _fake(req):
        captured["url"] = req.full_url
        return _FakeResponse('[]')
    monkeypatch.setattr(client.request, "urlopen", _fake)
    client.list_port_bundles("http://t", "devel/gperf")
    # Slash in origin must be percent-encoded so the path-segment
    # routing matches the FastAPI {origin:path} param.
    assert "/api/ports/devel%2Fgperf" in captured["url"]


def test_get_activity_filters_round_trip(monkeypatch):
    captured = {}
    def _fake(req):
        captured["url"] = req.full_url
        return _FakeResponse('[]')
    monkeypatch.setattr(client.request, "urlopen", _fake)
    client.get_activity(
        "http://t", job_id="job-1", stage_filter="tool",
        since_id=42, limit=10,
    )
    url = captured["url"]
    assert "job_id=job-1" in url
    assert "stage_filter=tool" in url
    assert "since_id=42" in url
    assert "limit=10" in url


def test_fetch_artifact_returns_raw_bytes(monkeypatch):
    captured = {}
    def _fake(url):
        # urlopen called with raw URL, not Request
        captured["url"] = url
        return _FakeResponse("--- raw diff bytes ---")
    monkeypatch.setattr(client.request, "urlopen", _fake)
    body = client.fetch_artifact("http://t", "b-1", "analysis/changes.diff")
    assert body == b"--- raw diff bytes ---"
    assert "/api/bundles/b-1/artifacts/analysis/changes.diff" in captured["url"]


def test_fetch_artifact_path_segments_encoded(monkeypatch):
    """A relpath like 'snippets/round_1/file.c' should percent-encode
    each segment individually so slashes survive."""
    captured = {}
    def _fake(url):
        captured["url"] = url
        return _FakeResponse("x")
    monkeypatch.setattr(client.request, "urlopen", _fake)
    client.fetch_artifact("http://t", "b-1", "snippets/round 1/file.c")
    # Space gets %20, slashes preserved.
    assert "round%201" in captured["url"]
    assert "snippets/round%201/file.c" in captured["url"]


def test_get_bundle_raises_on_404(monkeypatch):
    def _fake(req):
        raise error.HTTPError(
            req.full_url, 404, "Not Found", {},
            io.BytesIO(b'{"detail":"Unknown bundle"}'),
        )
    monkeypatch.setattr(client.request, "urlopen", _fake)
    with pytest.raises(RuntimeError) as exc:
        client.get_bundle("http://t", "ghost")
    assert "404" in str(exc.value)
