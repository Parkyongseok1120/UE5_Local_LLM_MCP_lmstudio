#!/usr/bin/env python
"""Transactional stale-reclaim guard used by the Node write-lock implementation."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


def process_state(pid: int) -> str:
    if pid <= 0:
        return "dead"
    if os.name == "nt":
        import ctypes

        query_limited_information = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(
            query_limited_information, False, pid
        )
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)
            return "alive"
        error = ctypes.windll.kernel32.GetLastError()
        return "unknown" if error == 5 else "dead"
    try:
        os.kill(pid, 0)
        return "alive"
    except PermissionError:
        return "unknown"
    except ProcessLookupError:
        return "dead"
    except OSError:
        return "unknown"


def process_start_identity(pid: int) -> str:
    if pid <= 0:
        return ""
    if os.name == "nt":
        import ctypes

        query_limited_information = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(
            query_limited_information, False, pid
        )
        if not handle:
            return ""
        creation = ctypes.c_ulonglong()
        exit_time = ctypes.c_ulonglong()
        kernel = ctypes.c_ulonglong()
        user = ctypes.c_ulonglong()
        try:
            ok = ctypes.windll.kernel32.GetProcessTimes(
                handle,
                ctypes.byref(creation),
                ctypes.byref(exit_time),
                ctypes.byref(kernel),
                ctypes.byref(user),
            )
            return f"filetime:{creation.value}" if ok else ""
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    try:
        stat_path = Path(f"/proc/{pid}/stat")
        if stat_path.is_file():
            stat = stat_path.read_text(encoding="utf-8")
            close = stat.rfind(")")
            fields = stat[close + 1 :].split() if close >= 0 else stat.split()
            return f"ticks:{int(fields[19])}"
    except (OSError, IndexError, TypeError, ValueError):
        return ""
    try:
        result = subprocess.run(
            ["ps", "-o", "lstart=", "-p", str(pid)],
            capture_output=True,
            check=False,
            text=True,
            timeout=5.0,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    started = str(result.stdout or "").strip() if result.returncode == 0 else ""
    return f"ps:{started}" if started else ""


def connect(database: Path) -> sqlite3.Connection:
    database.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database, timeout=2.0, isolation_level=None)
    connection.execute("PRAGMA busy_timeout = 2000")
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS reclaim_guards (
            lock_path TEXT PRIMARY KEY,
            owner TEXT NOT NULL,
            pid INTEGER NOT NULL,
            process_identity TEXT NOT NULL DEFAULT '',
            acquired_at TEXT NOT NULL
        )
        """
    )
    columns = {
        str(row[1]) for row in connection.execute("PRAGMA table_info(reclaim_guards)")
    }
    if "process_identity" not in columns:
        try:
            connection.execute(
                "ALTER TABLE reclaim_guards ADD COLUMN "
                "process_identity TEXT NOT NULL DEFAULT ''"
            )
        except sqlite3.OperationalError:
            refreshed = {
                str(row[1])
                for row in connection.execute("PRAGMA table_info(reclaim_guards)")
            }
            if "process_identity" not in refreshed:
                raise
    return connection


def release_guard(database: Path, lock_key: str, owner: str) -> dict:
    last_error = "release failed"
    for attempt in range(3):
        connection: sqlite3.Connection | None = None
        try:
            connection = connect(database)
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "DELETE FROM reclaim_guards WHERE lock_path = ? AND owner = ?",
                (lock_key, owner),
            )
            connection.execute("COMMIT")
            return {"ok": True}
        except (OSError, sqlite3.Error) as exc:
            last_error = str(exc)
            if connection is not None:
                try:
                    connection.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
            if attempt < 2:
                time.sleep(0.05)
        finally:
            if connection is not None:
                connection.close()
    return {"ok": False, "error": last_error}


def run(action: str, database: Path, lock_key: str, owner: str, pid: int) -> dict:
    if action == "release":
        return release_guard(database, lock_key, owner)
    if action != "acquire":
        return {"ok": False, "error": f"unknown action: {action}"}

    connection = connect(database)
    try:
        claimant_identity = process_start_identity(pid)
        observed = connection.execute(
            "SELECT owner, pid, process_identity FROM reclaim_guards WHERE lock_path = ?",
            (lock_key,),
        ).fetchone()
        observed_reclaimable = False
        if observed is not None and str(observed[0]) != owner:
            observed_pid = int(observed[1])
            observed_identity = str(observed[2] or "")
            current_identity = (
                process_start_identity(observed_pid) if observed_identity else ""
            )
            observed_reclaimable = bool(
                process_state(observed_pid) == "dead"
                or (
                    observed_identity
                    and current_identity
                    and current_identity != observed_identity
                )
            )
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT owner, pid, process_identity FROM reclaim_guards WHERE lock_path = ?",
            (lock_key,),
        ).fetchone()
        timestamp = datetime.now(tz=timezone.utc).isoformat()
        if row is None:
            connection.execute(
                "INSERT INTO reclaim_guards("
                "lock_path, owner, pid, process_identity, acquired_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (lock_key, owner, pid, claimant_identity, timestamp),
            )
        elif str(row[0]) == owner:
            pass
        elif (
            observed is not None
            and tuple(row) == tuple(observed)
            and observed_reclaimable
        ):
            connection.execute(
                "UPDATE reclaim_guards SET owner = ?, pid = ?, process_identity = ?, "
                "acquired_at = ? "
                "WHERE lock_path = ? AND owner = ?",
                (
                    owner,
                    pid,
                    claimant_identity,
                    timestamp,
                    lock_key,
                    str(row[0]),
                ),
            )
        else:
            connection.execute("ROLLBACK")
            return {"ok": False, "holder": "stale_reclaim_in_progress"}
        connection.execute("COMMIT")
        return {"ok": True}
    except (OSError, sqlite3.Error, TypeError, ValueError):
        try:
            connection.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise
    finally:
        connection.close()


def main() -> int:
    if len(sys.argv) != 6:
        print(json.dumps({"ok": False, "error": "invalid bridge arguments"}))
        return 2
    action, database, lock_key, owner, raw_pid = sys.argv[1:]
    try:
        result = run(action, Path(database), lock_key, owner, int(raw_pid))
    except (OSError, sqlite3.Error, TypeError, ValueError) as exc:
        result = {"ok": False, "error": str(exc)}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("ok") or result.get("holder") else 1


if __name__ == "__main__":
    raise SystemExit(main())
