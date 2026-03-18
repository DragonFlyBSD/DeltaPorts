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
