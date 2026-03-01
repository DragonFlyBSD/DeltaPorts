from __future__ import annotations

import json
from pathlib import Path


FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures" / "dportsv3"


def fixture_path(relative: str) -> Path:
    return FIXTURE_ROOT / relative


def read_text_fixture(relative: str) -> str:
    return fixture_path(relative).read_text()


def read_json_fixture(relative: str) -> dict:
    return json.loads(fixture_path(relative).read_text())


def list_fixture_paths(pattern: str) -> list[Path]:
    return sorted(FIXTURE_ROOT.glob(pattern))
