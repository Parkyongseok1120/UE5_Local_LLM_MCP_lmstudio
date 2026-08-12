from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from mcp_control_envelope import attach_control_envelope  # noqa: E402
from mcp_control_envelope import concise_control_text  # noqa: E402
from mcp_control_envelope import model_visible_control_text  # noqa: E402
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


def test_existing_server_control_survives_a_second_envelope_pass() -> None:
    payload = attach_control_envelope(
        {
            "ok": False,
            "control": {
                "version": 1,
                "taskId": "task-existing",
                "phase": "recovery",
                "status": "NeedsAction",
                "nextAction": "search_files",
                "nextActionIsTool": True,
                "retryPolicy": "once",
                "blockerFingerprint": "existing-fingerprint",
            },
        },
        tool_name="bridge",
    )
    assert payload["control"] == {
        "version": 1,
        "taskId": "task-existing",
        "phase": "recovery",
        "status": "NeedsAction",
        "nextAction": "search_files",
        "nextActionIsTool": True,
        "retryPolicy": "once",
        "blockerFingerprint": "existing-fingerprint",
    }


def test_tool_shaped_next_action_is_marked_executable_without_duplicate_hint() -> None:
    payload = attach_control_envelope(
        {
            "ok": False,
            "errorCode": "SLICE_PLAN_REQUIRED",
            "nextAction": "unreal_task_define_slices",
            "retryable": True,
        },
        tool_name="unreal_feature_intent_resolve",
    )
    assert payload["control"]["nextAction"] == "unreal_task_define_slices"
    assert payload["control"]["nextActionIsTool"] is True


def test_agent_getter_and_validator_actions_are_executable_tools() -> None:
    for next_action in ("get_active_project", "static_validate_project"):
        payload = attach_control_envelope(
            {"ok": True, "nextAction": next_action},
            tool_name="bridge",
        )
        assert payload["control"]["nextActionIsTool"] is True

    prose = attach_control_envelope(
        {"ok": False, "nextAction": "enable_or_call_unreal_agent_plan"},
        tool_name="bridge",
    )
    assert prose["control"]["nextActionIsTool"] is False


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


def test_lmstudio_text_fallback_contains_actionable_structured_data() -> None:
    payload = attach_control_envelope(
        {
            "ok": True,
            "path": {"uri": "project://Source"},
            "entries": [{"name": "O_Mock", "type": "dir"}],
        },
        tool_name="list_directory",
    )

    text = model_visible_control_text(payload, frontend="lmstudio")

    assert '"O_Mock"' in text
    assert '"project://Source"' in text
    assert '"control"' in text
    assert "Detailed result is available" not in text


def test_non_lmstudio_text_remains_concise() -> None:
    payload = attach_control_envelope(
        {"ok": True, "entries": [{"name": "x" * 5000}]},
        tool_name="list_directory",
    )

    text = model_visible_control_text(payload, frontend="cline")

    assert len(text) < 1200
    assert "structuredContent" in text


def test_lmstudio_hard_compaction_preserves_blocker_control() -> None:
    payload = attach_control_envelope(
        {
            "ok": False,
            "errorCode": "EVIDENCE_STAGNATION_REPEAT",
            "retryable": False,
            "stopCurrentWorkflow": True,
            "stopCurrentPhase": True,
            "phaseBoundary": "evidence",
            "doNotRetry": ["search_files"],
            "doNotRetryTools": ["unreal_rag_search"],
            "agentInstruction": "Do not call another evidence tool.",
            "rows": [{"text": "x" * 4_000} for _ in range(100)],
        },
        tool_name="search_files",
    )

    text = model_visible_control_text(payload, frontend="lmstudio", max_chars=2_500)
    visible = json.loads(text)

    assert len(text) <= 2_500
    assert visible["errorCode"] == "EVIDENCE_STAGNATION_REPEAT"
    assert visible["control"]["retryPolicy"] == "forbidden"
    assert visible["stopCurrentWorkflow"] is True
    assert visible["stopCurrentPhase"] is True
    assert visible["phaseBoundary"] == "evidence"
    assert visible["doNotRetryTools"] == ["unreal_rag_search"]
    assert visible["agentInstruction"] == "Do not call another evidence tool."


def test_lmstudio_extreme_control_strings_stay_valid_and_bounded() -> None:
    payload = attach_control_envelope(
        {
            "ok": False,
            "errorCode": "BLOCKED",
            "retryable": False,
            "stopCurrentWorkflow": True,
            "agentInstruction": "stop " * 10_000,
            "control": {
                "phase": "phase " * 10_000,
                "continuationToken": "token " * 10_000,
            },
            "rows": [{"text": "x" * 10_000} for _ in range(100)],
        },
        tool_name="search_files",
    )

    text = model_visible_control_text(payload, frontend="lmstudio", max_chars=2_000)
    visible = json.loads(text)

    assert len(text) <= 2_000
    assert visible["errorCode"] == "BLOCKED"
    assert visible["control"]["retryPolicy"] == "forbidden"
    assert visible["stopCurrentWorkflow"] is True
