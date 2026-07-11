from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from mcp_tool_compact import envelope_fields  # noqa: E402
from tool_exposure import tool_not_callable_payload  # noqa: E402


def test_envelope_fields_minimal() -> None:
    payload = envelope_fields(phase="status", user_message="Ready.")
    assert payload["phase"] == "status"
    assert payload["userMessage"] == "Ready."


def test_tool_not_callable_payload_has_envelope() -> None:
    payload = tool_not_callable_payload("unreal_task_start")
    assert payload["errorCode"] == "TOOL_NOT_CALLABLE"
    assert payload["userMessage"]
    assert payload["agentInstruction"]
    assert payload["phase"] == "failed"
