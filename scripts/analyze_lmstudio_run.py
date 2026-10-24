"""Summarize one assistant turn of a saved LM Studio conversation (read-only).

Reads ``~/.lmstudio/conversations/<id>.conversation.json`` and reports per-run
metrics: round timing, token totals, tool calls, projections, reasoning spikes,
source files read (and re-read), body deliveries per file (by path + sha + range,
across source reads and archive pages, with redundant same-version deliveries,
re-reads right after a compaction, cross-evidence version mismatches and
invented read handles), guidance selection changes, and write/build outcomes. ``--json`` prints the same data as JSON; ``--compare`` adds a
side-by-side table against a second conversation.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any


READ_TOOLS = {"read_file", "read_file_range", "read_symbol"}
WRITE_BUILD_TOOLS = ("replace_in_file", "apply_edit_bundle", "write_file", "build_unreal_project")
REASONING_SPIKE_TOKENS = 4000
ARCHIVE_READ_TOOL = "evidence_first_read_context"
# Source code runs about 0.3 tokens per character; this only scales chars.
CHARS_PER_TOKEN_ESTIMATE = 3.5
# A re-read this many rounds after a compaction event counts as compaction-driven.
REREAD_AFTER_COMPACTION_ROUNDS = 2
HANDLE_ERRORS = {"read_handle_unavailable", "invalid_read_handle"}
SOURCE_SCHEMES = {"project": "project://", "engine": "engine://"}


def conversations_root() -> Path:
    return Path.home() / ".lmstudio" / "conversations"


def resolve_conversation_path(value: str) -> Path:
    """Accept a path or a bare conversation id."""
    path = Path(value).expanduser()
    if path.is_file():
        return path
    candidate = conversations_root() / f"{value}.conversation.json"
    if candidate.is_file():
        return candidate
    raise FileNotFoundError(f"conversation not found: {value}")


def load_conversation(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _selected_version(message: dict[str, Any]) -> dict[str, Any]:
    versions = message.get("versions") or []
    if not versions:
        return {}
    index = message.get("currentlySelected", 0)
    if not isinstance(index, int) or not 0 <= index < len(versions):
        index = 0
    return versions[index] or {}


def assistant_message_indexes(conversation: dict[str, Any]) -> list[int]:
    return [
        index
        for index, message in enumerate(conversation.get("messages") or [])
        if _selected_version(message).get("role") == "assistant"
    ]


def _json_or_none(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except ValueError:
        return None


def _tool_result_payload(content: Any) -> Any:
    """Unwrap ``[{"type":"text","text":"<json>"}]`` into the inner JSON value."""
    outer = _json_or_none(content)
    if isinstance(outer, list):
        texts = [item.get("text") for item in outer if isinstance(item, dict) and item.get("type") == "text"]
        if len(texts) == 1:
            inner = _json_or_none(texts[0])
            return inner if inner is not None else texts[0]
        return texts
    return outer


def _source_kind(path: str) -> str | None:
    for kind, prefix in SOURCE_SCHEMES.items():
        if path.startswith(prefix):
            return kind
    return None


def _returned_line_span(payload: dict[str, Any]) -> tuple[int, int] | None:
    start, end = payload.get("startLine"), payload.get("endLine")
    if not (isinstance(start, int) and isinstance(end, int)):
        span = payload.get("range")
        if isinstance(span, dict):
            start, end = span.get("start"), span.get("end")
    if isinstance(start, int) and isinstance(end, int) and end >= start:
        return start, end
    return None


def _merge_spans(spans: list[tuple[int, int]]) -> int:
    covered = 0
    current: tuple[int, int] | None = None
    for start, end in sorted(spans):
        if current is None or start > current[1] + 1:
            if current is not None:
                covered += current[1] - current[0] + 1
            current = (start, end)
        else:
            current = (current[0], max(current[1], end))
    if current is not None:
        covered += current[1] - current[0] + 1
    return covered


def _ms_to_s(value: float) -> float:
    return round(value / 1000.0, 1)


def analyze_message(conversation: dict[str, Any], message_index: int) -> dict[str, Any]:
    messages = conversation.get("messages") or []
    if not 0 <= message_index < len(messages):
        raise IndexError(f"message index {message_index} out of range (0..{len(messages) - 1})")
    version = _selected_version(messages[message_index])
    if version.get("role") != "assistant":
        raise ValueError(f"message {message_index} is not an assistant turn")

    rounds: list[dict[str, Any]] = []
    projections: list[dict[str, Any]] = []
    guidance_events: list[dict[str, Any]] = []
    recovery_count = 0
    build_ids: list[str] = []
    requests: dict[str, dict[str, Any]] = {}
    results: list[tuple[str, Any]] = []
    result_rounds: list[int] = []
    raw_texts: list[str] = []
    result_text_index: list[int] = []
    ledger_events: list[dict[str, Any]] = []
    report_now_events: list[dict[str, Any]] = []
    tool_calls: Counter[str] = Counter()
    thinking_blocks = 0

    for step in version.get("steps") or []:
        step_type = step.get("type")
        if step_type == "debugInfoBlock":
            event = _json_or_none(step.get("debugInfo"))
            if not isinstance(event, dict):
                continue
            name = event.get("event")
            raw_texts.append(str(step.get("debugInfo")))
            if name == "direct_round_observation":
                rounds.append(event)
            elif name == "read_ledger":
                ledger_events.append(event)
            elif name == "report_now":
                report_now_events.append(event)
            elif name == "working_context_projection":
                projections.append(event)
            elif name == "reasoning_recovery_scheduled":
                recovery_count += 1
            elif name == "design_guidance_input":
                guidance_events.append(event)
            elif name == "direct_runtime_identity" and event.get("loadedBuildId"):
                build_ids.append(str(event["loadedBuildId"]))
        elif step_type == "contentBlock":
            is_thinking = (step.get("style") or {}).get("type") == "thinking"
            for item in step.get("content") or []:
                item_type = item.get("type")
                if item_type == "text" and is_thinking:
                    thinking_blocks += 1
                elif item_type == "toolCallRequest":
                    tool_calls[str(item.get("name"))] += 1
                    requests[str(item.get("toolCallRequestId"))] = item
                elif item_type == "toolCallResult":
                    request_id = str(item.get("toolCallRequestId"))
                    results.append((request_id, _tool_result_payload(item.get("content"))))
                    # Results precede their round's direct_round_observation event.
                    result_rounds.append(len(rounds))
                    result_text_index.append(len(raw_texts))
                    raw_texts.append(str(item.get("content")))

    # Timing and tokens. executionElapsedMs is cumulative across the run.
    prefill_ms = sum(r.get("modelPrefillMs") or 0 for r in rounds)
    generation_ms = sum(r.get("modelGenerationElapsedMs") or 0 for r in rounds)
    execution_ms = max(((r.get("executionCost") or {}).get("executionElapsedMs") or 0 for r in rounds), default=0)
    prompt_tokens = sum((r.get("predictionStats") or {}).get("promptTokensCount") or 0 for r in rounds)
    predicted_tokens = sum((r.get("predictionStats") or {}).get("predictedTokensCount") or 0 for r in rounds)
    reasoning_tokens = sum((r.get("predictionUsage") or {}).get("reasoningTokensCount") or 0 for r in rounds)
    rounds_by_index = {r.get("roundIndex"): r for r in rounds}

    changed_projections = [p for p in projections if p.get("changed")]
    projection_rounds = []
    for projection in changed_projections:
        observed = rounds_by_index.get(projection.get("roundIndex")) or {}
        projection_rounds.append({
            "roundIndex": projection.get("roundIndex"),
            "reason": projection.get("reason"),
            "inputTokens": projection.get("inputTokens"),
            "prefillS": _ms_to_s(observed.get("modelPrefillMs") or 0) if observed else None,
        })

    reasoning_spikes = [
        {
            "roundIndex": r.get("roundIndex"),
            "reasoningTokens": (r.get("predictionUsage") or {}).get("reasoningTokensCount"),
            "finishReason": r.get("finishReason"),
        }
        for r in rounds
        if ((r.get("predictionUsage") or {}).get("reasoningTokensCount") or 0) > REASONING_SPIKE_TOKENS
    ]

    # Source reads.
    file_spans: dict[str, list[tuple[int, int]]] = {}
    read_failures: Counter[str] = Counter()
    write_build: dict[str, Counter[str]] = {name: Counter() for name in WRITE_BUILD_TOOLS}
    tool_errors: Counter[str] = Counter()
    for request_id, payload in results:
        request = requests.get(request_id) or {}
        name = str(request.get("name"))
        error_code = payload.get("errorCode") if isinstance(payload, dict) else None
        if error_code:
            tool_errors[f"{name}:{error_code}"] += 1
        if name in write_build:
            outcome = str(error_code) if error_code else "ok" if isinstance(payload, dict) and payload.get("ok") is True else "failed_without_code" if isinstance(payload, dict) and payload.get("ok") is False else "unknown_result"
            write_build[name][outcome] += 1
        if name not in READ_TOOLS:
            continue
        if not isinstance(payload, dict) or error_code or payload.get("ok") is False:
            read_failures[str(error_code or "unparsed")] += 1
            continue
        path = str(payload.get("path") or (request.get("parameters") or {}).get("path") or "")
        span = _returned_line_span(payload)
        body = next((payload[key] for key in ("content", "text", "body", "code") if isinstance(payload.get(key), str)), None)
        if _source_kind(path) and span and body is not None:
            file_spans.setdefault(path, []).append(span)

    files_by_kind = {kind: sorted(p for p in file_spans if _source_kind(p) == kind) for kind in SOURCE_SCHEMES}
    returned_lines = sum(end - start + 1 for spans in file_spans.values() for start, end in spans)
    unique_lines = sum(_merge_spans(spans) for spans in file_spans.values())
    per_file = [
        {
            "path": path,
            "reads": len(spans),
            "returnedLines": sum(end - start + 1 for start, end in spans),
            "uniqueLines": _merge_spans(spans),
        }
        for path, spans in sorted(file_spans.items())
    ]

    # Guidance selection changes.
    guidance_changes = []
    previous: list[str] | None = None
    for event in guidance_events:
        selected = [str(x) for x in event.get("selectedIds") or []]
        if previous is None or selected != previous:
            guidance_changes.append({
                "roundIndex": event.get("roundIndex"),
                "selectedIds": selected,
                "added": [x for x in selected if x not in (previous or [])],
                "removed": [x for x in (previous or []) if x not in selected],
                "reason": event.get("reason"),
                "omissionReasons": list(event.get("omissionReasons") or []),
            })
        previous = selected

    deliveries = delivery_metrics(requests, results, result_rounds, raw_texts, result_text_index,
                                  [p.get("roundIndex") for p in changed_projections], ledger_events)

    return {
        "messageIndex": message_index,
        "loadedBuildIds": sorted(set(build_ids)),
        "rounds": len(rounds),
        "finishReasons": dict(Counter(str(r.get("finishReason")) for r in rounds)),
        "toolCalls": sum(tool_calls.values()),
        "toolCallsByName": dict(tool_calls.most_common()),
        "thinkingBlocks": thinking_blocks,
        "executionS": _ms_to_s(execution_ms),
        "prefillS": _ms_to_s(prefill_ms),
        "generationS": _ms_to_s(generation_ms),
        "promptTokens": prompt_tokens,
        "predictedTokens": predicted_tokens,
        "reasoningTokens": reasoning_tokens,
        "projectionEvents": len(projections),
        "projections": len(changed_projections),
        "projectionRounds": projection_rounds,
        "projectionPrefillS": round(sum(p["prefillS"] or 0 for p in projection_rounds), 1),
        "reasoningRecoveries": recovery_count,
        "reasoningSpikes": reasoning_spikes,
        "projectFiles": len(files_by_kind["project"]),
        "engineFiles": len(files_by_kind["engine"]),
        "returnedLines": returned_lines,
        "uniqueLines": unique_lines,
        "rereadLines": returned_lines - unique_lines,
        "readFailures": dict(read_failures),
        "files": per_file,
        "guidanceByRound": [{"roundIndex": e.get("roundIndex"), "selectedIds": list(e.get("selectedIds") or []),
                             "reason": e.get("reason"), "omissionReasons": list(e.get("omissionReasons") or [])}
                            for e in guidance_events],
        "guidanceChanges": guidance_changes,
        "writeBuild": {name: {"calls": sum(c.values()), "outcomes": dict(c)} for name, c in write_build.items()},
        "toolErrors": dict(tool_errors.most_common()),
        "deliveries": deliveries,
        "redundantDeliveries": deliveries["redundant"],
        "duplicatedTokensEstimate": deliveries["duplicatedTokensEstimate"],
        "rereadsAfterCompaction": deliveries["rereadsAfterCompaction"],
        "reportNowEvents": [{"roundIndex": e.get("roundIndex"), "trigger": e.get("trigger"), "finalize": e.get("finalize")}
                            for e in report_now_events],
    }


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _covers(ranges: list[tuple[int, int]], target: tuple[int, int] | None) -> bool:
    return target is not None and any(start <= target[0] and end >= target[1] for start, end in ranges)


def _body(payload: dict[str, Any]) -> str | None:
    return next((payload[key] for key in ("content", "text", "body", "code") if isinstance(payload.get(key), str)), None)


def _delivery(name: str, payload: Any) -> dict[str, Any] | None:
    """One body returned to the model: its path, sha, range and size.

    Source reads give lines (or a byte window); an archive page gives the lines
    it returned, or its record's source range when it returned the whole body.
    A partial character page of a body has no line range of its own."""
    if not isinstance(payload, dict) or payload.get("ok") is False or payload.get("errorCode"):
        return None
    body = _body(payload)
    if body is None:
        return None
    if name in READ_TOOLS:
        path, sha, facts, source = payload.get("path"), payload.get("sha256") or payload.get("sourceVersion"), payload, "source"
    elif name == ARCHIVE_READ_TOOL and payload.get("kind") == "historical_evidence_range":
        identity = payload.get("sourceIdentity") or {}
        facts = payload.get("sourceFacts") or {}
        path, sha, source = identity.get("path"), payload.get("sourceVersion"), "archive"
    else:
        return None
    if not isinstance(path, str) or not path or not isinstance(sha, str) or not sha:
        return None
    lines = _returned_line_span(facts)
    total = _int(facts.get("totalLines"))
    offset, nxt, size = _int(facts.get("offsetBytes")), _int(facts.get("nextOffsetBytes")), _int(facts.get("size"))
    byte_range = (offset, nxt - 1) if offset is not None and nxt is not None and nxt > offset and lines is None else None
    complete = bool(lines and lines[0] == 1 and total and lines[1] >= total) or bool(
        byte_range and offset == 0 and size is not None and nxt >= size and facts.get("hasMore") is not True)
    if source == "archive":
        returned = payload.get("returnedLines")
        if isinstance(returned, list) and len(returned) == 2 and all(_int(x) is not None for x in returned) and returned[1] >= returned[0]:
            lines, byte_range, complete = (returned[0], returned[1]), None, False
        elif payload.get("bodyComplete") is not True:
            lines, byte_range, complete = None, None, False
    return {"path": path, "sha": sha.lower(), "lines": lines, "bytes": byte_range, "complete": complete,
            "chars": len(body), "source": source}


def delivery_metrics(requests: dict[str, dict[str, Any]], results: list[tuple[str, Any]], result_rounds: list[int],
                     raw_texts: list[str], result_text_index: list[int], projection_rounds: list[Any],
                     ledger_events: list[dict[str, Any]]) -> dict[str, Any]:
    """Per file: bodies delivered by path + sha + range, and which were redundant.

    A delivery is redundant when an earlier delivery of the same path and sha
    already covered its range (a whole-file delivery covers every range). A
    result the plugin replaced with an UNCHANGED fact (read_ledger event) was
    not a body the model received and is counted as suppressed instead.

    An invented handle is a failed handle read whose handle appears nowhere
    earlier in this transcript. Handles shown only inside a compacted model
    input are not in the transcript, so this is an upper bound."""
    replaced = {str(call) for event in ledger_events for call in event.get("replacedCallIds") or []}
    compactions = sorted(r for r in projection_rounds if isinstance(r, int))
    owners: dict[str, tuple[str, str]] = {}
    for _, payload in results:
        if isinstance(payload, dict) and isinstance(payload.get("evidenceId"), str) and isinstance(payload.get("version"), str):
            owners.setdefault(payload["version"], (payload["evidenceId"], str((payload.get("sourceIdentity") or {}).get("path") or "")))
    state: dict[tuple[str, str], dict[str, Any]] = {}
    files: dict[str, dict[str, Any]] = {}
    totals = Counter()
    mismatches: list[dict[str, Any]] = []
    invented: list[dict[str, Any]] = []
    for index, (request_id, payload) in enumerate(results):
        request = requests.get(request_id) or {}
        name = str(request.get("name"))
        params = request.get("parameters") or {}
        round_index = result_rounds[index] if index < len(result_rounds) else None
        error = payload.get("errorCode") if isinstance(payload, dict) else None
        if name == ARCHIVE_READ_TOOL and error == "version_mismatch":
            version = str(params.get("version") or "")
            owner = (payload.get("versionBelongsTo") or {}) if isinstance(payload, dict) else {}
            owner_id = owner.get("evidenceId") or (owners.get(version) or (None, None))[0]
            cross = bool(owner_id and owner_id != params.get("evidenceId"))
            mismatches.append({"roundIndex": round_index, "evidenceId": str(params.get("evidenceId") or "")[:11],
                               "version": version[:8], "crossEvidence": cross,
                               "versionBelongsTo": str(owner.get("path") or (owners.get(version) or (None, ""))[1] or "") or None})
        if name == ARCHIVE_READ_TOOL and error in HANDLE_ERRORS:
            handle = str(params.get("readHandle") or "")
            position = result_text_index[index] if index < len(result_text_index) else len(raw_texts)
            if handle and not any(handle in text for text in raw_texts[:position]):
                invented.append({"roundIndex": round_index, "readHandle": handle})
        delivery = _delivery(name, payload)
        if delivery is None:
            continue
        key = (delivery["path"], delivery["sha"])
        known = state.setdefault(key, {"lines": [], "bytes": [], "complete": False, "delivered": False})
        entry = files.setdefault(delivery["path"], {"path": delivery["path"], "deliveries": 0, "redundant": 0,
                                                    "duplicatedChars": 0, "suppressed": 0, "rereadsAfterCompaction": 0,
                                                    "shas": Counter(), "ranges": Counter()})
        rng = f"lines {delivery['lines'][0]}-{delivery['lines'][1]}" if delivery["lines"] else \
            f"bytes {delivery['bytes'][0]}-{delivery['bytes'][1] + 1}" if delivery["bytes"] else "chars (partial page)"
        if str(request_id) in replaced:
            entry["suppressed"] += 1
            totals["suppressed"] += 1
            continue
        redundant = known["delivered"] and (known["complete"] or _covers(known["lines"], delivery["lines"])
                                            or _covers(known["bytes"], delivery["bytes"]))
        entry["deliveries"] += 1
        entry["shas"][delivery["sha"][:8]] += 1
        entry["ranges"][f"{delivery['sha'][:8]} {rng}"] += 1
        totals[f"{delivery['source']}Deliveries"] += 1
        totals["deliveries"] += 1
        if delivery["source"] == "source" and known["delivered"]:
            totals["rereads"] += 1
        if redundant:
            entry["redundant"] += 1
            entry["duplicatedChars"] += delivery["chars"]
            totals["redundant"] += 1
            totals["duplicatedChars"] += delivery["chars"]
            after = round_index is not None and any(0 <= round_index - r <= REREAD_AFTER_COMPACTION_ROUNDS for r in compactions)
            if delivery["source"] == "source" and after:
                entry["rereadsAfterCompaction"] += 1
                totals["rereadsAfterCompaction"] += 1
        known["delivered"] = True
        known["complete"] = known["complete"] or delivery["complete"]
        if delivery["lines"]:
            known["lines"].append(delivery["lines"])
        if delivery["bytes"]:
            known["bytes"].append(delivery["bytes"])
    per_file = []
    for entry in sorted(files.values(), key=lambda item: (-item["duplicatedChars"], item["path"])):
        per_file.append({**entry, "shas": dict(entry["shas"]), "ranges": dict(entry["ranges"].most_common()),
                         "duplicatedTokensEstimate": round(entry["duplicatedChars"] / CHARS_PER_TOKEN_ESTIMATE)})
    return {
        "deliveries": totals["deliveries"],
        "sourceDeliveries": totals["sourceDeliveries"],
        "archiveDeliveries": totals["archiveDeliveries"],
        "sourceRereads": totals["rereads"],
        "redundant": totals["redundant"],
        "duplicatedChars": totals["duplicatedChars"],
        "duplicatedTokensEstimate": round(totals["duplicatedChars"] / CHARS_PER_TOKEN_ESTIMATE),
        "rereadsAfterCompaction": totals["rereadsAfterCompaction"],
        "suppressedByLedger": totals["suppressed"],
        "versionMismatches": len(mismatches),
        "crossEvidenceVersionMismatches": sum(1 for item in mismatches if item["crossEvidence"]),
        "versionMismatchDetails": mismatches,
        "inventedHandles": len(invented),
        "inventedHandleDetails": invented,
        "ledgerRedundantRereads": sum(int(e.get("redundantRereads") or 0) for e in ledger_events),
        "ledgerArchiveRepages": sum(int(e.get("archiveRepages") or 0) for e in ledger_events),
        "files": per_file,
    }


def analyze_path(value: str, message_index: int | None) -> dict[str, Any]:
    path = resolve_conversation_path(value)
    conversation = load_conversation(path)
    if message_index is None:
        indexes = assistant_message_indexes(conversation)
        if not indexes:
            raise ValueError(f"no assistant turns in {path}")
        message_index = indexes[-1]
    report = analyze_message(conversation, message_index)
    report["conversation"] = path.stem.removesuffix(".conversation")
    report["path"] = str(path)
    return report


COMPARE_FIELDS = (
    ("rounds", "rounds"),
    ("toolCalls", "tool calls"),
    ("executionS", "execution s"),
    ("prefillS", "prefill s"),
    ("generationS", "generation s"),
    ("promptTokens", "prompt tokens"),
    ("predictedTokens", "predicted tokens"),
    ("reasoningTokens", "reasoning tokens"),
    ("projections", "projections"),
    ("projectionPrefillS", "projection prefill s"),
    ("reasoningRecoveries", "reasoning recoveries"),
    ("projectFiles", "project files"),
    ("engineFiles", "engine files"),
    ("returnedLines", "returned lines"),
    ("rereadLines", "re-read lines"),
    ("redundantDeliveries", "redundant deliveries"),
    ("duplicatedTokensEstimate", "duplicated tokens est"),
    ("rereadsAfterCompaction", "re-reads after compaction"),
)


def _fmt(value: Any) -> str:
    return f"{value:.1f}" if isinstance(value, float) else str(value)


def format_report(report: dict[str, Any]) -> str:
    out = [f"conversation {report['conversation']} message {report['messageIndex']}"]
    if report["loadedBuildIds"]:
        out.append(f"  loaded build: {', '.join(report['loadedBuildIds'])}")
    out.append(f"  rounds: {report['rounds']}  finish: {report['finishReasons']}")
    out.append(f"  tool calls: {report['toolCalls']}")
    for name, count in report["toolCallsByName"].items():
        out.append(f"    {name}: {count}")
    out.append(
        f"  time: execution {report['executionS']:.1f} s, prefill {report['prefillS']:.1f} s, "
        f"generation {report['generationS']:.1f} s"
    )
    out.append(
        f"  tokens: prompt {report['promptTokens']}, predicted {report['predictedTokens']}, "
        f"reasoning {report['reasoningTokens']}"
    )
    out.append(
        f"  projections: {report['projections']} changed of {report['projectionEvents']} events, "
        f"prefill on those rounds {report['projectionPrefillS']:.1f} s"
    )
    for p in report["projectionRounds"]:
        out.append(f"    round {p['roundIndex']}: {p['reason']} input {p['inputTokens']} prefill {_fmt(p['prefillS'])} s")
    out.append(f"  reasoning recoveries: {report['reasoningRecoveries']}")
    out.append(f"  rounds with > {REASONING_SPIKE_TOKENS} reasoning tokens: {len(report['reasoningSpikes'])}")
    for s in report["reasoningSpikes"]:
        out.append(f"    round {s['roundIndex']}: {s['reasoningTokens']} ({s['finishReason']})")
    out.append(
        f"  source reads: {report['projectFiles']} project files, {report['engineFiles']} engine files, "
        f"{report['returnedLines']} lines returned, {report['rereadLines']} re-read"
    )
    for f in report["files"]:
        reread = f["returnedLines"] - f["uniqueLines"]
        out.append(f"    {f['path']}: {f['reads']} reads, {f['returnedLines']} lines" + (f", {reread} re-read" if reread else ""))
    if report["readFailures"]:
        out.append(f"  read failures: {report['readFailures']}")
    d = report["deliveries"]
    out.append(
        f"  body deliveries: {d['deliveries']} ({d['sourceDeliveries']} source, {d['archiveDeliveries']} archive), "
        f"{d['redundant']} redundant, ~{d['duplicatedTokensEstimate']} duplicated tokens ({d['duplicatedChars']} chars), "
        f"{d['sourceRereads']} source re-reads of a delivered version, {d['rereadsAfterCompaction']} within "
        f"{REREAD_AFTER_COMPACTION_ROUNDS} rounds after a compaction"
    )
    out.append(
        f"  suppressed by ledger: {d['suppressedByLedger']}, ledger redundant re-reads: {d['ledgerRedundantRereads']}, "
        f"archive re-pages: {d['ledgerArchiveRepages']}, report_now: {len(report['reportNowEvents'])}"
    )
    out.append(
        f"  version mismatches: {d['versionMismatches']} ({d['crossEvidenceVersionMismatches']} with another record's version), "
        f"invented handles: {d['inventedHandles']}"
    )
    for f in d["files"]:
        if not f["redundant"] and not f["suppressed"]:
            continue
        out.append(
            f"    {f['path']}: {f['deliveries']} deliveries, {f['redundant']} redundant (~{f['duplicatedTokensEstimate']} tokens), "
            f"{f['rereadsAfterCompaction']} after compaction" + (f", {f['suppressed']} suppressed" if f["suppressed"] else "")
        )
    out.append(f"  guidance selection changes: {len(report['guidanceChanges'])}")
    for g in report["guidanceChanges"]:
        delta = " ".join([*(f"+{x}" for x in g["added"]), *(f"-{x}" for x in g["removed"])]) or "(cleared)"
        omitted = f" omitted: {', '.join(g['omissionReasons'])}" if g["omissionReasons"] else ""
        out.append(f"    round {g['roundIndex']}: {delta} [{g['reason']}]{omitted}")
    out.append("  guidance by round:")
    for g in report["guidanceByRound"]:
        out.append(f"    round {g['roundIndex']}: selected={g['selectedIds']} reason={g['reason']} omitted={g['omissionReasons']}")
    out.append("  write/build:")
    for name, entry in report["writeBuild"].items():
        outcomes = ", ".join(f"{k}={v}" for k, v in entry["outcomes"].items())
        out.append(f"    {name}: {entry['calls']}" + (f" ({outcomes})" if outcomes else ""))
    if report["toolErrors"]:
        out.append("  tool errors:")
        for key, count in report["toolErrors"].items():
            out.append(f"    {key}: {count}")
    return "\n".join(out)


def format_compare(left: dict[str, Any], right: dict[str, Any]) -> str:
    left_name = f"{left['conversation']}#{left['messageIndex']}"
    right_name = f"{right['conversation']}#{right['messageIndex']}"
    label_width = max(len(label) for _, label in COMPARE_FIELDS)
    col = max(len(left_name), len(right_name), 10)
    rows = [f"{'metric':<{label_width}}  {left_name:>{col}}  {right_name:>{col}}  {'delta':>10}"]
    for key, label in COMPARE_FIELDS:
        a, b = left.get(key, 0), right.get(key, 0)
        delta = round(b - a, 1) if isinstance(a, float) or isinstance(b, float) else b - a
        rows.append(f"{label:<{label_width}}  {_fmt(a):>{col}}  {_fmt(b):>{col}}  {_fmt(delta):>10}")
    return "\n".join(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("conversation", help="conversation file path or id under ~/.lmstudio/conversations")
    parser.add_argument("--message-index", type=int, help="assistant message index (default: last assistant turn)")
    parser.add_argument("--compare", metavar="OTHER", help="second conversation path or id to compare against")
    parser.add_argument("--compare-message-index", type=int, help="message index for --compare (default: last assistant turn)")
    parser.add_argument("--json", action="store_true", help="print JSON instead of text")
    args = parser.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        report = analyze_path(args.conversation, args.message_index)
        other = analyze_path(args.compare, args.compare_message_index) if args.compare else None
    except (OSError, ValueError, IndexError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.json:
        payload: dict[str, Any] = {"run": report}
        if other is not None:
            payload["compare"] = other
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(format_report(report))
        if other is not None:
            print()
            print(format_report(other))
            print()
            print(format_compare(report, other))
    return 0


if __name__ == "__main__":
    sys.exit(main())
