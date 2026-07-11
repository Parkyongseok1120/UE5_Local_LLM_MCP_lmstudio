from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from read_query_history import (  # noqa: E402
    consume_continuation_token,
    delivery_variant_key,
    issue_continuation_token,
    reset_query_history,
)
from workspace_paths import find_workspace_root  # noqa: E402


def test_continuation_token_single_use() -> None:
    reset_query_history()
    index = find_workspace_root() / "data" / "unreal58" / "rag.sqlite"
    key = delivery_variant_key(
        tool="unreal_rag_search",
        active_project="",
        query="LyraHealthComponent",
        mode="auto",
        scope="auto",
        detail_level="compact",
        top_k=4,
        hybrid=False,
        index_path=index,
        session_id="sess1",
    )
    token = issue_continuation_token(key)
    assert consume_continuation_token(token, key) is True
    assert consume_continuation_token(token, key) is False


def test_mcp_tools_list_matches_stable_manifest(monkeypatch, tmp_path) -> None:
    import importlib.util

    monkeypatch.setenv("MCP_ESSENTIAL_TOOLS", "1")
    monkeypatch.delenv("ALLOW_CONTROL_PLANE_TOOLS", raising=False)
    spec = importlib.util.spec_from_file_location("unreal_rag_mcp", ROOT / "scripts" / "unreal_rag_mcp.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    server = module.McpServer(tmp_path / "missing.sqlite")
    names = {tool["name"] for tool in server.all_tool_definitions()}
    manifest = json.loads((ROOT / "config" / "stable_tool_manifest.json").read_text(encoding="utf-8-sig"))
    assert names == set(manifest["ragEssential"])
    assert "unreal_task_start" not in names
