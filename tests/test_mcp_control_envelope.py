from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from mcp_control_envelope import attach_control_envelope  # noqa: E402
from mcp_control_envelope import concise_control_text  # noqa: E402
from mcp_tool_compact import compact_structured_payload  # noqa: E402


def test_control_envelope_normalizes_recovery_without_guessing_tool_names() -> None:
    payload = attach_control_envelope(
        {
            "ok": False,
            "errorCode": "FEATURE_INTENT_BLOCKING_QUESTIONS",
            "nextAction": "answer_feature_questions",
            "nextActionIsTool": False,
            "retryable": True,
            "blockers": [{"symbol": "ownership", "verdict": "blocked"}],
            "taskAuthorization": {
                "taskSessionId": "task-1234",
                "ownerCapability": "owner-secret",
            },
        },
        tool_name="unreal_feature_intent_resolve",
    )

    assert payload["control"]["taskId"] == "task-1234"
    assert payload["control"]["phase"] == "unreal_feature_intent_resolve"
    assert payload["control"]["status"] == "Blocked"
    assert payload["control"]["nextAction"] == "answer_feature_questions"
    assert payload["control"]["nextActionIsTool"] is False
    assert payload["control"]["retryPolicy"] == "once"
    assert len(payload["control"]["blockerFingerprint"]) == 24
    assert "owner-secret" not in str(payload["control"])


def test_control_envelope_survives_hard_structured_compaction() -> None:
    payload = attach_control_envelope(
        {
            "ok": False,
            "errorCode": "DEMO_BLOCKER",
            "requiredNextTool": "unreal_task_checkpoint",
            "rows": [{"text": "x" * 1000} for _ in range(100)],
        },
        tool_name="demo_tool",
    )

    compact = compact_structured_payload(payload, max_bytes=200)

    assert compact["control"] == payload["control"]
    assert compact["_structuredTruncated"] is True


def test_concise_text_does_not_duplicate_large_structured_payload() -> None:
    payload = attach_control_envelope(
        {
            "ok": False,
            "errorCode": "DEMO_ERROR",
            "error": "x" * 5000,
            "rows": [{"value": "y" * 1000} for _ in range(100)],
        },
        tool_name="demo_tool",
    )

    text = concise_control_text(payload)

    assert len(text) < 1200
    assert "structuredContent" in text
    assert "DEMO_ERROR" in text
