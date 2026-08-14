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


def test_task_control_v2_projects_one_exact_required_tool() -> None:
    payload = attach_control_envelope(
        {
            "ok": True,
            "taskSessionId": "task-v2",
            "controlEpoch": 7,
            "toolRoute": {
                "phase": "implementation",
                "routeHash": "route-7",
                "activeTools": ["read_file", "replace_in_file"],
            },
            "requiredNextTool": {
                "name": "replace_in_file",
                "args": {"path": "Source/Demo.cpp"},
            },
        },
        tool_name="task_api",
    )

    assert payload["control"] == {
        "version": 2,
        "epoch": 7,
        "taskSessionId": "task-v2",
        "routeHash": "route-7",
        "phase": "implementation",
        "disposition": "require_tool",
        "requiredTool": {
            "name": "replace_in_file",
            "args": {"path": "Source/Demo.cpp"},
        },
        "allowedTools": ["replace_in_file"],
        "retryPolicy": {"sameSemanticInput": "allowed"},
    }


def test_task_control_v2_survives_a_second_transport_pass() -> None:
    first = attach_control_envelope(
        {
            "ok": True,
            "taskSessionId": "task-v2",
            "controlEpoch": 8,
            "toolRoute": {
                "phase": "validation",
                "routeHash": "route-8",
                "activeTools": ["build_project", "run_tests"],
            },
            "requiredNextTool": "build_project",
        },
        tool_name="task_api",
    )
    second = attach_control_envelope(first, tool_name="bridge")

    assert second["control"] == first["control"]
    assert second["control"]["requiredTool"]["name"] == "build_project"
    assert second["control"]["allowedTools"] == ["build_project"]


def test_rc2_replay_a_repeated_gate_blocker_forces_rediscovery_without_stale_action() -> None:
    payload = attach_control_envelope(
        {
            "ok": False,
            "taskSessionId": "task-v2",
            "controlEpoch": 9,
            "errorCode": "REPEATED_GATE_BLOCKER",
            "retryable": False,
            "requiredNextTool": "replace_in_file",
            "toolRoute": {
                "phase": "implementation",
                "routeHash": "route-9",
                "activeTools": [
                    "search_files",
                    "read_file_range",
                    "replace_in_file",
                ],
            },
        },
        tool_name="task_api",
    )

    assert payload["control"]["disposition"] == "rediscover"
    assert "requiredTool" not in payload["control"]
    assert payload["control"]["allowedTools"] == [
        "search_files",
        "read_file_range",
    ]
    assert payload["control"]["retryPolicy"] == {
        "sameSemanticInput": "forbidden"
    }
    assert payload["control"]["blocker"]["code"] == "REPEATED_GATE_BLOCKER"


def test_terminal_task_control_exposes_no_tools() -> None:
    payload = attach_control_envelope(
        {
            "ok": True,
            "taskSessionId": "task-v2",
            "controlEpoch": 10,
            "status": "completed",
            "taskRouteTerminal": True,
            "toolRoute": {
                "phase": "complete",
                "routeHash": "route-10",
                "activeTools": ["read_file"],
            },
        },
        tool_name="task_api",
    )

    assert payload["control"]["disposition"] == "complete"
    assert payload["control"]["allowedTools"] == []
    assert "requiredTool" not in payload["control"]
    assert "blocker" not in payload["control"]


def test_tool_shaped_next_action_requires_an_explicit_executable_contract() -> None:
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
    assert payload["control"]["nextActionIsTool"] is False


def test_agent_getter_and_validator_actions_are_executable_only_when_declared() -> None:
    for next_action in ("get_active_project", "static_validate_project"):
        payload = attach_control_envelope(
            {"ok": True, "nextAction": next_action, "nextActionIsTool": True},
            tool_name="bridge",
        )
        assert payload["control"]["nextActionIsTool"] is True

    prose = attach_control_envelope(
        {"ok": False, "nextAction": "enable_or_call_unreal_agent_plan"},
        tool_name="bridge",
    )
    assert prose["control"]["nextActionIsTool"] is False


def test_informational_read_action_never_becomes_a_fake_tool_gate() -> None:
    payload = attach_control_envelope(
        {"ok": True, "requiredNextAction": "read_project_source_or_answer"},
        tool_name="unreal_rag_search",
    )

    assert payload["control"]["nextAction"] == "read_project_source_or_answer"
    assert payload["control"]["nextActionIsTool"] is False


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
