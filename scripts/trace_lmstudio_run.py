#!/usr/bin/env python
"""Reconstruct tool-call chains from an LM Studio conversation file.

Reads ``~/.lmstudio/conversations/<id>.conversation.json`` (read-only) and
links each request -> raw tool return -> harness projection events -> next
call -> final claims. It reports only what the file records: the projected
per-round model input is not stored there, so it is marked
``not_recorded`` instead of being inferred. User message text is kept only as
a SHA-256 digest; tool arguments are reduced to routing fields.

Usage: python scripts/trace_lmstudio_run.py <conversation.json> [--out DIR]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

ARG_FIELDS = ("action", "operationId", "compilationId", "indexVersion", "mode", "match", "symbolId", "path", "query", "sourceMode", "owner", "byteBudget", "maxBytes", "offsetBytes", "startLine",
              "endLine", "chunkId", "offset", "maxChars", "detailLevel", "matchFileNames", "namesOnly", "mode",
              "project", "readHandle", "action", "symbol")
RESULT_FLAGS = ("connection", "reasonCode", "compilationOutcome", "testStatus", "passed", "failed", "indexSource", "ok", "status", "errorCode", "hasMore", "truncated", "nextOffsetBytes", "nextStartLine",
                "matchCount", "bodyComplete", "notice", "suggestion", "validationScope", "factualEvidenceVerified",
                "summary", "omissionReason", "effectiveByteBudget", "duplicate", "absenceVerified",
                "apiAbsenceVerified", "evidenceEnvelopeTruncated", "matchesReturned", "matchesOmitted",
                "moreMatchesPossible", "nextCursor", "cursorOffset")
SOURCE_READS = {"read_file", "read_file_range", "read_symbol", "git_read_file"}
KEPT_EVENTS = {"working_context_projection", "model_input_evidence_summary", "execution_outcome", "batch_budget",
               "working_context_restore", "resume_state", "partial_search_scope_notice", "verification_scope_notice"}
KEPT_EVENT_FIELDS = ("event", "roundIndex", "reason", "changed", "disposition", "reportState", "executionOutcome",
                     "results", "modelInputId", "toolResults", "projections", "partialProjections", "status",
                     "priorTurnInterrupted", "restoredWindow")
READ_FAMILY = {"read_file", "read_file_range", "read_symbol", "list_directory", "search_files",
               "unreal_symbol_lookup", "unreal_rag_search", "evidence_first_read_context", "git_read_file"}
OVERCLAIM = re.compile(r"항상|한 번도|무조건|절대로?|검증 완료|확정|\balways\b|\bnever\b|\bverified\b", re.IGNORECASE)
ABSENCE = re.compile(r"설치되지|미설치|존재하지 않|없습니다|없다\b|not installed|does not exist|doesn't exist|is missing",
                     re.IGNORECASE)


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _short(value: Any, limit: int = 200) -> Any:
    if isinstance(value, str) and len(value) > limit:
        return value[:limit] + "…"
    return value


def _decode_result(content: Any) -> Any:
    """toolCallResult.content is a JSON list of {type:text,text:<json>} blocks."""
    payload: Any = content
    for _ in range(3):
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except (ValueError, TypeError):
                return payload
        if isinstance(payload, list) and payload and isinstance(payload[0], dict) and "text" in payload[0]:
            payload = "".join(str(block.get("text", "")) for block in payload if isinstance(block, dict))
            continue
        break
    return payload


def _find(obj: Any, key: str, depth: int = 0) -> Any:
    if depth > 4:
        return None
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for value in obj.values():
            found = _find(value, key, depth + 1)
            if found is not None:
                return found
    return None


def result_flags(decoded: Any) -> dict[str, Any]:
    if not isinstance(decoded, dict):
        text = str(decoded or "")
        flags: dict[str, Any] = {"ok": False if re.search(r"error|denied|reject", text, re.I) else None,
                                 "message": _short(text)}
        return {k: v for k, v in flags.items() if v is not None}
    flags = {}
    for key in RESULT_FLAGS:
        value = _find(decoded, key)
        if value is not None:
            flags[key] = _short(value) if not isinstance(value, (dict, list)) else _short(json.dumps(value, ensure_ascii=False))
    coverage = _find(decoded, "coverage")
    if isinstance(coverage, dict):
        flags["coverage"] = {k: coverage[k] for k in ("completeness", "scope", "apiAbsenceVerified") if k in coverage}
    elif isinstance(coverage, str):
        flags["coverage"] = coverage
    message = _find(decoded, "message") or _find(decoded, "error")
    if isinstance(message, str):
        flags["message"] = _short(message)
    return flags


def failure_kind(flags: dict[str, Any], tool: str = "") -> str | None:
    code = str(flags.get("errorCode") or flags.get("reasonCode") or "")
    message = str(flags.get("message") or "")
    if flags.get("duplicate") is True:
        return "duplicate_receipt"
    if code == "path_denied":
        return "outside_allowed_roots"
    if code in {"editor_not_running", "editor_reloading", "bridge_not_installed", "edit_disabled", "execute_disabled"}:
        return code
    if code == "PATH_OUTSIDE_ALLOWED_ROOTS" or "outside WORKSPACE_ROOT" in message:
        return "outside_allowed_roots"
    if code in {"NOT_FOUND", "FILE_NOT_FOUND", "not_found"} or message.startswith("File not found"):
        return "not_found"
    if flags.get("matchCount") == 0 and ("coverage_limited" in str(flags.get("status"))
                                         or "partial" in json.dumps(flags.get("coverage"), ensure_ascii=False)):
        return "coverage_limited_zero"
    # The contract tool describes the validation policy; only a validate call is a validation result.
    if tool == "evidence_first_validate" and (
            flags.get("validationScope") == "packet_structure_and_record_consistency"
            or flags.get("factualEvidenceVerified") is False):
        return "structure_only_validation"
    if flags.get("hasMore") is True or flags.get("truncated") is True or flags.get("bodyComplete") is False:
        return "partial_result"
    if flags.get("ok") is False or code:
        return "error_other"
    return None


def _mark_complete_line_range(call: dict[str, Any]) -> None:
    """Older read_file_range results set truncated when the file merely continues
    past the requested range. A next line right after endLine means the
    requested range itself was returned whole."""
    result, args = call.get("result") or {}, call.get("args") or {}
    if call.get("name") != "read_file_range" or call.get("failureKind") != "partial_result":
        return
    end, next_line = args.get("endLine"), result.get("nextStartLine")
    if isinstance(end, int) and next_line == end + 1:
        result["requestedRangeComplete"] = "inferred_from_args"
        call["failureKind"] = None


def _denied_calls(event: dict[str, Any], after_call: int) -> list[dict[str, Any]]:
    """Calls the harness refused before execution, with the recorded reason.
    They never produce a toolCallResult, so contentBlocks alone cannot show them."""
    trace = event.get("toolTrace")
    runtime = (trace.get("runtime") or []) if isinstance(trace, dict) else []
    denied = []
    for entry in runtime:
        state = str(entry.get("validationState") or "")
        if not state.startswith("denied"):
            continue
        item = {"toolName": entry.get("toolName"), "validationState": state, "afterCall": after_call,
                "roundIndex": event.get("roundIndex")}
        for key in ("budgetDenialReason", "budgetAllowance", "readResultLimit"):
            if entry.get(key) is not None:
                item[key] = entry[key]
        snapshot = entry.get("budgetAtDenial")
        if isinstance(snapshot, dict):
            item["budgetAtDenial"] = {k: snapshot[k] for k in ("capacity", "used", "available", "held") if k in snapshot}
        denied.append(item)
    return denied


def _mark_rereads(calls: list[dict[str, Any]], events: list[dict[str, Any]]) -> None:
    """A source read repeated with identical arguments in the same turn. It is a
    reacquisition when a projection ran between the two calls (the earlier body
    may have been compacted), otherwise an identical reread."""
    projections = [e.get("afterCall", -1) for e in events
                   if e.get("event") == "working_context_projection" and e.get("changed") is not False]
    seen: dict[str, int] = {}
    for call in calls:
        if call["name"] not in SOURCE_READS or call.get("failureKind") in {"not_found", "outside_allowed_roots",
                                                                          "error_other"}:
            continue
        key = json.dumps([call["name"], call["args"]], sort_keys=True, ensure_ascii=False)
        if key in seen:
            earlier = seen[key]
            projected = any(earlier <= after < call["index"] for after in projections)
            call["reread"] = {"of": earlier, "kind": "reacquire_after_projection" if projected else "identical_reread"}
        seen[key] = call["index"]


def _routing_args(params: Any) -> dict[str, Any]:
    if not isinstance(params, dict):
        return {}
    return {k: _short(params[k], 160) for k in ARG_FIELDS if k in params}


def _relation(call: dict[str, Any], follow: dict[str, Any] | None) -> dict[str, Any]:
    if follow is None:
        return {"nextCall": None, "relation": "no_follow_up"}
    rel = "switched_tool"
    if follow["name"] == call["name"]:
        a, b = call["args"], follow["args"]
        if a == b:
            rel = "repeated_identical"
        elif a.get("path") != b.get("path") or a.get("query") != b.get("query"):
            rel = "changed_target"
        elif any(a.get(k) != b.get(k) for k in ("byteBudget", "maxBytes", "offsetBytes", "startLine", "offset", "maxChars")):
            rel = "changed_budget_or_offset"
        else:
            rel = "changed_other_args"
    return {"nextCall": follow["index"], "relation": rel,
            "followUpGainedEvidence": follow["failureKind"] is None}


def parse_conversation(data: dict[str, Any]) -> dict[str, Any]:
    turns: list[dict[str, Any]] = []
    models: set[str] = set()
    builds: set[str] = set()
    call_index = 0
    for message in data.get("messages", []):
        versions = message.get("versions") or []
        version = versions[message.get("currentlySelected", 0)] if versions else {}
        if version.get("role") == "user":
            text = "".join(c.get("text", "") for c in version.get("content", []) if isinstance(c, dict))
            turns.append({"userMessageSha256": sha256(text), "calls": [], "events": [], "deniedCalls": [],
                          "finalText": ""})
            continue
        if not turns:
            turns.append({"userMessageSha256": None, "calls": [], "events": [], "deniedCalls": [], "finalText": ""})
        turn = turns[-1]
        pending: dict[str, dict[str, Any]] = {}
        text_after_last_call: list[str] = []
        for step in version.get("steps", []):
            if step.get("type") == "debugInfoBlock":
                try:
                    event = json.loads(step.get("debugInfo") or "{}")
                except ValueError:
                    continue
                name = event.get("event")
                if name == "direct_runtime_identity":
                    if event.get("modelIdentifier"):
                        models.add(str(event["modelIdentifier"]))
                    if event.get("loadedBuildId"):
                        builds.add(str(event["loadedBuildId"]))
                if name in KEPT_EVENTS:
                    keep = {k: event[k] for k in KEPT_EVENT_FIELDS if k in event}
                    keep["afterCall"] = call_index - 1
                    turn["events"].append(keep)
                if name == "direct_round_observation":
                    turn["deniedCalls"].extend(_denied_calls(event, call_index - 1))
                continue
            if step.get("type") != "contentBlock":
                continue
            for block in step.get("content", []):
                kind = block.get("type")
                if kind == "toolCallRequest":
                    call = {"index": call_index, "name": block.get("name"), "plugin": block.get("pluginIdentifier"),
                            "args": _routing_args(block.get("parameters")), "result": None, "failureKind": None}
                    call_index += 1
                    pending[str(block.get("toolCallRequestId"))] = call
                    turn["calls"].append(call)
                    text_after_last_call = []
                elif kind == "toolCallResult":
                    call = pending.get(str(block.get("toolCallRequestId")))
                    if call is not None:
                        call["result"] = result_flags(_decode_result(block.get("content")))
                        call["failureKind"] = failure_kind(call["result"], str(call["name"] or ""))
                        _mark_complete_line_range(call)
                elif kind == "text":
                    text_after_last_call.append(str(block.get("text", "")))
        if text_after_last_call:
            turn["finalText"] = "".join(text_after_last_call)
    for turn in turns:
        calls = turn["calls"]
        for i, call in enumerate(calls):
            if call["failureKind"] and call["name"] in READ_FAMILY | {"evidence_first_validate"}:
                follow = next((c for c in calls[i + 1:i + 7] if c["name"] in READ_FAMILY), None)
                call["followUp"] = _relation(call, follow)
        _mark_rereads(calls, turn["events"])
        turn["claims"] = extract_claims(turn.pop("finalText"), calls)
    return {"models": sorted(models), "loadedBuildIds": sorted(builds), "projectedInput": "not_recorded",
            "turns": turns}


def extract_claims(text: str, calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    validations = [c for c in calls if c["name"] == "evidence_first_validate" and c.get("result")]
    validation_scopes = {c["result"].get("validationScope") for c in validations}
    factual_validation = any(c["result"].get("factualEvidenceVerified") is True for c in validations)
    claims = []
    for sentence in re.split(r"(?<=[.!?。])\s+|\n+", text):
        sentence = sentence.strip()
        if not sentence:
            continue
        kinds = []
        if OVERCLAIM.search(sentence):
            kinds.append("overclaim_wording")
        if ABSENCE.search(sentence):
            kinds.append("absence_claim")
        if not kinds:
            continue
        item = {"kinds": kinds, "sentence": _short(sentence, 240)}
        if re.search(r"검증 완료|verified", sentence, re.I) and not factual_validation \
                and "packet_structure_and_record_consistency" in validation_scopes:
            item["note"] = "only structure validation was recorded in this turn"
        claims.append(item)
    return claims


def summarize(trace: dict[str, Any]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    relations: dict[str, int] = {}
    for turn in trace["turns"]:
        for call in turn["calls"]:
            if call["failureKind"]:
                counts[call["failureKind"]] = counts.get(call["failureKind"], 0) + 1
                rel = (call.get("followUp") or {}).get("relation")
                if rel:
                    relations[rel] = relations.get(rel, 0) + 1
    rereads: dict[str, int] = {}
    denied: dict[str, int] = {}
    for turn in trace["turns"]:
        for call in turn["calls"]:
            kind = (call.get("reread") or {}).get("kind")
            if kind:
                rereads[kind] = rereads.get(kind, 0) + 1
        for item in turn.get("deniedCalls", []):
            reason = str(item.get("budgetDenialReason") or item["validationState"])
            denied[reason] = denied.get(reason, 0) + 1
    return {"failureKinds": counts, "followUpRelations": relations,
            "callCount": sum(len(t["calls"]) for t in trace["turns"]), "rereads": rereads, "deniedCalls": denied}


def render_markdown(name: str, trace: dict[str, Any]) -> str:
    summary = summarize(trace)
    lines = [f"# Trace {name}", "",
             f"- models: {', '.join(trace['models']) or 'unknown'}",
             f"- loadedBuildIds: {', '.join(trace['loadedBuildIds']) or 'unknown'}",
             f"- projected model input: {trace['projectedInput']}",
             f"- calls: {summary['callCount']}; failure kinds: {json.dumps(summary['failureKinds'], ensure_ascii=False)}",
             f"- follow-up relations: {json.dumps(summary['followUpRelations'], ensure_ascii=False)}",
             f"- rereads: {json.dumps(summary['rereads'], ensure_ascii=False)}; "
             f"denied calls: {json.dumps(summary['deniedCalls'], ensure_ascii=False)}", ""]
    for number, turn in enumerate(trace["turns"], 1):
        lines += [f"## Turn {number} (user sha256 {str(turn['userMessageSha256'])[:12]})", "",
                  "| # | tool | args | failure | key result fields | follow-up |", "|---|---|---|---|---|---|"]
        for call in turn["calls"]:
            if not call["failureKind"]:
                continue
            result = {k: v for k, v in (call["result"] or {}).items() if k != "ok"}
            follow = call.get("followUp") or {}
            lines.append("| {} | {} | `{}` | {} | `{}` | {} |".format(
                call["index"], call["name"], json.dumps(call["args"], ensure_ascii=False).replace("|", "\\|"),
                call["failureKind"], json.dumps(result, ensure_ascii=False).replace("|", "\\|")[:300],
                f"{follow.get('relation')} → #{follow.get('nextCall')}" if follow else ""))
        states = [e for e in turn["events"]
                  if e.get("event") in {"working_context_restore", "resume_state", "execution_outcome"}]
        if states:
            lines += ["", "Execution state: " + "; ".join(
                f"{e['event']}={e.get('status', e.get('executionOutcome', e.get('priorTurnInterrupted')))}"
                for e in states)]
        if turn.get("deniedCalls"):
            lines += ["", "Denied before execution:", ""]
            lines += [f"- {d['toolName']} {d['validationState']} {d.get('budgetDenialReason', '')} after #{d['afterCall']} "
                      f"{json.dumps(d.get('budgetAtDenial', {}), ensure_ascii=False)}" for d in turn["deniedCalls"]]
        if turn["claims"]:
            lines += ["", "Claims in the final text:", ""]
            lines += [f"- [{', '.join(c['kinds'])}] {c['sentence']}" + (f" — {c['note']}" if c.get("note") else "")
                      for c in turn["claims"]]
        lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("conversation", type=Path)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)
    trace = parse_conversation(json.loads(args.conversation.read_text(encoding="utf-8")))
    name = args.conversation.name.split(".")[0]
    if args.out:
        args.out.mkdir(parents=True, exist_ok=True)
        (args.out / f"trace-{name}.json").write_text(json.dumps(trace, ensure_ascii=False, indent=1), encoding="utf-8")
        (args.out / f"trace-{name}.md").write_text(render_markdown(name, trace), encoding="utf-8")
    sys.stdout.write(json.dumps(summarize(trace), ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
