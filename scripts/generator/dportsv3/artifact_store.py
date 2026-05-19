"""HTTP service that writes state.db, blobs, and full logs.

The single writer for state.db (the central evidence + agentic metadata
store). Tracker reads it; runner writes to it via this HTTP layer.
Schema lives in ``dportsv3.db.schema`` and is shared with tracker.

Entry points:
- ``scripts/artifact-store`` — standalone shim (no venv required)
- ``python -m dportsv3.artifact_store`` (or the venv's bin/artifact-store
  console script once pyproject.toml is updated)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .db.schema import init_db as _init_state_db

DEFAULT_BIND = "127.0.0.1"
DEFAULT_PORT = 8788
DEFAULT_LOGS_ROOT = "/build/synth/logs"


def log(level: str, message: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"{ts} {level:5} {message}")


def emit_event(conn: sqlite3.Connection, event_type: str, data: dict[str, Any]) -> None:
    ts = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO events (ts, type, data_json) VALUES (?, ?, ?)",
        (ts, event_type, json.dumps(data)),
    )


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def blob_path(root: Path, sha: str) -> Path:
    return root / "objects" / "sha256" / sha[0:2] / sha[2:4] / sha


class ArtifactStore:
    def __init__(self, logs_root: Path) -> None:
        self.logs_root = logs_root
        self.evidence_root = logs_root / "evidence"
        self.blob_root = self.evidence_root / "blobstore"
        self.full_logs_root = self.evidence_root / "full-logs"
        self.db_path = self.evidence_root / "state.db"
        self._lock = threading.Lock()

        self._ensure_dirs()
        self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        _init_state_db(self.conn)

    def _ensure_dirs(self) -> None:
        self.evidence_root.mkdir(parents=True, exist_ok=True)
        (self.blob_root / "objects" / "sha256").mkdir(parents=True, exist_ok=True)
        self.full_logs_root.mkdir(parents=True, exist_ok=True)

    def upsert_run_bundle(self, payload: dict[str, Any]) -> None:
        run_id = payload.get("run_id")
        profile = payload.get("profile")
        bundle_id = payload.get("bundle_id")
        origin = payload.get("origin")
        flavor = payload.get("flavor")
        ts_utc = payload.get("ts_utc")
        result = payload.get("result")
        target = payload.get("target")
        now = datetime.now(timezone.utc).isoformat()

        with self._lock:
            if run_id:
                self.conn.execute(
                    """INSERT INTO runs (run_id, profile, target, path, ts_start, ts_end, last_seen_at)
                       VALUES (?, ?, ?, NULL, ?, NULL, ?)
                       ON CONFLICT(run_id) DO UPDATE SET
                         profile=excluded.profile,
                         target=COALESCE(excluded.target, runs.target),
                         last_seen_at=excluded.last_seen_at""",
                    (run_id, profile, target, ts_utc, now),
                )

            self.conn.execute(
                """INSERT INTO bundles (bundle_id, run_id, origin, flavor, ts_utc, result, target, path, last_seen_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?)
                   ON CONFLICT(bundle_id) DO UPDATE SET
                     run_id=excluded.run_id,
                     origin=excluded.origin,
                     flavor=excluded.flavor,
                     ts_utc=excluded.ts_utc,
                     result=excluded.result,
                     target=COALESCE(excluded.target, bundles.target),
                     last_seen_at=excluded.last_seen_at""",
                (bundle_id, run_id, origin, flavor, ts_utc, result, target, now),
            )
            emit_event(self.conn, "bundle_upserted", {
                "bundle_id": bundle_id,
                "run_id": run_id,
                "origin": origin,
                "result": result,
                "target": target,
            })
            self.conn.commit()

    def put_blob(self, bundle_id: str, relpath: str, data: bytes, kind: str | None) -> dict[str, Any]:
        sha = sha256_bytes(data)
        obj_path = blob_path(self.blob_root, sha)
        if not obj_path.exists():
            obj_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = obj_path.with_suffix(".tmp")
            tmp_path.write_bytes(data)
            tmp_path.rename(obj_path)

        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self.conn.execute(
                """INSERT INTO blob_objects (sha256, size, created_at)
                   VALUES (?, ?, ?)
                   ON CONFLICT(sha256) DO NOTHING""",
                (sha, len(data), now),
            )
            self.conn.execute(
                """INSERT INTO artifact_refs (bundle_id, relpath, backend, sha256, fs_path, kind, size, created_at)
                   VALUES (?, ?, 'blob', ?, NULL, ?, ?, ?)
                   ON CONFLICT(bundle_id, relpath) DO UPDATE SET
                     backend='blob', sha256=excluded.sha256, fs_path=NULL,
                     kind=excluded.kind, size=excluded.size, created_at=excluded.created_at""",
                (bundle_id, relpath, sha, kind, len(data), now),
            )
            emit_event(self.conn, "artifact_put", {
                "bundle_id": bundle_id,
                "artifact": relpath,
                "backend": "blob",
            })
            self.conn.commit()

        return {"sha256": sha, "size": len(data)}

    def put_fs_ref(self, bundle_id: str, relpath: str, fs_path: str, kind: str | None) -> dict[str, Any]:
        path = Path(fs_path)
        size = path.stat().st_size if path.exists() else None
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self.conn.execute(
                """INSERT INTO artifact_refs (bundle_id, relpath, backend, sha256, fs_path, kind, size, created_at)
                   VALUES (?, ?, 'fs', NULL, ?, ?, ?, ?)
                   ON CONFLICT(bundle_id, relpath) DO UPDATE SET
                     backend='fs', sha256=NULL, fs_path=excluded.fs_path,
                     kind=excluded.kind, size=excluded.size, created_at=excluded.created_at""",
                (bundle_id, relpath, fs_path, kind, size, now),
            )
            emit_event(self.conn, "artifact_put", {
                "bundle_id": bundle_id,
                "artifact": relpath,
                "backend": "fs",
            })
            self.conn.commit()

        return {"size": size}

    def get_artifact(self, bundle_id: str, relpath: str) -> tuple[str, Path] | None:
        row = self.conn.execute(
            """SELECT backend, sha256, fs_path FROM artifact_refs
               WHERE bundle_id = ? AND relpath = ?""",
            (bundle_id, relpath),
        ).fetchone()
        if not row:
            return None
        if row["backend"] == "blob":
            obj_path = blob_path(self.blob_root, row["sha256"])
            return "blob", obj_path
        return "fs", Path(row["fs_path"])

    def upsert_user_context(self, run_id: str, origin: str, context_text: str) -> int:
        """Set or update the operator's hint text for one (run_id, origin).

        Bumps ``context_rev`` by 1 on every write so the runner's
        ``process_user_context_updates`` loop can detect new input and
        re-enqueue a triage retry.

        Returns the new ``context_rev``.
        """
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            row = self.conn.execute(
                "SELECT context_rev FROM user_context WHERE run_id = ? AND origin = ?",
                (run_id, origin),
            ).fetchone()
            if row:
                new_rev = int(row["context_rev"]) + 1
                self.conn.execute(
                    """UPDATE user_context
                       SET context_text = ?, updated_at = ?, context_rev = ?
                       WHERE run_id = ? AND origin = ?""",
                    (context_text, now, new_rev, run_id, origin),
                )
            else:
                new_rev = 1
                self.conn.execute(
                    """INSERT INTO user_context
                       (run_id, origin, context_text, updated_at, context_rev)
                       VALUES (?, ?, ?, ?, ?)""",
                    (run_id, origin, context_text, now, new_rev),
                )
            emit_event(self.conn, "user_context_updated", {
                "run_id": run_id,
                "origin": origin,
                "context_rev": new_rev,
                "updated_at": now,
            })
            self.conn.commit()
        return new_rev


class Handler(BaseHTTPRequestHandler):
    server: "ArtifactStoreServer"

    def log_message(self, format: str, *args: Any) -> None:
        log("HTTP", f"{self.address_string()} - {format % args}")

    def _send_json(self, data: Any, status: int = 200) -> None:
        body = json.dumps(data, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_error_json(self, status: int, message: str) -> None:
        self._send_json({"error": message}, status)

    def _read_json_body(self) -> dict[str, Any] | None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0:
            return None
        raw = self.rfile.read(length)
        if not raw:
            return None
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            return None

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        store = self.server.store

        if path == "/health":
            self._send_json({
                "ok": True,
                "db_path": str(store.db_path),
                "blobstore_root": str(store.blob_root),
                "full_logs_root": str(store.full_logs_root),
            })
            return

        if path == "/v1/artifacts/get":
            bundle_id = query.get("bundle_id", [None])[0]
            relpath = query.get("relpath", [None])[0]
            if not bundle_id or not relpath:
                self._send_error_json(400, "bundle_id and relpath required")
                return
            result = store.get_artifact(bundle_id, relpath)
            if not result:
                self._send_error_json(404, "artifact not found")
                return
            backend, file_path = result
            if not file_path.exists():
                self._send_error_json(404, "artifact file missing")
                return
            data = file_path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return

        self._send_error_json(404, "Not found")

    def do_POST(self) -> None:
        store = self.server.store
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/v1/bundles/upsert":
            body = self._read_json_body()
            if not body:
                self._send_error_json(400, "invalid JSON body")
                return
            bundle_id = body.get("bundle_id")
            if not bundle_id:
                self._send_error_json(400, "bundle_id required")
                return
            store.upsert_run_bundle(body)
            self._send_json({"ok": True})
            return

        if path == "/v1/artifacts/put":
            bundle_id = self.headers.get("X-Bundle-Id")
            relpath = self.headers.get("X-Relpath")
            kind = self.headers.get("X-Kind")
            if not bundle_id or not relpath:
                self._send_error_json(400, "X-Bundle-Id and X-Relpath required")
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = 0
            data = self.rfile.read(length) if length > 0 else b""
            if data is None:
                data = b""
            result = store.put_blob(bundle_id, relpath, data, kind)
            self._send_json({"ok": True, **result})
            return

        if path == "/v1/artifacts/put-fs":
            body = self._read_json_body()
            if not body:
                self._send_error_json(400, "invalid JSON body")
                return
            bundle_id = body.get("bundle_id")
            relpath = body.get("relpath")
            fs_path = body.get("fs_path")
            kind = body.get("kind")
            if not bundle_id or not relpath or not fs_path:
                self._send_error_json(400, "bundle_id, relpath, fs_path required")
                return
            result = store.put_fs_ref(bundle_id, relpath, fs_path, kind)
            self._send_json({"ok": True, **result})
            return

        if path == "/v1/user-context":
            # Ported from state-server's POST /user-context. Same body
            # shape and behaviour; state-server still serves the legacy
            # path in parallel until step 8 retires it.
            body = self._read_json_body()
            if not body:
                self._send_error_json(400, "invalid JSON body")
                return
            run_id = body.get("run_id")
            origin = body.get("origin")
            context_text = body.get("context_text")
            if not run_id or not origin or context_text is None:
                self._send_error_json(400, "run_id, origin, context_text required")
                return
            context_text = str(context_text).strip()
            if not context_text:
                self._send_error_json(400, "context_text cannot be empty")
                return
            if len(context_text) > 8000:
                self._send_error_json(400, "context_text too long (max 8000 chars)")
                return
            new_rev = store.upsert_user_context(run_id, origin, context_text)
            self._send_json({"ok": True, "context_rev": new_rev})
            return

        self._send_error_json(404, "Not found")


class ArtifactStoreServer(HTTPServer):
    def __init__(self, server_address: tuple[str, int], RequestHandlerClass: type, store: ArtifactStore) -> None:
        super().__init__(server_address, RequestHandlerClass)
        self.store = store


def main() -> None:
    parser = argparse.ArgumentParser(description="Local artifact store (blobstore + metadata)")
    parser.add_argument("--bind", default=DEFAULT_BIND)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--logs-root", default=DEFAULT_LOGS_ROOT)
    args = parser.parse_args()

    store = ArtifactStore(Path(args.logs_root))
    server = ArtifactStoreServer((args.bind, args.port), Handler, store)
    log("INFO", f"artifact-store listening on http://{args.bind}:{args.port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
