#!/usr/bin/env python
"""SQLite-backed cross-process job store."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from state_root import ensure_state_root_layout, jobs_sqlite_path

_DB_LOCK = threading.Lock()
_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
  job_id TEXT PRIMARY KEY,
  status TEXT NOT NULL,
  revision INTEGER NOT NULL DEFAULT 0,
  progress_sequence INTEGER NOT NULL DEFAULT 0,
  payload_json TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
"""

JOB_ID_RE = __import__("re").compile(r"^[A-Fa-f0-9]{12,32}$")
TERMINAL_STATUSES = frozenset({
    "completed", "failed", "timed_out", "cancelled", "cancellation_uncertain",
})


def _utc_now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA)


def validate_job_id(job_id: str) -> str:
    value = str(job_id or "").strip()
    if not JOB_ID_RE.fullmatch(value):
        raise ValueError("jobId must match ^[A-Fa-f0-9]{12,32}$")
    return value


def _db_path(workspace: Path | None = None) -> Path:
    from state_root import resolve_agent_state_root

    root = ensure_state_root_layout(resolve_agent_state_root(workspace))
    return jobs_sqlite_path(root)


def read_job_record(job_id: str, workspace: Path | None = None) -> dict[str, Any] | None:
    job_id = validate_job_id(job_id)
    db_path = _db_path(workspace)
    with _DB_LOCK:
        conn = _connect(db_path)
        try:
            _ensure_schema(conn)
            row = conn.execute("SELECT payload_json FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
            if not row:
                return None
            return json.loads(row["payload_json"])
        finally:
            conn.close()


def write_job_record(
    job: dict[str, Any],
    *,
    expected_revision: int | None = None,
    workspace: Path | None = None,
) -> bool:
    job_id = validate_job_id(str(job["jobId"]))
    db_path = _db_path(workspace)
    payload = dict(job)
    with _DB_LOCK:
        conn = _connect(db_path)
        try:
            _ensure_schema(conn)
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT revision, payload_json FROM jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            if expected_revision is not None:
                if not row:
                    conn.execute("ROLLBACK")
                    return False
                current_revision = int(row["revision"])
                if current_revision != expected_revision:
                    conn.execute("ROLLBACK")
                    return False
                merged = {**json.loads(row["payload_json"]), **payload}
                merged["revision"] = current_revision + 1
            elif row:
                merged = {**json.loads(row["payload_json"]), **payload}
                merged["revision"] = int(row["revision"]) + 1
            else:
                merged = dict(payload)
                merged["revision"] = max(1, int(merged.get("revision") or 0) or 1)
            merged["updatedAt"] = _utc_now()
            conn.execute(
                """
                INSERT INTO jobs(job_id, status, revision, progress_sequence, payload_json, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(job_id) DO UPDATE SET
                  status = excluded.status,
                  revision = excluded.revision,
                  progress_sequence = excluded.progress_sequence,
                  payload_json = excluded.payload_json,
                  updated_at = excluded.updated_at
                """,
                (
                    job_id,
                    str(merged.get("status") or "created"),
                    int(merged.get("revision") or 0),
                    int(merged.get("progressSequence") or 0),
                    json.dumps(merged, ensure_ascii=False),
                    merged["updatedAt"],
                ),
            )
            conn.execute("COMMIT")
            return True
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
            raise
        finally:
            conn.close()


def mutate_job_record(
    job_id: str,
    mutator: Callable[[dict[str, Any]], None],
    *,
    workspace: Path | None = None,
) -> bool:
    job_id = validate_job_id(job_id)
    current = read_job_record(job_id, workspace=workspace)
    if not current:
        return False
    expected = int(current.get("revision") or 0)
    draft = dict(current)
    mutator(draft)
    if write_job_record(draft, expected_revision=expected, workspace=workspace):
        return True
    fresh = read_job_record(job_id, workspace=workspace)
    if not fresh:
        return False
    retry_expected = int(fresh.get("revision") or 0)
    draft = dict(fresh)
    mutator(draft)
    return write_job_record(draft, expected_revision=retry_expected, workspace=workspace)
