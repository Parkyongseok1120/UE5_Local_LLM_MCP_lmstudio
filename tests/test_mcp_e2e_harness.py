from __future__ import annotations

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


def test_mcp_tools_list_includes_task_tools() -> None:
    from unreal_rag_mcp import McpServer

    index = ROOT / "data" / "unreal58" / "rag.sqlite"
    server = McpServer(index)
    names = [tool["name"] for tool in server.all_tool_definitions()]
    for tool in (
        "unreal_task_start",
        "unreal_task_status",
        "unreal_project_status",
        "unreal_job_log_read",
    ):
        assert tool in names
