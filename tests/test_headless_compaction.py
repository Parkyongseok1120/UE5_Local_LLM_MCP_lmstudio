"""Headless input uses the core's complete exchanges and preserves evidence roles."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

import headless_mcp_chat as chat

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")
ADAPTER = ROOT / "scripts/headless_compact.js"
pytestmark = pytest.mark.skipif(not NODE, reason="Node is required for the real compaction adapter")


def history():
    messages = [{"role": "system", "content": "Keep the project contract."},
                {"role": "user", "content": "Review the source."},
                {"role": "assistant", "content": "Cache hypothesis was rejected."}]
    for i in range(40):
        messages += [
            {"role": "assistant", "content": None, "tool_calls": [{"id": f"r{i}", "type": "function",
             "function": {"name": "read_file", "arguments": json.dumps({"path": f"Source/File{i}.cpp"})}}]},
            {"role": "tool", "tool_call_id": f"r{i}", "content": json.dumps({"ok": True,
             "path": f"Source/File{i}.cpp", "content": "int Value;\n" * 300})},
        ]
    return messages


def test_long_current_turn_compacts_and_keeps_complete_exchanges():
    messages = history()
    completed = subprocess.run([NODE, str(ADAPTER)], input=json.dumps({"messages": messages,
        "measurement": {"remainingTokens": -5000}, "options": {"maxCheckpointChars": 6000}}),
        capture_output=True, text=True, encoding="utf-8", check=True, timeout=15)
    result = json.loads(completed.stdout)
    assert result["compacted"] and len(result["messages"]) < len(messages)
    assert any(m["role"] == "user" and m["content"] == "Review the source." for m in result["messages"])
    requests = {t["id"] for m in result["messages"] for t in m.get("tool_calls", [])}
    replies = {m["tool_call_id"] for m in result["messages"] if m["role"] == "tool"}
    assert requests == replies
    system = "\n".join(m["content"] for m in result["messages"] if m["role"] == "system")
    assert "Keep the project contract." in system


def test_assistant_checkpoint_is_preserved_in_its_original_authority_role():
    messages = [{"role": "user", "content": "Investigate the actor lifetime crash."},
                {"role": "assistant", "content": "Cache hypothesis was rejected. Need to check the actor lifetime."},
                {"role": "user", "content": "Continue."}]
    completed = subprocess.run([NODE, str(ADAPTER)], input=json.dumps({"messages": messages,
        "measurement": {"remainingTokens": -5000},
        "options": {"recentCompleteTurns": 0, "maxCurrentTurnMessages": 0, "maxCheckpointChars": 12000}}),
        capture_output=True, text=True, encoding="utf-8", check=True, timeout=15)
    result = json.loads(completed.stdout)
    system = "\n".join(m["content"] for m in result["messages"] if m["role"] == "system")
    assistant = "\n".join(m["content"] or "" for m in result["messages"] if m["role"] == "assistant")
    assert "Cache hypothesis was rejected" in assistant
    assert "Cache hypothesis was rejected" not in system


def test_compacted_input_is_reestimated():
    messages = chat.compact_in_background(history(), [], node=NODE, adapter=ADAPTER,
        context_length=20000, soft_remaining_tokens=12000, compact_above_messages=20)
    estimate = (len(json.dumps(messages, ensure_ascii=False)) + 2) // 4
    assert estimate + 8192 + 1536 <= 20000


def test_unfit_current_user_input_never_reaches_model(monkeypatch):
    sent = []
    monkeypatch.setattr(chat, "post_chat", lambda *a, **k: sent.append(a))
    with pytest.raises(RuntimeError, match="model request was not sent"):
        chat.run_turn(messages=[], prompt="p" * 100000, model="fixture", endpoint="unused",
            tool_definitions=[], tool_owners={}, timeout=1, max_rounds=1, compactor_node=NODE,
            compactor_adapter=ADAPTER, context_length=16000, soft_remaining_tokens=12000,
            compact_above_messages=20, stream=False)
    assert sent == []
