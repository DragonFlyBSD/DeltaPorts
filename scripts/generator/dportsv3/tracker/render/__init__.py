"""Tracker presentation library (extracted from server.py, Phase 2)."""

from .text import render_markdown, render_diff
from .artifacts import (
    artifact_view_data,
    artifact_media_type,
    resolve_artifact_path,
    default_artifact_relpath,
    load_tool_trace,
)
from .sessions import (
    session_view_data,
    is_session_relpath,
    parse_session_records,
    SESSION_ATTEMPT_RE,
)
from .activity import group_activity_by_attempt

__all__ = [
    "render_markdown", "render_diff", "artifact_view_data",
    "artifact_media_type", "resolve_artifact_path",
    "default_artifact_relpath", "load_tool_trace",
    "session_view_data", "is_session_relpath",
    "parse_session_records", "SESSION_ATTEMPT_RE",
    "group_activity_by_attempt",
]
