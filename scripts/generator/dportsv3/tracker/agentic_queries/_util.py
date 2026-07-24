"""SQL helpers for the tracker's agentic-read endpoints.

These queries read the state.db tables originally owned by the
(now-retired) state-server: ``runs``, ``bundles``, ``jobs``, ``events``,
``activity_log``, ``runner_status``, ``artifact_refs``.

Target filtering: ``bundles``, ``jobs``, ``runs`` carry a nullable
``target`` column added in step 5. Filter is applied as an equality
match when supplied. ``NULL``-target rows surface only when no filter
is set — they're legacy or filed by a writer that didn't know its
target.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from dportsv3.agent.lifecycle import ACTIVE_WORK_STATE_VALUES


def _row_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {str(key): row[key] for key in row.keys()}


def _maybe(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return _row_dict(row) if row is not None else None


def _decode_extra_json(item: dict[str, Any]) -> dict[str, Any]:
    raw = item.get("extra_json")
    if raw:
        try:
            item["extra"] = json.loads(raw)
        except (TypeError, ValueError):
            item["extra"] = raw
    else:
        item["extra"] = None
    return item


