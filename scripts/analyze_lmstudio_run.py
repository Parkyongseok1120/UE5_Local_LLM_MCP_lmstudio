"""Summarize one assistant turn of a saved LM Studio conversation (read-only).

Reads ``~/.lmstudio/conversations/<id>.conversation.json`` and reports per-run
metrics: round timing, token totals, tool calls, projections, reasoning spikes,
source files read (and re-read), guidance selection changes, and write/build
outcomes. ``--json`` prints the same data as JSON; ``--compare`` adds a
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
    tool_calls: Counter[str] = Counter()
    thinking_blocks = 0

    for step in version.get("steps") or []:
        step_type = step.get("type")
        if step_type == "debugInfoBlock":
            event = _json_or_none(step.get("debugInfo"))
            if not isinstance(event, dict):
                continue
            name = event.get("event")
            if name == "direct_round_observation":
                rounds.append(event)
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
