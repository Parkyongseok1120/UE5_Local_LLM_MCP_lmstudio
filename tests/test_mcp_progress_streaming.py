from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from unreal_rag_mcp import McpServer  # noqa: E402


def test_progress_token_streams_phases_heartbeats_and_completion(tmp_path: Path) -> None:
    server = McpServer(tmp_path / "missing.sqlite")
    sent: list[dict] = []
    server.send = sent.append

    server._begin_tool_progress(
        41,
        "unreal_architecture_reasoning",
        {"_meta": {"progressToken": "progress-41"}},
        interval_seconds=0.01,
    )
    server.progress_phase(41, "Architecture validation")
    time.sleep(0.035)
    server.result(41, {"ok": True})
    count_at_result = len(sent)
    time.sleep(0.025)

    notifications = [
        item for item in sent if item.get("method") == "notifications/progress"
    ]
    assert len(notifications) >= 4
    assert all(
        item["params"]["progressToken"] == "progress-41"
        for item in notifications
    )
    progress_values = [item["params"]["progress"] for item in notifications]
    assert progress_values == sorted(progress_values)
    assert any(
        "Architecture validation" in item["params"]["message"]
        for item in notifications
    )
    assert "completed" in notifications[-1]["params"]["message"]
    assert sent[-1] == {"jsonrpc": "2.0", "id": 41, "result": {"ok": True}}
    assert len(sent) == count_at_result


def test_long_tool_without_progress_token_emits_logging_heartbeat(tmp_path: Path) -> None:
    server = McpServer(tmp_path / "missing.sqlite")
    sent: list[dict] = []
    server.send = sent.append

    server._begin_tool_progress(
        42,
        "unreal_code_sketch_claim_validate",
        {},
        interval_seconds=0.01,
    )
    time.sleep(0.025)
    server.result(42, {"ok": True})

    logs = [item for item in sent if item.get("method") == "notifications/message"]
    assert logs
    assert "unreal_code_sketch_claim_validate" in logs[0]["params"]["data"]
    assert "elapsed" in logs[0]["params"]["data"]
    assert sent[-1]["id"] == 42


def test_cold_symbol_lookup_without_progress_token_emits_heartbeat(tmp_path: Path) -> None:
    server = McpServer(tmp_path / "missing.sqlite")
    sent: list[dict] = []
    server.send = sent.append

    server._begin_tool_progress(
        44,
        "unreal_symbol_lookup",
        {},
        interval_seconds=0.01,
    )
    time.sleep(0.025)
    server.result(44, {"ok": True})

    logs = [item for item in sent if item.get("method") == "notifications/message"]
    assert logs
    assert "Unreal and project symbol lookup" in logs[0]["params"]["data"]
    assert "elapsed" in logs[0]["params"]["data"]
    assert sent[-1]["id"] == 44


def test_progress_interval_is_bounded_to_two_through_five_seconds(
    tmp_path: Path,
    monkeypatch,
) -> None:
    server = McpServer(tmp_path / "missing.sqlite")
    monkeypatch.setenv("MCP_PROGRESS_INTERVAL_SECONDS", "0.1")
    assert server._progress_interval_seconds() == 2.0
    monkeypatch.setenv("MCP_PROGRESS_INTERVAL_SECONDS", "99")
    assert server._progress_interval_seconds() == 5.0
    monkeypatch.setenv("MCP_PROGRESS_INTERVAL_SECONDS", "invalid")
    assert server._progress_interval_seconds() == 3.0


def test_initialize_advertises_logging_for_tokenless_progress_fallback(
    tmp_path: Path,
) -> None:
    server = McpServer(tmp_path / "missing.sqlite")
    sent: list[dict] = []
    server.send = sent.append

    server.handle_message(
        {
            "jsonrpc": "2.0",
            "id": 43,
            "method": "initialize",
            "params": {"protocolVersion": "2025-06-18"},
        }
    )

    capabilities = sent[-1]["result"]["capabilities"]
    assert capabilities["logging"] == {}
    assert capabilities["tools"]["listChanged"] is True
