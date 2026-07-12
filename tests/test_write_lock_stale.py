from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from write_locks import _is_stale_lock, lock_file_path, try_acquire_cross_process_lock  # noqa: E402


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
