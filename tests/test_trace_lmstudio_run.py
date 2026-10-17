"""Trace analyzer: classifies recorded tool chains without inferring unrecorded input."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import trace_lmstudio_run as trace  # noqa: E402


def _result(payload: dict) -> str:
    return json.dumps([{"type": "text", "text": json.dumps(payload)}])


def _assistant(blocks: list[dict], events: list[dict] | None = None) -> dict:
    steps = [{"type": "debugInfoBlock", "debugInfo": json.dumps(e)} for e in (events or [])]
    steps.append({"type": "contentBlock", "content": blocks})
    return {"versions": [{"type": "multiStep", "role": "assistant", "steps": steps}], "currentlySelected": 0}


def _call(rid: str, name: str, params: dict, result: dict) -> list[dict]:
    return [{"type": "toolCallRequest", "toolCallRequestId": rid, "name": name, "parameters": params,
             "pluginIdentifier": "mcp/unreal-agent"},
            {"type": "toolCallResult", "toolCallRequestId": rid, "content": _result(result)}]


def _conversation(blocks: list[dict]) -> dict:
    user = {"versions": [{"role": "user", "content": [{"type": "text", "text": "SECRET USER TEXT"}]}],
            "currentlySelected": 0}
    identity = {"event": "direct_runtime_identity", "modelIdentifier": "test-model", "loadedBuildId": "b1"}
    return {"messages": [user, _assistant(blocks, [identity])]}


def test_not_found_then_identical_retry_and_absence_claim_are_linked():
    missing = {"ok": False, "errorCode": "NOT_FOUND", "message": "File not found: engine://Engine/x/A.cpp"}
    blocks = _call("1", "read_file", {"path": "engine://Engine/x/A.cpp"}, missing) \
        + _call("2", "read_file", {"path": "engine://Engine/x/A.cpp"}, missing) \
        + [{"type": "text", "text": "엔진 Private 소스가 설치되지 않았습니다. 따라서 항상 실패합니다."}]
    result = trace.parse_conversation(_conversation(blocks))
    calls = result["turns"][0]["calls"]
    assert calls[0]["failureKind"] == "not_found"
    assert calls[0]["followUp"]["relation"] == "repeated_identical"
    kinds = [c["kinds"] for c in result["turns"][0]["claims"]]
    assert ["absence_claim"] in kinds and any("overclaim_wording" in k for k in kinds)
    assert result["models"] == ["test-model"] and result["projectedInput"] == "not_recorded"
    assert "SECRET USER TEXT" not in json.dumps(result, ensure_ascii=False)


def test_byte_budget_partial_then_continuation_is_recognised():
    partial = {"ok": True, "hasMore": True, "truncated": True, "nextOffsetBytes": 439, "coverage": "partial"}
    full = {"ok": True, "hasMore": False, "truncated": False, "coverage": "complete"}
    blocks = _call("1", "read_file", {"path": "project://A.Build.cs", "byteBudget": 2048}, partial) \
        + _call("2", "read_file", {"path": "project://A.Build.cs", "byteBudget": 2048, "offsetBytes": 439}, full)
    calls = trace.parse_conversation(_conversation(blocks))["turns"][0]["calls"]
    assert calls[0]["failureKind"] == "partial_result"
    assert calls[0]["followUp"] == {"nextCall": 1, "relation": "changed_budget_or_offset", "followUpGainedEvidence": True}


def test_structure_only_validation_flags_verified_wording_but_contract_does_not():
    scope = {"ok": True, "validationScope": "packet_structure_and_record_consistency", "factualEvidenceVerified": False}
    blocks = _call("1", "evidence_first_contract", {"mode": "audit"}, scope) \
        + _call("2", "evidence_first_validate", {"mode": "audit"}, scope) \
        + [{"type": "text", "text": "## P1 — 기능 결함 (검증 완료)"}]
    turn = trace.parse_conversation(_conversation(blocks))["turns"][0]
    assert [c["failureKind"] for c in turn["calls"]] == [None, "structure_only_validation"]
    assert turn["claims"][0]["note"] == "only structure validation was recorded in this turn"


def test_complete_line_range_with_more_file_lines_is_not_partial():
    more = {"ok": True, "truncated": True, "nextStartLine": 81}
    blocks = _call("1", "read_file_range", {"path": "engine://E/A.h", "startLine": 1, "endLine": 80}, more)
    call = trace.parse_conversation(_conversation(blocks))["turns"][0]["calls"][0]
    assert call["failureKind"] is None and call["result"]["requestedRangeComplete"] == "inferred_from_args"


def test_cli_writes_json_and_markdown(tmp_path):
    source = tmp_path / "123.conversation.json"
    source.write_text(json.dumps(_conversation(_call("1", "search_files", {"path": "C:/Engine"},
                                                     {"ok": False, "errorCode": "INVALID_ARGUMENT",
                                                      "message": "read path is outside WORKSPACE_ROOT and activeProject: C:/Engine"}))),
                      encoding="utf-8")
    assert trace.main([str(source), "--out", str(tmp_path / "out")]) == 0
    data = json.loads((tmp_path / "out" / "trace-123.json").read_text(encoding="utf-8"))
    assert data["turns"][0]["calls"][0]["failureKind"] == "outside_allowed_roots"
    assert "outside_allowed_roots" in (tmp_path / "out" / "trace-123.md").read_text(encoding="utf-8")
