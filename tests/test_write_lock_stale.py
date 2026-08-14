from __future__ import annotations

import json
import shutil
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
    first.parent.mkdir(parents=True)
    second.parent.mkdir(parents=True)
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
