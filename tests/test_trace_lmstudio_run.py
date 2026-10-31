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


def test_thinking_is_not_delivered_but_context_exclusion_does_not_hide_prose():
    doc = _conversation([])
    steps = doc["messages"][1]["versions"][0]["steps"]
    steps.extend([
        {"type": "contentBlock", "style": {"type": "thinking"}, "content": [{"type": "text", "text": "This method is never called."}]},
        {"type": "contentBlock", "shouldIncludeInContext": False, "content": [{"type": "text", "text": "Verified always wrong."}]},
        {"type": "contentBlock", "content": [{"type": "text", "text": "No conclusion yet."}]},
    ])
    claims = trace.parse_conversation(doc)["turns"][0]["claims"]
    assert all("never called" not in claim["sentence"] for claim in claims)
    assert any("Verified always wrong" in claim["sentence"] for claim in claims)


def test_host_notice_origin_is_separate_from_the_model_claim_it_corrects():
    doc = _conversation([{"type": "text", "text": "검증 완료."}])
    steps = doc["messages"][1]["versions"][0]["steps"]
    steps.extend([
        {"type": "contentBlock", "content": [{"type": "text", "text": "[검증 범위] '검증 완료'는 사실 확인이 아닙니다."}]},
        {"type": "debugInfoBlock", "debugInfo": json.dumps({"event": "verification_scope_notice"})},
    ])
    turn = trace.parse_conversation(doc)["turns"][0]
    assert len(turn["claims"]) == 1 and turn["claims"][0]["sentence"] == "검증 완료."
    assert turn["hostNotices"][0]["event"] == "verification_scope_notice"


def test_metadata_budget_keeps_retry_minimum_and_suggestion_in_trace():
    result = {"ok": False, "errorCode": "OUTPUT_LIMIT_EXCEEDED",
              "retry": {"allowed": True, "mode": "different_arguments", "minimum": {"field": "byteBudget", "value": 2173}},
              "suggestion": {"tool": "read_file_range", "args": {"path": "project://A.h", "byteBudget": 2173}}}
    turn = trace.parse_conversation(_conversation(_call("r", "read_file", {"path": "project://A.h"}, result)))["turns"][0]
    assert turn["calls"][0]["failureKind"] == "response_metadata_budget"
    assert "2173" in str(turn["calls"][0]["result"]["retry"])


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


def _assistant_steps(steps: list[dict]) -> dict:
    return {"versions": [{"type": "multiStep", "role": "assistant", "steps": steps}], "currentlySelected": 0}


def _event(payload: dict) -> dict:
    return {"type": "debugInfoBlock", "debugInfo": json.dumps(payload)}


def test_denied_calls_restore_state_and_rereads_are_recorded():
    """The 2026-10-18 shape: restored window, a budget denial that never returns a
    result, and the same partial read repeated after a projection."""
    read = {"ok": True, "hasMore": True, "truncated": True, "nextOffsetBytes": 5988, "omissionReason": "byte_budget"}
    args = {"path": "project://Source/G/GameMode.cpp", "byteBudget": 8192}
    denial = {"event": "direct_round_observation", "roundIndex": 3, "toolTrace": {"runtime": [
        {"toolName": "read_file", "validationState": "allowed"},
        {"toolName": "read_file", "validationState": "denied_batch_budget", "budgetDenialReason": "held_reservations_insufficient",
         "budgetAtDenial": {"capacity": 14252, "used": 12825, "available": 1427, "held": 12825, "entries": []}}]}}
    steps = [_event({"event": "working_context_restore", "status": "restored_remeasure_required"}),
             _event({"event": "resume_state", "priorTurnInterrupted": True, "partialProjections": 8}),
             {"type": "contentBlock", "content": _call("1", "read_file", args, read)},
             _event({"event": "working_context_projection", "changed": True, "reason": "measured_input_pressure"}),
             {"type": "contentBlock", "content": _call("2", "read_file", args, read)},
             _event(denial),
             {"type": "contentBlock", "content": _call("3", "read_file", args, read)},
             _event({"event": "execution_outcome", "executionOutcome": "canceled"})]
    user = {"versions": [{"role": "user", "content": [{"type": "text", "text": "계속해"}]}], "currentlySelected": 0}
    data = trace.parse_conversation({"messages": [user, _assistant_steps(steps)]})
    turn = data["turns"][0]
    assert [c.get("reread", {}).get("kind") for c in turn["calls"]] == [None, "reacquire_after_projection", "identical_reread"]
    assert turn["deniedCalls"] == [{"toolName": "read_file", "validationState": "denied_batch_budget", "afterCall": 1,
                                    "roundIndex": 3, "budgetDenialReason": "held_reservations_insufficient",
                                    "budgetAtDenial": {"capacity": 14252, "used": 12825, "available": 1427, "held": 12825}}]
    assert {e["event"]: e for e in turn["events"]}["resume_state"]["priorTurnInterrupted"] is True
    summary = trace.summarize(data)
    assert summary["rereads"] == {"reacquire_after_projection": 1, "identical_reread": 1}
    assert summary["deniedCalls"] == {"held_reservations_insufficient": 1}
    markdown = trace.render_markdown("x", data)
    assert "working_context_restore=restored_remeasure_required" in markdown and "execution_outcome=canceled" in markdown
    assert "denied_batch_budget held_reservations_insufficient" in markdown


def test_partial_search_records_omitted_matches_and_cursor():
    page = {"ok": True, "coverage": "partial", "truncated": True, "omissionReason": "response_budget",
            "matchesReturned": 16, "matchesOmitted": 1, "absenceVerified": False, "nextCursor": "abc"}
    blocks = _call("1", "search_files", {"path": "project://Source", "query": "bIsDead"}, page)
    call = trace.parse_conversation(_conversation(blocks))["turns"][0]["calls"][0]
    assert call["failureKind"] == "partial_result"
    assert call["result"]["matchesOmitted"] == 1 and call["result"]["absenceVerified"] is False
    assert call["result"]["nextCursor"] == "abc"


def test_unity_trace_classification_and_fields():
    for code in ("not_found", "editor_reloading", "editor_not_running", "edit_disabled"):
        assert trace.failure_kind({"errorCode": code}) == code
    assert trace.failure_kind({"reasonCode": "editor_reloading"}) == "editor_reloading"
    assert trace.failure_kind({"errorCode": "path_denied"}) == "outside_allowed_roots"
    assert {"action", "operationId", "compilationId", "indexVersion"} <= set(trace.ARG_FIELDS)
    assert {"connection", "reasonCode", "compilationOutcome", "testStatus", "passed", "failed"} <= set(trace.RESULT_FLAGS)


def test_read_shape_counts_lines_whole_reads_and_calls_per_edit():
    blocks = _call("1", "read_file", {"path": "engine://Engine/Source/A.cpp"},
                   {"ok": True, "content": "a\nb\nc", "sha256": "x"}) \
        + _call("2", "read_file_range", {"path": "project://Source/B.cpp", "startLine": 10, "endLine": 19},
                {"ok": True, "startLine": 10, "endLine": 19}) \
        + _call("3", "read_file_range", {"path": "project://Source/B.cpp", "startLine": 10, "endLine": 19},
                {"ok": True, "startLine": 10, "endLine": 19}) \
        + _call("4", "replace_in_file", {"path": "project://Source/B.cpp"}, {"ok": True, "operation": "replaced"})
    shape = trace.summarize(trace.parse_conversation(_conversation(blocks)))["readShape"]
    assert shape["sourceReads"] == 3 and shape["wholeFileReads"] == 1
    assert shape["linesRead"] == 23 and shape["engineLinesRead"] == 3 and shape["maxLinesPerRead"] == 10
    assert shape["repeatedReadRate"] == 0.333 and shape["readsBeforeFirstEdit"] == [3]
    assert shape["successfulEdits"] == 1 and shape["callsPerSuccessfulEdit"] == 4.0


def test_read_shape_does_not_count_byte_windows_or_unreturned_edits_as_success():
    blocks = _call("1", "read_file", {"path": "project://A.cpp", "offsetBytes": 0, "maxBytes": 100},
                   {"ok": True, "content": "a\nb"})
    pending = _call("2", "replace_in_file", {"path": "project://A.cpp"}, {"ok": True})[0]
    shape = trace.summarize(trace.parse_conversation(_conversation(blocks + [pending])))["readShape"]
    assert shape["wholeFileReads"] == 0
    assert shape["successfulEdits"] == 0


def test_different_symbol_views_are_not_identical_rereads():
    blocks = _call("1", "read_symbol", {"path": "project://A.cpp", "symbol": "F::Run", "view": "declaration"},
                   {"ok": True, "content": "void Run();"})
    blocks += _call("2", "read_symbol", {"path": "project://A.cpp", "symbol": "F::Run", "view": "body"},
                    {"ok": True, "content": "void F::Run() {}"})
    assert trace.summarize(trace.parse_conversation(_conversation(blocks)))["readShape"]["repeatedReadRate"] == 0


def test_repeat_receipt_does_not_hide_a_repeated_read_in_metrics():
    args = {"path": "project://A.cpp", "startLine": 10, "endLine": 12, "byteBudget": 4096}
    blocks = _call("1", "read_file_range", args, {"ok": True, "content": "a\nb\nc"})
    blocks += _call("2", "read_file_range", {**args, "repeatReceipt": "provider-receipt"},
                    {"ok": True, "duplicate": True})
    shape = trace.summarize(trace.parse_conversation(_conversation(blocks)))["readShape"]
    assert shape["repeatedReadRate"] == 0.5
    assert shape["linesRead"] == 3
