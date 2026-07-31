from __future__ import annotations

import json
import sqlite3
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


@pytest.fixture()
def isolated_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    state_root = tmp_path / "state"
    state_root.mkdir()
    monkeypatch.setenv("AGENT_STATE_ROOT", str(state_root))
    import job_store

    job_store._SCHEMA_READY_PATHS.clear()
    return tmp_path


def _legacy_db(state_root: Path) -> Path:
    db_path = state_root / "jobs" / "jobs.sqlite"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(
            """
            CREATE TABLE jobs (
              job_id TEXT PRIMARY KEY,
              status TEXT NOT NULL,
              revision INTEGER NOT NULL DEFAULT 0,
              progress_sequence INTEGER NOT NULL DEFAULT 0,
              payload_json TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            CREATE INDEX idx_jobs_status ON jobs(status);
            """
        )
        job_id = uuid.uuid4().hex[:12]
        payload = {
            "jobId": job_id,
            "status": "completed",
            "revision": 1,
            "progressSequence": 0,
            "taskSessionId": "legacy_task_aaaaaaaa",
            "arguments": {"taskSessionId": "legacy_task_aaaaaaaa"},
            "progress": [],
        }
        conn.execute(
            """
            INSERT INTO jobs(job_id, status, revision, progress_sequence, payload_json, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                "completed",
                1,
                0,
                json.dumps(payload),
                datetime.now(tz=timezone.utc).isoformat(),
            ),
        )
        conn.commit()
        return db_path
    finally:
        conn.close()


def test_legacy_jobs_sqlite_migrates_task_session_column(isolated_state: Path) -> None:
    import job_store
    from state_root import resolve_agent_state_root

    state_root = Path(resolve_agent_state_root())
    db_path = _legacy_db(state_root)
    # Prove legacy schema has no task_session_id before migration.
    conn = sqlite3.connect(str(db_path))
    try:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
        assert "task_session_id" not in cols
    finally:
        conn.close()

    jobs = job_store.list_job_records(isolated_state, limit=10)
    assert len(jobs) == 1
    assert jobs[0]["taskSessionId"] == "legacy_task_aaaaaaaa"

    conn = sqlite3.connect(str(db_path))
    try:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
        assert "task_session_id" in cols
        version = int(conn.execute("PRAGMA user_version").fetchone()[0])
        assert version >= job_store._SCHEMA_VERSION
        row = conn.execute(
            "SELECT task_session_id FROM jobs WHERE job_id = ?",
            (jobs[0]["jobId"],),
        ).fetchone()
        assert row[0] == "legacy_task_aaaaaaaa"
        # Index creation must succeed after ALTER.
        indexes = {
            row[1]
            for row in conn.execute("PRAGMA index_list(jobs)").fetchall()
        }
        assert "idx_jobs_task_session" in indexes
    finally:
        conn.close()

    found = job_store.find_jobs_by_task_session_id(
        "legacy_task_aaaaaaaa",
        isolated_state,
        include_terminal=True,
    )
    assert len(found) == 1


def test_prune_leaves_cancellation_uncertain(isolated_state: Path) -> None:
    import job_store

    job_id = uuid.uuid4().hex[:12]
    old = (datetime.now(tz=timezone.utc) - timedelta(hours=48)).isoformat()
    job = {
        "jobId": job_id,
        "status": "cancellation_uncertain",
        "revision": 2,
        "progressSequence": 0,
        "taskSessionId": "task_uncertain_01",
        "orphanProcessSuspected": True,
        "updatedAt": old,
        "progress": [],
    }
    assert job_store.write_job_record(job, workspace=isolated_state)
    # Force old updated_at in SQLite.
    db_path = job_store._db_path(isolated_state)
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "UPDATE jobs SET updated_at = ?, status = ? WHERE job_id = ?",
            (old, "cancellation_uncertain", job_id),
        )
        conn.commit()
    finally:
        conn.close()
    job_store._SCHEMA_READY_PATHS.clear()
    deleted = job_store.prune_terminal_jobs(isolated_state, ttl_hours=24)
    assert deleted == 0
    assert job_store.read_job_record(job_id, workspace=isolated_state) is not None
