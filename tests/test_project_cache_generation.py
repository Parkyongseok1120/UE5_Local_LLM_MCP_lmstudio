from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from project_switch_invalidate import (  # noqa: E402
    clear_local_project_caches,
    publish_project_switch_generation,
    read_cache_generation,
)
from unreal_rag_mcp import McpServer  # noqa: E402


def test_publish_increments_once_per_switch(tmp_path: Path) -> None:
    before = read_cache_generation(tmp_path)
    after = publish_project_switch_generation(tmp_path)
    assert after == before + 1 or after > before


def test_observer_clear_does_not_increment_generation(tmp_path: Path) -> None:
    gen = publish_project_switch_generation(tmp_path)
    clear_local_project_caches(tmp_path, previous_project=None, new_project=None)
    assert read_cache_generation(tmp_path) == gen


def test_same_server_many_tool_calls_keeps_generation(tmp_path: Path) -> None:
    index = tmp_path / "rag.sqlite"
    index.write_bytes(b"")
    server = McpServer(index)
    server.workspace = tmp_path
    start = publish_project_switch_generation(tmp_path)
    server._cache_generation = start
    for _ in range(100):
        server._maybe_refresh_project_caches()
    assert read_cache_generation(tmp_path) == start


def test_observer_syncs_after_external_publish(tmp_path: Path) -> None:
    index = tmp_path / "rag.sqlite"
    index.write_bytes(b"")
    server = McpServer(index)
    server.workspace = tmp_path
    server._cache_generation = read_cache_generation(tmp_path)
    new_gen = publish_project_switch_generation(tmp_path)
    server._maybe_refresh_project_caches()
    assert server._cache_generation == new_gen
    assert read_cache_generation(tmp_path) == new_gen


def test_dual_workspace_publish_does_not_ping_pong(tmp_path: Path) -> None:
    ws_a = tmp_path / "a"
    ws_b = tmp_path / "b"
    ws_a.mkdir()
    ws_b.mkdir()
    index = ws_a / "rag.sqlite"
    index.write_bytes(b"")
    server = McpServer(index)
    server.workspace = ws_a
    server._cache_generation = read_cache_generation(ws_a)
    gen = publish_project_switch_generation(ws_a)
    server._maybe_refresh_project_caches()
    assert read_cache_generation(ws_a) == gen
    server._maybe_refresh_project_caches()
    assert read_cache_generation(ws_a) == gen
