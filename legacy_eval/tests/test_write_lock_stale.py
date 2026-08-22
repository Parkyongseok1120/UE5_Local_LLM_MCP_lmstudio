# Archived with the unused Python write-lock implementation.
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from write_locks import (  # noqa: E402
    _canonical_lock_key,
    _is_stale_lock,
    lock_file_path,
    release_cross_process_lock,
    try_acquire_cross_process_lock,
)


def test_lock_identity_is_host_aware_without_unicode_case_folding(tmp_path: Path) -> None:
    dotted_capital_i = tmp_path / "Source" / "\u0130" / "Thing.cpp"
    decomposed_dotted_i = tmp_path / "Source" / "I\u0307" / "Thing.cpp"
    for host_platform in ("linux", "darwin", "win32"):
        assert _canonical_lock_key(dotted_capital_i, host_platform) != _canonical_lock_key(
            decomposed_dotted_i, host_platform
        )
    assert _canonical_lock_key(tmp_path / "Source" / "FOO.cpp", "win32") == _canonical_lock_key(
        tmp_path / "source" / "foo.cpp", "win32"
    )
    assert _canonical_lock_key(tmp_path / "Source" / "FOO.cpp", "linux") != _canonical_lock_key(
        tmp_path / "source" / "foo.cpp", "linux"
    )


def test_node_python_lock_identity_parity(tmp_path: Path) -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("node executable not available")
    cases = [
        {"path": str(tmp_path / "Source" / "\u0130" / "Thing.cpp"), "host": "win32"},
        {"path": str(tmp_path / "Source" / "I\u0307" / "Thing.cpp"), "host": "win32"},
        {"path": str(tmp_path / "Source" / "FOO.cpp"), "host": "linux"},
        {"path": str(tmp_path / "source" / "foo.cpp"), "host": "linux"},
    ]
    module_path = ROOT / "lmstudio-unreal-agent-mcp" / "src" / "write-locks.js"
    script = """
const { canonicalLockKey } = require(process.argv[1]);
const cases = JSON.parse(process.argv[2]);
process.stdout.write(JSON.stringify(cases.map(item => canonicalLockKey(item.path, item.host))));
"""
    completed = subprocess.run(
        [node, "-e", script, str(module_path), json.dumps(cases, ensure_ascii=False)],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    node_keys = json.loads(completed.stdout)
    python_keys = [_canonical_lock_key(Path(item["path"]), item["host"]) for item in cases]
    assert node_keys == python_keys


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permits both spellings as separate files")
def test_posix_locks_keep_canonically_similar_i_dot_files_independent(tmp_path: Path) -> None:
    state_root = tmp_path / "state"
    first = tmp_path / "Source" / "\u0130" / "Thing.cpp"
    second = tmp_path / "Source" / "I\u0307" / "Thing.cpp"
    first.parent.mkdir(parents=True, exist_ok=True)
    second.parent.mkdir(parents=True, exist_ok=True)
    if first.parent.samefile(second.parent):
        pytest.skip("host filesystem aliases the two Unicode spellings")
    first.write_text("first", encoding="utf-8")
    second.write_text("second", encoding="utf-8")
    try:
        assert lock_file_path(first, state_root) != lock_file_path(second, state_root)
        assert try_acquire_cross_process_lock(first, label="first", state_root=state_root).get("ok") is True
        assert try_acquire_cross_process_lock(second, label="second", state_root=state_root).get("ok") is True
    finally:
        release_cross_process_lock(second, state_root)
        release_cross_process_lock(first, state_root)


def test_dead_pid_lock_is_stale_immediately(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    target = tmp_path / "sample.txt"
    target.write_text("x", encoding="utf-8")
    lock_path = lock_file_path(target)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text("999999:deadbeef\nwrite\n", encoding="utf-8")
    monkeypatch.setattr("write_locks._process_alive", lambda _pid: "dead")
    assert _is_stale_lock(lock_path) is True
    acquired = try_acquire_cross_process_lock(target, label="test")
    assert acquired.get("ok") is True


def test_reused_live_pid_lock_is_stale_by_birth_identity(
    tmp_path: Path,
    monkeypatch,
) -> None:
    state_root = tmp_path / "state"
    target = tmp_path / "sample.txt"
    target.write_text("x", encoding="utf-8")
    lock_path = lock_file_path(target, state_root)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text(
        f"{os.getpid()}:old-owner\nwrite\nold\nprocessIdentity:reused:old\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("write_locks._process_alive", lambda _pid: "alive")
    assert _is_stale_lock(lock_path) is True


def test_two_python_stale_reclaimers_cannot_both_acquire(tmp_path: Path) -> None:
    state_root = tmp_path / "state"
    target = tmp_path / "sample.txt"
    target.write_text("x", encoding="utf-8")
    lock_path = lock_file_path(target, state_root)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text("999999:dead-owner\nwrite\nold\n", encoding="utf-8")
    child_script = """
import json, sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from write_locks import try_acquire_cross_process_lock, release_cross_process_lock
target = Path(sys.argv[2])
state_root = Path(sys.argv[3])
result = try_acquire_cross_process_lock(target, label="race", state_root=state_root)
print(json.dumps(result), flush=True)
if result.get("ok"):
    time.sleep(1.2)
    release_cross_process_lock(target, state_root)
"""
    commands = [
        sys.executable,
        "-c",
        child_script,
        str(ROOT / "scripts"),
        str(target),
        str(state_root),
    ]
    children = [
        subprocess.Popen(
            commands,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        )
        for _ in range(2)
    ]
    results = []
    for child in children:
        stdout, stderr = child.communicate(timeout=10)
        assert child.returncode == 0, stderr
        results.append(json.loads(stdout.strip()))
    assert sum(result.get("ok") is True for result in results) == 1
    assert sum(result.get("ok") is not True for result in results) == 1


def test_dead_python_stale_reclaimer_is_transactionally_replaced(
    tmp_path: Path,
    monkeypatch,
) -> None:
    state_root = tmp_path / "state"
    target = tmp_path / "sample.txt"
    target.write_text("x", encoding="utf-8")
    lock_path = lock_file_path(target, state_root)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text("999999:dead-lock-owner\nwrite\nold\n", encoding="utf-8")
    database = lock_path.parent / "stale-reclaim.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE reclaim_guards ("
            "lock_path TEXT PRIMARY KEY, owner TEXT NOT NULL, pid INTEGER NOT NULL, "
            "acquired_at TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO reclaim_guards VALUES (?, ?, ?, ?)",
            (lock_path.name, "999999:dead-reclaimer", 999999, "old"),
        )
    monkeypatch.setattr("write_locks._process_alive", lambda _pid: "dead")

    acquired = try_acquire_cross_process_lock(
        target,
        label="replacement",
        state_root=state_root,
    )
    try:
        assert acquired.get("ok") is True
        assert acquired.get("staleReclaimed") is True
    finally:
        release_cross_process_lock(target, state_root)


def test_node_python_stale_reclaimers_share_one_transactional_guard(
    tmp_path: Path,
) -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("node executable not available")
    state_root = tmp_path / "state"
    target = tmp_path / "sample.txt"
    target.write_text("x", encoding="utf-8")
    lock_path = lock_file_path(target, state_root)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text("999999:dead-owner\nwrite\nold\n", encoding="utf-8")
    module_path = ROOT / "lmstudio-unreal-agent-mcp" / "src" / "write-locks.js"
    node_script = """
const locks = require(process.argv[1]);
const result = locks.tryAcquireCrossProcessLock(process.argv[2], "node-race", process.argv[3]);
process.stdout.write(JSON.stringify(result));
if (result.ok) setTimeout(() => locks.releaseCrossProcessLock(process.argv[2], process.argv[3]), 1200);
"""
    python_script = """
import json, sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from write_locks import try_acquire_cross_process_lock, release_cross_process_lock
target = Path(sys.argv[2])
state_root = Path(sys.argv[3])
result = try_acquire_cross_process_lock(target, label="python-race", state_root=state_root)
print(json.dumps(result), flush=True)
if result.get("ok"):
    time.sleep(1.2)
    release_cross_process_lock(target, state_root)
"""
    children = [
        subprocess.Popen(
            [
                node,
                "-e",
                node_script,
                str(module_path),
                str(target),
                (
                    str(state_root).swapcase()
                    if sys.platform == "win32"
                    else str(state_root)
                ),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        ),
        subprocess.Popen(
            [
                sys.executable,
                "-c",
                python_script,
                str(ROOT / "scripts"),
                str(target),
                str(state_root),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        ),
    ]
    results = []
    for child in children:
        stdout, stderr = child.communicate(timeout=10)
        assert child.returncode == 0, stderr
        results.append(json.loads(stdout.strip()))
    assert sum(result.get("ok") is True for result in results) == 1
    assert sum(result.get("ok") is not True for result in results) == 1


def test_reused_pid_with_different_birth_identity_does_not_strand_guard(
    tmp_path: Path,
    monkeypatch,
) -> None:
    state_root = tmp_path / "state"
    target = tmp_path / "sample.txt"
    target.write_text("x", encoding="utf-8")
    lock_path = lock_file_path(target, state_root)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text("999999:dead-lock-owner\nwrite\nold\n", encoding="utf-8")
    database = lock_path.parent / "stale-reclaim.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE reclaim_guards ("
            "lock_path TEXT PRIMARY KEY, owner TEXT NOT NULL, pid INTEGER NOT NULL, "
            "process_identity TEXT NOT NULL DEFAULT '', acquired_at TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO reclaim_guards VALUES (?, ?, ?, ?, ?)",
            (lock_path.name, "old-process", os.getpid(), "reused:old", "old"),
        )
    monkeypatch.setattr(
        "write_locks._process_alive",
        lambda pid: "dead" if pid == 999999 else "alive",
    )

    acquired = try_acquire_cross_process_lock(
        target,
        label="replacement",
        state_root=state_root,
    )
    try:
        assert acquired.get("ok") is True
        assert acquired.get("staleReclaimed") is True
    finally:
        release_cross_process_lock(target, state_root)
