from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from task_api import (  # noqa: E402
    task_list_active,
    task_retry_job_cancel,
    task_root,
    task_start,
)


def test_conversation_ids_isolate_task_ownership(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    monkeypatch.setenv("MCP_BRIDGE_PAIR_ID", "bridge-conversation-test")
    monkeypatch.setenv("MCP_CLIENT_INSTANCE_ID", "client-conversation-test")
    monkeypatch.delenv("MCP_SESSION_ID", raising=False)
    monkeypatch.delenv("MCP_CONVERSATION_ID", raising=False)
    monkeypatch.delenv("MCP_CONNECTION_ID", raising=False)

    chat_a = task_start(
        tmp_path,
        request="chat a",
        conversation_id="conv-aaaa",
        start_background_job=False,
    )
    chat_b = task_start(
        tmp_path,
        request="chat b",
        conversation_id="conv-bbbb",
        start_background_job=False,
    )
    assert chat_a["ok"] is True
    assert chat_b["ok"] is True
    assert chat_a["state"]["conversationId"] == "conv-aaaa"
    assert chat_b["state"]["conversationId"] == "conv-bbbb"
    assert chat_a["state"]["mcpConnectionId"] != chat_b["state"]["mcpConnectionId"]

    listed_a = task_list_active(tmp_path, conversation_id="conv-aaaa")
    listed_b = task_list_active(tmp_path, conversation_id="conv-bbbb")
    own_a = [t for t in listed_a["tasks"] if t.get("connectionMatches")]
    own_b = [t for t in listed_b["tasks"] if t.get("connectionMatches")]
    assert len(own_a) == 1
    assert own_a[0]["taskSessionId"] == chat_a["taskSessionId"]
    assert len(own_b) == 1
    assert own_b[0]["taskSessionId"] == chat_b["taskSessionId"]

    # Without conversationId, conversation-scoped tasks do not match.
    listed_none = task_list_active(tmp_path)
    assert all(t.get("connectionMatches") is False for t in listed_none["tasks"] if t.get("status") == "running")


def test_retry_cancel_syncs_uncertain_task(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    started = task_start(
        tmp_path,
        request="uncertain sync",
        conversation_id="conv-sync-01",
        start_background_job=False,
    )
    task_id = str(started["taskSessionId"])
    state_path = task_root(tmp_path, task_id) / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["status"] = "cancellation_uncertain"
    state["terminalLogged"] = True
    state_path.write_text(json.dumps(state), encoding="utf-8")

    result = task_retry_job_cancel(
        tmp_path,
        task_session_id=task_id,
        conversation_id="conv-sync-01",
    )
    assert result["ok"] is True
    assert result["nextAction"] == "unreal_agent_plan"
    synced = json.loads(state_path.read_text(encoding="utf-8"))
    assert synced["status"] == "cancelled"
    assert synced.get("orphanProcessSuspected") is False


def test_corrupt_task_owner_requires_force(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    task_id = "corrupt_owner_task01"
    task_dir = tmp_path / "state" / "tasks" / task_id
    task_dir.mkdir(parents=True)
    (task_dir / "state.json").write_text("{", encoding="utf-8")
    (task_dir / "workspace-root.txt").write_text(str(tmp_path.resolve()), encoding="utf-8")

    denied = task_retry_job_cancel(tmp_path, task_session_id=task_id)
    assert denied["ok"] is False
    assert denied["errorCode"] == "TASK_OWNER_UNVERIFIABLE"
