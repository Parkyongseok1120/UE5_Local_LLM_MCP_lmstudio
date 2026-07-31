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


class JobStoreError(RuntimeError):
    """Raised when the SQLite job store is unavailable in fail-closed mode."""


_DB_LOCK = threading.Lock()
_SCHEMA_READY_PATHS: set[str] = set()
# Legacy-compatible base table (no task_session_id index in CREATE).
_SCHEMA_BASE = """
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
_SCHEMA_VERSION = 2

JOB_ID_RE = __import__("re").compile(r"^[A-Fa-f0-9]{12,32}$")
TERMINAL_STATUSES = frozenset({
    "completed", "failed", "timed_out", "cancelled",
})
RECOVERABLE_UNCERTAIN_STATUSES = frozenset({"cancellation_uncertain"})
VALID_TRANSITIONS: dict[str, frozenset[str]] = {
    "created": frozenset({"queued", "cancelled", "cancel_requested"}),
    "starting": frozenset({"queued", "running", "cancelled", "cancel_requested", "failed", "cancellation_uncertain"}),
    "queued": frozenset({"starting", "running", "cancelled", "cancel_requested", "failed"}),
    "running": frozenset({"completed", "failed", "timed_out", "cancelled", "cancel_requested", "cancellation_uncertain"}),
    "cancel_requested": frozenset({"cancelled", "cancellation_uncertain"}),
    "cancellation_uncertain": frozenset({"cancelled", "cancellation_uncertain", "cancel_requested"}),
}
NON_REGRESSIVE_FROM = frozenset({"cancel_requested", "cancelled"})
BLOCKED_AFTER_CANCEL = frozenset({"running", "completed", "failed", "queued", "starting", "created"})


def _utc_now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def status_transition_allowed(current: str, next_status: str) -> bool:
    current = str(current or "created")
    next_status = str(next_status or "")
    if current == next_status:
        return True
    if current in TERMINAL_STATUSES:
        return False
    allowed = VALID_TRANSITIONS.get(current, frozenset())
    if next_status in allowed:
        return True
    return current == "starting" and next_status == "running"


def _merge_payload(current: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any] | None:
    merged = {**current, **patch}
    current_status = str(current.get("status") or "created")
    next_status = str(merged.get("status") or current_status)
    if next_status != current_status and not status_transition_allowed(current_status, next_status):
        return None
    if current_status in NON_REGRESSIVE_FROM and next_status in BLOCKED_AFTER_CANCEL:
        if next_status != current_status:
            return None
    return merged


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    return conn


def _task_session_from_payload(payload: dict[str, Any]) -> str:
    args = payload.get("arguments") if isinstance(payload.get("arguments"), dict) else {}
    return str(
        payload.get("taskSessionId")
        or args.get("taskSessionId")
        or args.get("task_session_id")
        or ""
    ).strip()


def _migrate_schema(conn: sqlite3.Connection) -> None:
    """Upgrade legacy DBs safely under an exclusive write lock."""

    # Avoid executescript(): it auto-COMMITs and would drop BEGIN EXCLUSIVE.
    conn.execute("BEGIN EXCLUSIVE")
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS jobs (
              job_id TEXT PRIMARY KEY,
              status TEXT NOT NULL,
              revision INTEGER NOT NULL DEFAULT 0,
              progress_sequence INTEGER NOT NULL DEFAULT 0,
              payload_json TEXT NOT NULL,
              updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status)"
        )
        columns = {
            str(row["name"])
            for row in conn.execute("PRAGMA table_info(jobs)").fetchall()
        }
        if "task_session_id" not in columns:
            try:
                conn.execute(
                    "ALTER TABLE jobs ADD COLUMN task_session_id TEXT NOT NULL DEFAULT ''"
                )
            except sqlite3.OperationalError as exc:
                # Concurrent migrator may have added the column first.
                if "duplicate column" not in str(exc).lower():
                    raise
                columns = {
                    str(row["name"])
                    for row in conn.execute("PRAGMA table_info(jobs)").fetchall()
                }
                if "task_session_id" not in columns:
                    raise
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_jobs_task_session ON jobs(task_session_id, status)"
        )
        rows = conn.execute(
            "SELECT job_id, payload_json FROM jobs "
            "WHERE task_session_id = '' OR task_session_id IS NULL"
        ).fetchall()
        for row in rows:
            try:
                payload = json.loads(row["payload_json"])
            except (TypeError, json.JSONDecodeError):
                continue
            if not isinstance(payload, dict):
                continue
            task_id = _task_session_from_payload(payload)
            if task_id:
                conn.execute(
                    "UPDATE jobs SET task_session_id = ? WHERE job_id = ?",
                    (task_id, row["job_id"]),
                )
        conn.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
        conn.execute("COMMIT")
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        raise


def _ensure_schema(conn: sqlite3.Connection, *, db_key: str = "") -> None:
    if db_key and db_key in _SCHEMA_READY_PATHS:
        try:
            version = int(conn.execute("PRAGMA user_version").fetchone()[0])
        except Exception:
            version = 0
        if version >= _SCHEMA_VERSION:
            return
    _migrate_schema(conn)
    if db_key:
        _SCHEMA_READY_PATHS.add(db_key)


def validate_job_id(job_id: str) -> str:
    value = str(job_id or "").strip()
    if not JOB_ID_RE.fullmatch(value):
        raise ValueError("jobId must match ^[A-Fa-f0-9]{12,32}$")
    return value


def _db_path(workspace: Path | None = None) -> Path:
    del workspace
    from state_root import resolve_agent_state_root

    root = ensure_state_root_layout(resolve_agent_state_root())
    return jobs_sqlite_path(root)


def read_job_record(job_id: str, workspace: Path | None = None) -> dict[str, Any] | None:
    job_id = validate_job_id(job_id)
    db_path = _db_path(workspace)
    try:
        with _DB_LOCK:
            conn = _connect(db_path)
            try:
                _ensure_schema(conn, db_key=str(db_path))
                row = conn.execute(
                    "SELECT payload_json FROM jobs WHERE job_id = ?", (job_id,)
                ).fetchone()
                if not row:
                    return None
                return json.loads(row["payload_json"])
            finally:
                conn.close()
    except (sqlite3.Error, OSError, json.JSONDecodeError) as exc:
        raise JobStoreError(str(exc)) from exc


def write_job_record(
    job: dict[str, Any],
    *,
    expected_revision: int | None = None,
    workspace: Path | None = None,
) -> bool:
    job_id = validate_job_id(str(job["jobId"]))
    db_path = _db_path(workspace)
    payload = dict(job)
    try:
        with _DB_LOCK:
            conn = _connect(db_path)
            try:
                _ensure_schema(conn, db_key=str(db_path))
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
                    merged = _merge_payload(json.loads(row["payload_json"]), payload)
                    if merged is None:
                        conn.execute("ROLLBACK")
                        return False
                    merged["revision"] = current_revision + 1
                elif row:
                    merged = _merge_payload(json.loads(row["payload_json"]), payload)
                    if merged is None:
                        conn.execute("ROLLBACK")
                        return False
                    merged["revision"] = int(row["revision"]) + 1
                else:
                    merged = dict(payload)
                    merged["revision"] = max(1, int(merged.get("revision") or 0) or 1)
                merged["updatedAt"] = _utc_now()
                task_session_id = _task_session_from_payload(merged)
                if task_session_id and not str(merged.get("taskSessionId") or "").strip():
                    merged["taskSessionId"] = task_session_id
                conn.execute(
                    """
                    INSERT INTO jobs(
                      job_id, status, revision, progress_sequence,
                      payload_json, updated_at, task_session_id
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(job_id) DO UPDATE SET
                      status = excluded.status,
                      revision = excluded.revision,
                      progress_sequence = excluded.progress_sequence,
                      payload_json = excluded.payload_json,
                      updated_at = excluded.updated_at,
                      task_session_id = excluded.task_session_id
                    """,
                    (
                        job_id,
                        str(merged.get("status") or "created"),
                        int(merged.get("revision") or 0),
                        int(merged.get("progressSequence") or 0),
                        json.dumps(merged, ensure_ascii=False),
                        merged["updatedAt"],
                        task_session_id,
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
    except (sqlite3.Error, OSError, json.JSONDecodeError) as exc:
        raise JobStoreError(str(exc)) from exc


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


def transition_job_record(
    job_id: str,
    next_status: str,
    mutator: Callable[[dict[str, Any]], None] | None = None,
    *,
    workspace: Path | None = None,
) -> bool:
    job_id = validate_job_id(job_id)

    def apply(draft: dict[str, Any]) -> None:
        current_status = str(draft.get("status") or "created")
        if not status_transition_allowed(current_status, next_status):
            raise ValueError(f"invalid transition {current_status} -> {next_status}")
        draft["status"] = next_status
        if mutator:
            mutator(draft)

    current = read_job_record(job_id, workspace=workspace)
    if not current:
        return False
    expected = int(current.get("revision") or 0)
    draft = dict(current)
    try:
        apply(draft)
    except ValueError:
        return False
    if write_job_record(draft, expected_revision=expected, workspace=workspace):
        return True
    fresh = read_job_record(job_id, workspace=workspace)
    if not fresh:
        return False
    draft = dict(fresh)
    try:
        apply(draft)
    except ValueError:
        return False
    return write_job_record(draft, expected_revision=int(fresh.get("revision") or 0), workspace=workspace)


def list_job_records(workspace: Path | None = None, *, limit: int = 20) -> list[dict[str, Any]]:
    db_path = _db_path(workspace)
    with _DB_LOCK:
        conn = _connect(db_path)
        try:
            _ensure_schema(conn, db_key=str(db_path))
            rows = conn.execute(
                "SELECT payload_json FROM jobs ORDER BY updated_at DESC LIMIT ?",
                (max(1, int(limit)),),
            ).fetchall()
            jobs: list[dict[str, Any]] = []
            for row in rows:
                try:
                    jobs.append(json.loads(row["payload_json"]))
                except (TypeError, json.JSONDecodeError):
                    continue
            return jobs
        finally:
            conn.close()


def find_jobs_by_task_session_id(
    task_session_id: str,
    workspace: Path | None = None,
    *,
    include_terminal: bool = True,
) -> list[dict[str, Any]]:
    """Return all jobs linked to a task session via indexed task_session_id."""

    session = str(task_session_id or "").strip()
    if not session:
        return []
    db_path = _db_path(workspace)
    with _DB_LOCK:
        conn = _connect(db_path)
        try:
            _ensure_schema(conn, db_key=str(db_path))
            if include_terminal:
                rows = conn.execute(
                    """
                    SELECT payload_json FROM jobs
                    WHERE task_session_id = ?
                    ORDER BY updated_at DESC
                    """,
                    (session,),
                ).fetchall()
            else:
                placeholders = ",".join("?" for _ in TERMINAL_STATUSES)
                rows = conn.execute(
                    f"""
                    SELECT payload_json FROM jobs
                    WHERE task_session_id = ?
                      AND status NOT IN ({placeholders})
                    ORDER BY updated_at DESC
                    """,
                    (session, *sorted(TERMINAL_STATUSES)),
                ).fetchall()
            jobs: list[dict[str, Any]] = []
            for row in rows:
                try:
                    payload = json.loads(row["payload_json"])
                except (TypeError, json.JSONDecodeError):
                    continue
                if isinstance(payload, dict):
                    jobs.append(payload)
            return jobs
        finally:
            conn.close()


def prune_terminal_jobs(workspace: Path | None = None, *, ttl_hours: int = 24) -> int:
    """Prune confirmed terminal jobs only. Never auto-delete cancellation_uncertain."""

    cutoff = datetime.now(tz=timezone.utc).timestamp() - (max(1, int(ttl_hours)) * 3600)
    cutoff_iso = datetime.fromtimestamp(cutoff, tz=timezone.utc).isoformat()
    db_path = _db_path(workspace)
    statuses = tuple(sorted(TERMINAL_STATUSES))
    placeholders = ",".join("?" for _ in statuses)
    with _DB_LOCK:
        conn = _connect(db_path)
        try:
            _ensure_schema(conn, db_key=str(db_path))
            cur = conn.execute(
                f"""
                DELETE FROM jobs
                WHERE status IN ({placeholders})
                  AND updated_at < ?
                  AND (
                    status != 'cancelled'
                    OR json_extract(payload_json, '$.processTerminationConfirmed') = 1
                    OR (
                      json_extract(payload_json, '$.subprocessSpawned') IS NOT 1
                      AND json_extract(payload_json, '$.pid') IS NULL
                      AND json_extract(payload_json, '$.pgid') IS NULL
                      AND json_extract(payload_json, '$.pidStartedAt') IS NULL
                    )
                  )
                """,
                (*statuses, cutoff_iso),
            )
            conn.commit()
            return int(cur.rowcount or 0)
        finally:
            conn.close()
