from __future__ import annotations

import io
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from direct_rag_history import (  # noqa: E402
    forget,
    query_keys,
    receipt_matches,
    record,
)
from direct_rag_server import DirectRagServer  # noqa: E402


def test_direct_repeat_receipt_is_state_bound(monkeypatch, tmp_path: Path) -> None:
    history = tmp_path / "direct-rag-history.json"
    index = tmp_path / "rag.sqlite"
    index.write_bytes(b"direct-index-fixture")
    monkeypatch.setenv("DIRECT_RAG_HISTORY_PATH", str(history))
    semantic, variant = query_keys(
        tool="unreal_rag_search",
        active_project="",
        projects=[],
        query=f"LyraHealthComponent {tmp_path.name}",
        mode="auto",
        scope="auto",
        detail="compact",
        top_k=4,
        hybrid=False,
        index=index,
    )
    receipt = record(semantic, variant, "compact", match_count=1)
    assert receipt_matches("", variant) is False
    assert receipt_matches(receipt, variant) is True
    assert forget(variant) is True


def test_direct_rag_tools_list_matches_stable_manifest(tmp_path: Path) -> None:
    output = io.StringIO()
    server = DirectRagServer(
        tmp_path / "missing.sqlite",
        workspace=tmp_path,
        output_stream=output,
    )
    server.handle_message({"jsonrpc": "2.0", "id": 7, "method": "tools/list"})
    response = json.loads(output.getvalue())
    tools = response["result"]["tools"]
    names = {tool["name"] for tool in tools}
    manifest = json.loads((ROOT / "config" / "stable_tool_manifest.json").read_text(encoding="utf-8-sig"))
    assert names == set(manifest["ragEssential"])
    assert not any(name.startswith("unreal_task_") for name in names)
    serialized = json.dumps(tools, ensure_ascii=False)
    for legacy_control in (
        "taskAuthorization",
        "requiredNextTool",
        "nextAction",
        "gatePassed",
    ):
        assert legacy_control not in serialized
