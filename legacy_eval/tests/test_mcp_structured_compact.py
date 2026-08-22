from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from mcp_tool_compact import (  # noqa: E402
    compact_agent_plan_payload,
    compact_code_sketch_payload,
    compact_structured_payload,
)


def test_compact_structured_payload_stays_valid_json() -> None:
    payload = {
        "ok": True,
        "results": [{"text": "x" * 5000, "path": f"/p/{idx}"} for idx in range(200)],
        "notes": "y" * 9000,
    }
    compact = compact_structured_payload(payload, max_bytes=8000)
    serialized = json.dumps(compact, ensure_ascii=False)
    assert len(serialized) <= 8000
    assert compact.get("error") != "structuredContent could not be serialized"
    assert compact.get("_structuredTruncated") is True


def test_rag_match_count_is_not_misclassified_as_asset_graph() -> None:
    handoff_args = {
        "query": "GomokuGameState",
        "path": "project://Source",
        "maxResults": 40,
    }
    payload = {
        "ok": True,
        "searchCompleted": True,
        "projectEvidenceAvailable": False,
        "projectMiss": True,
        "scope": "project_miss",
        "matchCount": 0,
        "message": "No active-project RAG match was found.",
        "requiredNextTool": "search_files",
        "requiredNextToolArgs": handoff_args,
        "nextAction": "search_files",
        "nextActionArgs": handoff_args,
        "nextActionIsTool": True,
        "control": {
            "version": 1,
            "phase": "unreal_rag_search",
            "status": "NeedsAction",
            "nextAction": "search_files",
            "nextActionIsTool": True,
        },
    }

    compact = compact_structured_payload(payload, max_bytes=8_000)

    assert compact["projectMiss"] is True
    assert compact["requiredNextTool"] == "search_files"
    assert compact["requiredNextToolArgs"] == handoff_args
    assert compact["message"].startswith("No active-project")
    assert "assetKind" not in compact


def test_code_sketch_compaction_preserves_every_known_bad_replacement() -> None:
    payload = {
        "ok": False,
        "verdictSummary": "0 verified, 0 weak, 12 known_bad, 100 unverified",
        "knownBadCount": 12,
        "unverifiedCount": 100,
        "weakCount": 0,
        "results": [
            {
                "symbol": f"bad{index}",
                "verdict": "known_bad",
                "replacement": f"UseReplacement{index}",
                "note": "n" * 500,
            }
            for index in range(12)
        ] + [
            {
                "symbol": f"unknown{index}",
                "verdict": "unverified",
                "note": "u" * 500,
            }
            for index in range(100)
        ],
    }
    compact = compact_code_sketch_payload(payload, max_bytes=2_000)
    bad = [row for row in compact["results"] if row.get("verdict") == "known_bad"]
    assert len(bad) == 12
    assert [row["replacement"] for row in bad] == [
        f"UseReplacement{index}" for index in range(12)
    ]


def test_code_sketch_compaction_preserves_deterministic_recovery() -> None:
    payload = {
        "ok": False,
        "gatePassed": False,
        "writeGateClosed": True,
        "verdictSummary": "0 verified, 0 weak, 0 known_bad, 1 unverified",
        "knownBadCount": 0,
        "unverifiedCount": 1,
        "weakCount": 0,
        "firstBlocker": {
            "symbol": "InitializeBoard",
            "verdict": "unverified",
            "note": "No exact owner/signature evidence.",
        },
        "nextAction": "unreal_symbol_lookup",
        "nextActionArgs": {
            "query": "InitializeBoard",
            "top_k": 8,
            "detailLevel": "compact",
        },
        "doNotRetryUnchanged": True,
        "reuseCurrentTaskAuthorization": True,
        "agentInstruction": "Do not rerun the unchanged sketch.",
        "results": [
            {
                "symbol": "InitializeBoard",
                "verdict": "unverified",
                "note": "No exact owner/signature evidence.",
            }
        ],
    }

    compact = compact_code_sketch_payload(payload, max_bytes=600)

    assert compact["firstBlocker"]["symbol"] == "InitializeBoard"
    assert compact["nextAction"] == "unreal_symbol_lookup"
    assert compact["nextActionArgs"]["query"] == "InitializeBoard"
    assert compact["doNotRetryUnchanged"] is True
    assert compact["reuseCurrentTaskAuthorization"] is True


def test_generic_compaction_routes_sketch_payload_to_safe_compactor() -> None:
    payload = {
        "ok": False,
        "verdictSummary": "known bad",
        "knownBadCount": 8,
        "results": [
            {
                "symbol": f"bad{index}",
                "verdict": "known_bad",
                "replacement": f"replacement{index}",
                "note": "x" * 1_000,
            }
            for index in range(8)
        ],
    }
    compact = compact_structured_payload(payload, max_bytes=2_000)
    assert len(compact["results"]) == 8
    assert all(row.get("replacement") for row in compact["results"])


def test_generic_compaction_never_discards_replacements_for_tiny_budget() -> None:
    payload = {
        "ok": False,
        "verdictSummary": "known bad",
        "knownBadCount": 3,
        "results": [
            {
                "symbol": f"bad{index}",
                "verdict": "known_bad",
                "replacement": f"complete replacement {index}",
            }
            for index in range(3)
        ],
    }

    compact = compact_structured_payload(payload, max_bytes=300)

    assert [row["replacement"] for row in compact["results"]] == [
        f"complete replacement {index}" for index in range(3)
    ]


def test_agent_plan_compaction_preserves_authorization_and_bounds_repeated_request() -> None:
    authorization = {
        "taskSessionId": "session",
        "authToken": "secret",
        "planId": "plan",
        "planRevision": "2",
        "activeSliceId": "slice",
        "routeHash": "route",
        "routePhase": "planner",
    }
    payload = {
        "request": "r" * 20_000,
        "taskKind": "edit",
        "editStrategy": "exact_patch",
        "evidencePlan": {"queries": ["q" * 20_000], "gates": ["gate"]},
        "suggestedToolCalls": [{"tool": "read_file", "args": {"query": "q" * 5_000}}],
        "writeGate": {"writesAllowed": True},
        "toolRoute": {"activeTools": ["read_file"], "pendingGates": ["gate"]},
        "taskAuthorization": authorization,
        "taskAuthorizationRequiredForWrites": True,
        "writeToolAuthorizationArgs": {"taskAuthorization": authorization},
        "authorizationRetryPolicy": {"reuseExistingAuthorization": True},
        "nextAction": "unreal_code_sketch_claim_validate",
        "nextActionIsTool": True,
        "requiredNextToolArgs": {"taskAuthorization": authorization},
        "contextCompactorRouting": {
            "policy": "advisory",
            "active": False,
            "blocksWrites": False,
            "directModelAllowed": True,
        },
        "checkpoints": ["c" * 2_000 for _ in range(30)],
        "stopConditions": ["s" * 2_000 for _ in range(30)],
        "retryPolicy": ["p" * 2_000 for _ in range(30)],
        "notes": ["n" * 2_000 for _ in range(30)],
    }
    compact = compact_agent_plan_payload(payload, max_bytes=8_000)
    assert compact["taskAuthorization"] == authorization
    assert compact["writeToolAuthorizationArgs"]["taskAuthorization"] == authorization
    assert compact["contextCompactorRouting"]["policy"] == "advisory"
    assert compact["contextCompactorRouting"]["directModelAllowed"] is True
    assert compact["requiredNextToolArgs"] == {"taskAuthorization": authorization}
    assert len(compact["request"]) < 1_300
    assert len(json.dumps(compact, ensure_ascii=False)) <= 8_000
    assert len(json.dumps(compact, ensure_ascii=False)) < len(json.dumps(payload, ensure_ascii=False))


def test_read_only_plan_compaction_drops_verbose_planning_but_keeps_task_control() -> None:
    authorization = {
        "taskSessionId": "read-session",
        "authToken": "read-token",
        "planId": "read-plan",
        "planRevision": "4",
        "activeSliceId": "inspect",
        "routeHash": "read-route",
        "routePhase": "planner",
    }
    payload = {
        "request": "inspect " + "source " * 20_000,
        "objective": "프로젝트 Example로 바꾸고 Player AnimInstance 분석해",
        "requestIntent": {
            "version": 1,
            "objectiveHash": "a" * 64,
            "domain": "source",
            "operation": "analyze",
            "mutability": "none",
            "speechAct": "command",
            "negated": False,
            "targets": {"symbols": ["UCPlayerCharacterAnimInstance"]},
            "ambiguity": {"status": "resolved", "material": False},
        },
        "resolvedTargets": [{"symbol": "UCPlayerCharacterAnimInstance", "exact": False}],
        "semanticAmbiguity": {"selectedInterpretation": None, "material": False},
        "taskKind": "cpp_analysis",
        "editStrategy": "no_edit",
        "evidencePlan": {
            "task_kind": "cpp_analysis",
            "queries": ["query " * 10_000],
            "rag_modes": ["review", "planning"],
            "gates": ["direct_source_evidence", "unreal_review_claim_validate"],
            "writes_allowed": False,
            "files_to_read": ["project://Example/Source/Foo.cpp"] * 40,
        },
        "projectContext": {
            "ok": True,
            "projectName": "Example",
            "projectDir": "/projects/Example",
            "uprojectPath": "/projects/Example/Example.uproject",
            "sourceBrowsePath": "project://Example/Source",
            "hugeDiscoveryCache": {"rows": ["x" * 1_000 for _ in range(80)]},
        },
        "writeGate": {"writesAllowed": False, "forbiddenWhen": ["no_edit"]},
        "suggestedToolCalls": [
            {"tool": "search_files", "args": {"query": "Foo", "path": "project://Example/Source"}},
            {"tool": "read_file", "args": {"path": "Source/Example/Foo.cpp"}},
        ],
        "sourceEvidence": {
            "required": True,
            "sourceReadSucceeded": False,
            "filesRead": [{"path": "Source/Example/Foo.cpp", "text": "x" * 20_000}],
            "claimPolicy": "fail_closed",
        },
        "toolRoute": {"activeTools": ["search_files", "read_file"], "routeHash": "read-route"},
        "taskAuthorization": authorization,
        "taskAuthorizationRequiredForWrites": True,
        "writeToolAuthorizationArgs": {"taskAuthorization": authorization},
        "authorizationRetryPolicy": {"reuseExistingAuthorization": True},
        "control": {"version": 2, "epoch": 4, "allowedTools": ["search_files"]},
        "nextAction": "search_files",
        "nextActionIsTool": True,
        "requiredNextTool": "search_files",
        "requiredNextToolArgs": {"taskAuthorization": authorization},
        "domainProfile": {"verbose": "x" * 50_000},
        "planSlices": [{"verbose": "x" * 50_000}],
        "featureIntent": {"verbose": "x" * 50_000},
        "toolDiscoveryCandidates": [{"verbose": "x" * 50_000}],
    }

    compact = compact_agent_plan_payload(payload, max_bytes=8_000)

    assert compact["taskAuthorization"] == authorization
    assert compact["writeToolAuthorizationArgs"] == {"taskAuthorization": authorization}
    assert compact["toolRoute"] == payload["toolRoute"]
    assert compact["control"] == payload["control"]
    assert compact["requiredNextToolArgs"] == {"taskAuthorization": authorization}
    assert compact["objective"] == payload["objective"]
    assert compact["requestIntent"] == payload["requestIntent"]
    assert compact["resolvedTargets"] == payload["resolvedTargets"]
    assert compact["semanticAmbiguity"] == payload["semanticAmbiguity"]
    assert compact["projectContext"]["uprojectPath"] == "/projects/Example/Example.uproject"
    assert "hugeDiscoveryCache" not in compact["projectContext"]
    assert "domainProfile" not in compact
    assert "planSlices" not in compact
    assert "featureIntent" not in compact
    assert "toolDiscoveryCandidates" not in compact
    assert len(json.dumps(compact, ensure_ascii=False)) <= 8_000


def test_write_plan_compaction_preserves_request_intent_and_resume_contract() -> None:
    request_intent = {
        "version": 1,
        "objectiveHash": "b" * 64,
        "domain": "source",
        "operation": "modify",
        "mutability": "source_files",
        "speechAct": "command",
        "negated": False,
        "targets": {"symbols": ["UCPlayerCharacterAnimInstance"]},
        "ambiguity": {"status": "resolved", "material": False},
    }
    payload = {
        "request": "modify " + "source " * 20_000,
        "objective": "프로젝트 Example로 바꾸고 Player AnimInstance를 수정해",
        "objectiveHash": "b" * 64,
        "requestIntent": request_intent,
        "resolvedTargets": [{"symbol": "UCPlayerCharacterAnimInstance", "exact": True}],
        "semanticAmbiguity": {"selectedInterpretation": None, "material": False},
        "pendingRequest": "Player AnimInstance를 수정해",
        "pendingRequestHash": "c" * 64,
        "resumeAfter": "unreal_set_active_project",
        "taskKind": "edit",
        "writeGate": {"writesAllowed": True},
        "toolRoute": {"activeTools": ["read_file", "apply_patch"]},
        "taskAuthorization": {"taskSessionId": "session", "authToken": "token"},
        "checkpoints": ["checkpoint " * 1_000 for _ in range(30)],
        "notes": ["note " * 1_000 for _ in range(30)],
        "toolDiscoveryCandidates": [{"verbose": "x" * 50_000}],
    }

    compact = compact_agent_plan_payload(payload, max_bytes=8_000)

    for key in (
        "objective",
        "objectiveHash",
        "requestIntent",
        "resolvedTargets",
        "semanticAmbiguity",
        "pendingRequest",
        "pendingRequestHash",
        "resumeAfter",
        "toolRoute",
        "taskAuthorization",
    ):
        assert compact[key] == payload[key]
    assert compact["_structuredTruncated"] is True
    assert len(json.dumps(compact, ensure_ascii=False)) <= 8_000


def test_generic_compaction_preserves_top_level_project_resume_contract() -> None:
    request_intent = {
        "version": 1,
        "objectiveHash": "d" * 64,
        "domain": "project_control",
        "operation": "select",
        "mutability": "control_state",
        "speechAct": "command",
        "negated": False,
        "targets": {"projectName": "Example"},
        "ambiguity": {"status": "resolved", "material": False},
    }
    payload = {
        "ok": True,
        "objective": "프로젝트 Example로 바꾸고 분석해",
        "objectiveHash": "d" * 64,
        "requestIntent": request_intent,
        "resolvedTargets": [{"projectPath": "/projects/Example/Example.uproject"}],
        "semanticAmbiguity": {"selectedInterpretation": None, "material": False},
        "pendingRequest": "분석해",
        "pendingRequestHash": "e" * 64,
        "resumeAfter": "unreal_set_active_project",
        "rows": [{"text": "x" * 5_000} for _ in range(100)],
    }

    compact = compact_structured_payload(payload, max_bytes=8_000)

    for key in (
        "objective",
        "objectiveHash",
        "requestIntent",
        "resolvedTargets",
        "semanticAmbiguity",
        "pendingRequest",
        "pendingRequestHash",
        "resumeAfter",
    ):
        assert compact[key] == payload[key]
    assert compact["_structuredTruncated"] is True
    assert len(json.dumps(compact, ensure_ascii=False)) <= 8_000


def test_generic_task_status_compaction_preserves_nested_current_intent() -> None:
    request_intent = {
        "version": 1,
        "objectiveHash": "f" * 64,
        "domain": "source",
        "operation": "modify",
        "mutability": "source_files",
        "speechAct": "command",
        "negated": False,
        "targets": {"symbols": ["UDemoComponent"]},
        "ambiguity": {"status": "resolved", "material": False},
    }
    payload = {
        "ok": True,
        "status": "running",
        "state": {
            "taskSessionId": "task-session",
            "objective": "UDemoComponent를 수정해",
            "objectiveHash": "f" * 64,
            "requestIntent": request_intent,
            "history": [{"text": "x" * 5_000} for _ in range(100)],
        },
        "events": [{"payload": "y" * 5_000} for _ in range(100)],
    }

    compact = compact_structured_payload(payload, max_bytes=8_000)

    assert compact["state"] == {
        "taskSessionId": "task-session",
        "objective": "UDemoComponent를 수정해",
        "objectiveHash": "f" * 64,
        "requestIntent": request_intent,
    }
    assert compact["_structuredTruncated"] is True
    assert len(json.dumps(compact, ensure_ascii=False)) <= 8_000


def test_generic_task_checkpoint_compaction_preserves_continuity_intent() -> None:
    request_intent = {
        "version": 1,
        "objectiveHash": "1" * 64,
        "domain": "source",
        "operation": "analyze",
        "mutability": "none",
        "speechAct": "command",
        "negated": False,
        "targets": {"symbols": ["UDemoComponent"]},
        "ambiguity": {"status": "resolved", "material": False},
    }
    payload = {
        "ok": True,
        "taskSessionId": "task-checkpoint-session",
        "continuity": {
            "checkpoint": {
                "taskSessionId": "task-checkpoint-session",
                "objective": "UDemoComponent를 분석해",
                "objectiveHash": "1" * 64,
                "requestIntent": request_intent,
                "notes": ["x" * 5_000 for _ in range(100)],
            },
            "history": [{"text": "y" * 5_000} for _ in range(100)],
        },
        "events": [{"payload": "z" * 5_000} for _ in range(100)],
    }

    compact = compact_structured_payload(payload, max_bytes=8_000)

    assert compact["continuity"] == {
        "checkpoint": {
            "taskSessionId": "task-checkpoint-session",
            "objective": "UDemoComponent를 분석해",
            "objectiveHash": "1" * 64,
            "requestIntent": request_intent,
        }
    }
    assert compact["taskSessionId"] == "task-checkpoint-session"
    assert compact["_structuredTruncated"] is True
    assert len(json.dumps(compact, ensure_ascii=False)) <= 8_000


def test_specialized_metadata_compaction_keeps_nested_task_intent() -> None:
    request_intent = {
        "version": 1,
        "objectiveHash": "2" * 64,
        "domain": "asset",
        "operation": "analyze",
        "mutability": "none",
        "speechAct": "command",
        "negated": False,
        "targets": {},
        "ambiguity": {"status": "resolved", "material": False},
    }
    payload = {
        "ok": True,
        "exportDir": {"path": "/project/Saved/LmStudioMetadataExports"},
        "needsEditorExport": False,
        "state": {
            "taskSessionId": "metadata-task-session",
            "objective": "메타데이터 상태를 분석해",
            "objectiveHash": "2" * 64,
            "requestIntent": request_intent,
            "noise": "x" * 20_000,
        },
        "noise": "y" * 20_000,
    }

    compact = compact_structured_payload(payload, max_bytes=8_000)

    assert compact["state"] == {
        "taskSessionId": "metadata-task-session",
        "objective": "메타데이터 상태를 분석해",
        "objectiveHash": "2" * 64,
        "requestIntent": request_intent,
    }
    assert "noise" not in compact
    assert len(json.dumps(compact, ensure_ascii=False)) <= 8_000


def test_code_sketch_direct_compaction_keeps_top_level_control_surfaces() -> None:
    request_intent = {
        "version": 1,
        "objectiveHash": "3" * 64,
        "domain": "source",
        "operation": "modify",
        "mutability": "source_files",
        "speechAct": "command",
        "negated": False,
        "targets": {"symbols": ["UDemoComponent"]},
        "ambiguity": {"status": "resolved", "material": False},
    }
    payload = {
        "ok": False,
        "verdictSummary": "0 verified, 1 known_bad",
        "knownBadCount": 1,
        "unverifiedCount": 0,
        "objective": "UDemoComponent를 수정해",
        "objectiveHash": "3" * 64,
        "requestIntent": request_intent,
        "taskAuthorization": {
            "taskSessionId": "sketch-task-session",
            "authToken": "sketch-auth-token",
        },
        "results": [
            {
                "symbol": "BadCall",
                "verdict": "known_bad",
                "replacement": "UseGoodCall",
                "note": "x" * 20_000,
            }
        ],
    }

    compact = compact_structured_payload(payload, max_bytes=2_000)

    assert compact["objective"] == payload["objective"]
    assert compact["objectiveHash"] == payload["objectiveHash"]
    assert compact["requestIntent"] == request_intent
    assert compact["taskAuthorization"] == payload["taskAuthorization"]
    assert compact["results"][0]["replacement"] == "UseGoodCall"
    assert len(json.dumps(compact, ensure_ascii=False)) <= 2_000


def test_code_sketch_reserves_control_projection_before_selecting_detail_tier() -> None:
    request_intent = {
        "version": 1,
        "objectiveHash": "4" * 64,
        "domain": "source",
        "operation": "modify",
        "mutability": "source_files",
        "speechAct": "command",
        "negated": False,
        "targets": {"symbols": ["UDemoComponent"]},
        "ambiguity": {"status": "resolved", "material": False},
    }
    payload = {
        "ok": False,
        "verdictSummary": "0 verified, 1 known_bad",
        "knownBadCount": 1,
        "objective": "UDemoComponent를 수정해",
        "objectiveHash": "4" * 64,
        "requestIntent": request_intent,
        "taskAuthorization": {
            "taskSessionId": "s" * 100,
            "authToken": "t" * 200,
        },
        "results": [
            {
                "symbol": "BadCall",
                "verdict": "known_bad",
                "replacement": "UseGoodCall",
                "note": "x" * 1_000,
            }
        ],
    }

    compact = compact_structured_payload(payload, max_bytes=2_000)

    assert compact["requestIntent"] == request_intent
    assert compact["taskAuthorization"] == payload["taskAuthorization"]
    assert compact["results"] == [
        {
            "symbol": "BadCall",
            "verdict": "known_bad",
            "replacement": "UseGoodCall",
        }
    ]
    assert len(json.dumps(compact, ensure_ascii=False)) <= 2_000


def test_generic_plan_compaction_never_shrinks_authorization_for_tiny_budget() -> None:
    authorization = {
        "taskSessionId": "session",
        "authToken": "secret-token",
        "planId": "plan",
        "planRevision": "2",
        "activeSliceId": "slice",
        "routeHash": "route",
        "routePhase": "planner",
    }
    payload = {
        "request": "r" * 10_000,
        "taskKind": "edit",
        "writeGate": {"writesAllowed": True},
        "toolRoute": {"activeTools": ["read_file"]},
        "taskAuthorization": authorization,
        "taskAuthorizationRequiredForWrites": True,
        "writeToolAuthorizationArgs": {"taskAuthorization": authorization},
        "authorizationRetryPolicy": {"reuseExistingAuthorization": True},
    }

    compact = compact_structured_payload(payload, max_bytes=200)

    assert compact["taskAuthorization"] == authorization
    assert compact["writeToolAuthorizationArgs"]["taskAuthorization"] == authorization
