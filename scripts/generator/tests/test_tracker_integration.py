from __future__ import annotations

import io
import json
from pathlib import Path
from urllib import error, parse

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from dportsv3.cli import main
from dportsv3.tracker import client as tracker_client
from dportsv3.tracker.server import create_app


class _HTTPResponseAdapter:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload

    def read(self) -> bytes:
        return self.payload

    def __enter__(self) -> _HTTPResponseAdapter:
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False


@pytest.fixture
def test_client(tmp_path: Path) -> TestClient:
    app = create_app(tmp_path / "tracker.db")
    with TestClient(app) as client:
        yield client


def test_cli_server_db_round_trip(
    monkeypatch: pytest.MonkeyPatch,
    test_client: TestClient,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def _urlopen(req):
        parts = parse.urlsplit(req.full_url)
        path = parts.path
        if parts.query:
            path = f"{path}?{parts.query}"
        response = test_client.request(
            req.get_method(),
            path,
            content=req.data,
            headers=dict(req.header_items()),
        )
        if response.status_code >= 400:
            raise error.HTTPError(
                req.full_url,
                response.status_code,
                response.reason_phrase,
                hdrs=None,
                fp=io.BytesIO(response.content),
            )
        return _HTTPResponseAdapter(response.content)

    monkeypatch.setenv("DPORTSV3_TRACKER_URL", "http://tracker.test")
    monkeypatch.setattr(tracker_client.request, "urlopen", _urlopen)

    code = main(["tracker", "start-build", "--target", "@main", "--type", "release"])
    out = capsys.readouterr()
    assert code == 0
    assert "Started release build 1 for @main" in out.out

    code = main(
        [
            "tracker",
            "record-result",
            "--run",
            "1",
            "--origin",
            "devel/foo",
            "--version",
            "1.0",
            "--result",
            "success",
            "--log-url",
            "https://logs.example/devel/foo.log.gz",
        ]
    )
    out = capsys.readouterr()
    assert code == 0
    assert "Recorded success for devel/foo in run 1" in out.out

    code = main(
        [
            "tracker",
            "finish-build",
            "--run",
            "1",
            "--commit-sha",
            "abc123",
            "--commit-branch",
            "main",
            "--commit-pushed-at",
            "2026-03-17T18:10:00+00:00",
        ]
    )
    out = capsys.readouterr()
    assert code == 0
    assert "Finished build 1" in out.out

    code = main(["tracker", "show-build", "--run", "1", "--json"])
    out = capsys.readouterr()
    assert code == 0
    build_payload = json.loads(out.out)
    assert build_payload["build_run"]["commit_sha"] == "abc123"
    assert (
        build_payload["results"][0]["log_url"]
        == "https://logs.example/devel/foo.log.gz"
    )

    code = main(["tracker", "start-build", "--target", "@main", "--type", "test"])
    out = capsys.readouterr()
    assert code == 0
    assert "Started test build 2 for @main" in out.out
