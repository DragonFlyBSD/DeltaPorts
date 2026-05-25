"""Step 25f telemetry — per-intent activity-log rows + bundle-page
"Intent sequence" rendering.

Two surfaces:

1. PatchEventDispatcher emits an `intent_applied` activity row
   alongside the generic `tool:apply_intent` row whenever the
   patch agent calls apply_intent. The row carries
   intent_type / intent_target / ok / substrate_diff_sha256 /
   substrate_diff_bytes + inline substrate_diff if ≤ 4 KB.

2. Bundle detail page renders the intent_log.json artifact as a
   structured "Intent sequence" table so operators don't have to
   open the raw JSON to read the agent's edits.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from dportsv3.agent.steps import PatchEventDispatcher


# --------------------------------------------------------------------
# Telemetry helper — _emit_intent_applied
# --------------------------------------------------------------------


def _dispatcher_with_capture():
    """Build a dispatcher that captures activity_log calls."""
    captured: list = []

    def _activity_log(queue_root, stage, message, *,
                      job_id=None, duration_ms=None, extra=None):
        captured.append({
            "stage": stage, "message": message, "job_id": job_id,
            "duration_ms": duration_ms, "extra": extra or {},
        })

    d = PatchEventDispatcher(
        queue_root=Path("/tmp"),
        job_id="job-x",
        origin="devel/foo",
        activity_log=_activity_log,
        looks_env_suspicious=lambda res: False,
        invalidate_health_cache=lambda: None,
        summarize_tool_call=lambda t, a, r: "summary",
    )
    return d, captured


def _drop_patch_event(*, ok: bool, substrate_diff: str = ""):
    """Build a tool_call event for an apply_intent(drop_patch) call."""
    return {
        "type": "tool_call",
        "tool": "apply_intent",
        "attempt": 1, "turn": 3,
        "args": {
            "origin": "devel/foo",
            "intent": {"type": "drop_patch",
                       "target": "dragonfly/patch-old.c",
                       "reason": "obsolete"},
        },
        "result": {
            "ok": ok,
            "intent_type": "drop_patch",
            "mode": "compat",
            "substrate_diff": substrate_diff,
            "paths_changed": ["ports/devel/foo/dragonfly/patch-old.c"],
        },
        "duration_ms": 42,
    }


class TestIntentAppliedTelemetry:

    def test_emits_alongside_tool_call(self):
        d, captured = _dispatcher_with_capture()
        d(_drop_patch_event(ok=True, substrate_diff="diff --git\n+1\n-2\n"))
        stages = [r["stage"] for r in captured]
        # Both the generic tool: row AND the intent_applied row.
        assert "tool:apply_intent" in stages
        assert "intent_applied" in stages

    def test_inline_diff_when_under_limit(self):
        d, captured = _dispatcher_with_capture()
        diff = "+x\n" * 100  # ~400 bytes, well under 4 KB
        d(_drop_patch_event(ok=True, substrate_diff=diff))
        row = [r for r in captured if r["stage"] == "intent_applied"][0]
        assert row["extra"]["substrate_diff"] == diff
        assert row["extra"]["substrate_diff_bytes"] == len(diff.encode("utf-8"))
        assert len(row["extra"]["substrate_diff_sha256"]) == 64

    def test_sha_only_when_diff_over_limit(self):
        d, captured = _dispatcher_with_capture()
        diff = "x" * 5000  # > 4 KB
        d(_drop_patch_event(ok=True, substrate_diff=diff))
        row = [r for r in captured if r["stage"] == "intent_applied"][0]
        # No inline diff; sha + bytes only.
        assert "substrate_diff" not in row["extra"]
        assert row["extra"]["substrate_diff_bytes"] == 5000
        assert row["extra"]["substrate_diff_sha256"]

    def test_failure_surfaces_blocked_by_flags(self):
        d, captured = _dispatcher_with_capture()
        ev = {
            "type": "tool_call",
            "tool": "apply_intent",
            "attempt": 1, "turn": 1,
            "args": {"origin": "devel/foo",
                     "intent": {"type": "drop_patch", "target": "x",
                                "reason": "y"}},
            "result": {
                "ok": False,
                "intent_type": "drop_patch",
                "error": "substrate is in a half-migrated state",
                "blocked_by": "substrate_invariant",
                "invariant_violations": ["dops_with_unmigrated_makefile_dragonfly"],
                "unmigrated_artifacts": ["Makefile.DragonFly"],
            },
        }
        d(ev)
        row = [r for r in captured if r["stage"] == "intent_applied"][0]
        assert row["extra"]["ok"] is False
        assert row["extra"]["blocked_by"] == "substrate_invariant"
        assert "Makefile.DragonFly" in row["extra"]["unmigrated_artifacts"]
        assert "FAIL" in row["message"]

    def test_target_extraction_handles_dest_and_path_variants(self):
        d, captured = _dispatcher_with_capture()
        # add_file intent uses 'dest' instead of 'target'.
        ev = {
            "type": "tool_call", "tool": "apply_intent",
            "attempt": 1, "turn": 1,
            "args": {"origin": "devel/foo", "intent": {
                "type": "add_file", "dest": "files/post-install.sh",
                "kind": "resource", "content": "x",
            }},
            "result": {"ok": True, "intent_type": "add_file",
                       "mode": "compat", "substrate_diff": ""},
        }
        d(ev)
        row = [r for r in captured if r["stage"] == "intent_applied"][0]
        assert row["extra"]["intent_target"] == "files/post-install.sh"

    def test_non_apply_intent_tool_does_not_emit(self):
        d, captured = _dispatcher_with_capture()
        ev = {
            "type": "tool_call", "tool": "dsynth_build",
            "attempt": 1, "turn": 1,
            "args": {"origin": "devel/foo"},
            "result": {"ok": True},
        }
        d(ev)
        intent_rows = [r for r in captured if r["stage"] == "intent_applied"]
        assert intent_rows == []


# --------------------------------------------------------------------
# Bundle UI: Intent sequence card
# --------------------------------------------------------------------


fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from dportsv3.db.schema import init_db
from dportsv3.tracker.server import create_app


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _seed_bundle_with_intent_log(tmp_path: Path) -> Path:
    """Build a state.db + on-disk blob layout with a bundle whose
    analysis/intent_log.json artifact is present."""
    db_path = tmp_path / "state.db"
    artifact_root = tmp_path / "logs"
    artifact_root.mkdir()
    # Real blob layout: <root>/blobstore/objects/sha256/aa/bb/<full>
    blob_root = artifact_root / "blobstore" / "objects" / "sha256"
    blob_root.mkdir(parents=True)

    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    init_db(conn)
    now = _now()
    conn.execute(
        """INSERT INTO bundles (bundle_id, run_id, origin, flavor, ts_utc,
                                result, target, path, last_seen_at)
           VALUES (?, '', ?, '', ?, 'failure', '@main', '', ?)""",
        ("b-1", "devel/foo", now, now),
    )

    # Write the intent_log.json artifact as a blob.
    log_doc = {
        "schema_version": 1,
        "origin": "devel/foo",
        "target": "@main",
        "mode_at_apply": "compat",
        "baseline_commit": "abc123def456abc123def456abc123def456abcd",
        "intents": [
            {"seq": 0,
             "intent": {"type": "drop_patch",
                        "target": "dragonfly/patch-old.c",
                        "reason": "obsolete upstream"},
             "applied_at": "2026-05-25T10:00:00Z",
             "ok": True,
             "substrate_diff": "deleted file mode\n",
             "error": None},
            {"seq": 1,
             "intent": {"type": "add_file",
                        "dest": "files/post-install.sh",
                        "kind": "resource",
                        "content": "#!/bin/sh\n"},
             "applied_at": "2026-05-25T10:00:01Z",
             "ok": False,
             "substrate_diff": "",
             "error": "dest already exists"},
        ],
    }
    body = json.dumps(log_doc).encode("utf-8")
    import hashlib
    sha = hashlib.sha256(body).hexdigest()
    blob_path = blob_root / sha[0:2] / sha[2:4] / sha
    blob_path.parent.mkdir(parents=True, exist_ok=True)
    blob_path.write_bytes(body)
    conn.execute(
        """INSERT INTO artifact_refs (bundle_id, relpath, backend,
                                       sha256, size, created_at)
           VALUES ('b-1', 'analysis/intent_log.json', 'blob', ?, ?, ?)""",
        (sha, len(body), now),
    )
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture
def client_with_intent_log(tmp_path, monkeypatch):
    db_path = _seed_bundle_with_intent_log(tmp_path)
    # Tell create_app where the artifact root is.
    monkeypatch.setenv("DPORTSV3_ARTIFACT_ROOT", str(tmp_path / "logs"))
    app = create_app(db_path)
    with TestClient(app) as c:
        yield c


class TestBundleIntentSequenceCard:

    def test_intent_sequence_rendered_when_log_present(
        self, client_with_intent_log,
    ):
        resp = client_with_intent_log.get("/agentic/bundles/b-1")
        assert resp.status_code == 200
        body = resp.text
        assert "Intent sequence" in body
        assert "2 intents" in body
        assert "mode = compat" in body
        # drop_patch row + its reason.
        assert "drop_patch" in body
        assert "patch-old.c" in body
        assert "obsolete upstream" in body
        # add_file row + ok=False rendering with error excerpt.
        assert "add_file" in body
        assert "post-install.sh" in body
        assert "dest already exists" in body
        # baseline footer.
        assert "abc123def456" in body

    def test_no_intent_card_when_log_absent(self, tmp_path, monkeypatch):
        """Pre-Step-25 bundles (no intent_log.json) render unchanged."""
        db_path = _seed_bundle_with_intent_log(tmp_path)
        # Delete the artifact ref so the loader returns None.
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "DELETE FROM artifact_refs WHERE relpath = 'analysis/intent_log.json'"
        )
        conn.commit()
        conn.close()
        monkeypatch.setenv("DPORTSV3_ARTIFACT_ROOT", str(tmp_path / "logs"))
        app = create_app(db_path)
        with TestClient(app) as c:
            resp = c.get("/agentic/bundles/b-1")
        assert resp.status_code == 200
        assert "Intent sequence" not in resp.text
