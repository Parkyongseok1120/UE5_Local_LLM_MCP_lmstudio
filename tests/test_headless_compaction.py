"""Headless input uses the core's complete exchanges and preserves evidence roles."""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import headless_mcp_chat as chat
import pytest

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")
ADAPTER = ROOT / "scripts/headless_compact.js"
pytestmark = pytest.mark.skipif(not NODE, reason="Node is required for the real compaction adapter")


@pytest.mark.parametrize("failure", [chat.McpError("initialize rejected"), EOFError("closed"), TimeoutError("deadline"), KeyboardInterrupt()])
def test_failed_start_reaps_owned_child_and_preserves_error(monkeypatch, failure):
    created = []
    original = subprocess.Popen

    def track(*args, **kwargs):
        process = original(*args, **kwargs)
        created.append(process)
        return process

    def reject(*_args):
        raise failure

    monkeypatch.setattr(chat.subprocess, "Popen", track)
    monkeypatch.setattr(chat.McpProcess, "request", reject)
    client = chat.McpProcess("fixture", {"command": sys.executable,
        "args": ["-u", "-c", "import sys; sys.stdin.read()"]}, timeout=0.1)
    try:
        with pytest.raises(type(failure)) as caught:
            client.start()
        assert caught.value is failure
        assert len(created) == 1 and created[0].poll() is not None
        assert created[0].stdin.closed and created[0].stdout.closed
        assert client.process is None
        client.close()
    finally:
        for process in created:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)


def test_second_server_initialization_failure_cleans_first_server(monkeypatch, tmp_path):
    config = tmp_path / "mcp.json"
    config.write_text(json.dumps({"mcpServers": {"one": {}, "two": {}}}), encoding="utf-8")
    closed = []

    def start(self):
        if self.label == "two":
            raise chat.McpError("second initialize failed")

    monkeypatch.setattr(chat.McpProcess, "start", start)
    monkeypatch.setattr(chat.McpProcess, "tools", lambda self: [{"name": self.label, "inputSchema": {}}])
    monkeypatch.setattr(chat.McpProcess, "close", lambda self: closed.append(self.label))
    monkeypatch.setattr(sys, "argv", ["headless", "--mcp-config", str(config), "--list-tools"])
    with pytest.raises(chat.McpError, match="second initialize"):
        chat.main()
    assert closed == ["two", "one"]


def test_close_reaps_after_kill_when_graceful_and_terminate_waits_timeout():
    from unittest.mock import Mock
    process = Mock()
    process.stdin = process.stdout = process.stderr = None
    process.wait.side_effect = [subprocess.TimeoutExpired("child", 2), subprocess.TimeoutExpired("child", 2), 0]
    process.poll.return_value = 0
    client = chat.McpProcess("fixture", {}, 1, process=process)
    client.close()
    process.terminate.assert_called_once()
    process.kill.assert_called_once()
    assert process.wait.call_count == 3
    assert client.process is None


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
