"""Read-only GUI telemetry collector. Does not send prompts or control LM Studio."""
import hashlib
import json
import pathlib
import re
import sys

source = pathlib.Path(sys.argv[1])
output = pathlib.Path(__file__).parent
chat = json.loads(source.read_text(encoding="utf8"))
events, reads, answers, requests = [], [], [], []
for message in chat.get("messages", []):
    for version in message.get("versions", []):
        for step in version.get("steps", []):
            if "debugInfo" in step:
                events.append(json.loads(step["debugInfo"]))
            if step.get("style", {}).get("type") == "thinking":
                continue
            for part in step.get("content", []):
                if part.get("type") == "text" and step.get("roleOverride") == "assistant":
                    answers.append(part.get("text", ""))
                if part.get("type") == "toolCallRequest":
                    requests.append({k: part.get(k) for k in ["toolCallRequestId", "name", "parameters"]})
                if part.get("type") != "toolCallResult":
                    continue
                try:
                    value = json.loads(part["content"])
                    if isinstance(value, list):
                        value = json.loads(next(x["text"] for x in value if x.get("type") == "text"))
                    reads.append({"callId": part.get("toolCallRequestId"), **{k: v for k, v in value.items()
                        if k not in ["receipt", "nextCursor", "cursor", "transport", "approvalId"]}})
                except (ValueError, TypeError, KeyError, StopIteration):
                    reads.append({"callId": part.get("toolCallRequestId"), "decodeError": True})

measurements = {e["modelInputId"]: e for e in events if e.get("event") == "direct_context_measurement"}
water = {e["modelInputId"]: e for e in events if e.get("event") == "context_low_water"}
rounds = [e for e in events if e.get("event") == "direct_round_observation"]
compacted = False
rows = []
cumulative = post_compaction = 0
for event in rounds:
    key = event["modelInputId"]
    m, w = measurements.get(key, {}), water.get(key, {})
    actual_compaction = bool(m.get("compacted") and m.get("compactionAppliedCount", 0) > 0)
    compacted |= actual_compaction
    exact = m.get("finalInputTokens", 0) if m.get("finalExactMeasurement") else 0
    cumulative += exact
    if compacted:
        post_compaction += exact
    rows.append({"modelInputId": key, "roundIndex": event.get("roundIndex"), "exactInputTokens": exact,
        "compacted": actual_compaction, "low": w.get("effectiveLowWaterTokens"), "high": w.get("effectiveHighWaterTokens"),
        "lowMet": exact <= w.get("effectiveLowWaterTokens", 0), "mandatoryFloor": w.get("mandatoryFloorTokens"),
        "finishReason": event.get("finishReason"), "backendStats": event.get("predictionStats"),
        "prefillMs": event.get("modelPrefillMs"), "cumulativeExactInputTokens": cumulative,
        "postCompactionCumulativeExactInputTokens": post_compaction})

pages = [r for r in reads if r.get("path") == "Packages/packages-lock.json" and isinstance(r.get("startLine"), int)]
errors = [r for r in reads if r.get("errorCode") or r.get("error") or r.get("decodeError")]
contiguous = all(p["startLine"] == (pages[i-1]["endLine"] + 1 if i else 1) for i, p in enumerate(pages))
stable = len({p.get("hash") for p in pages}) <= 1
complete = bool(pages and pages[-1].get("hasMore") is False and contiguous)
summary = {"sourceConversation": str(source), "identities": [e for e in events if e.get("event") == "direct_runtime_identity"],
    "metric": "sum of exact templated inputs of completed actual research/report predictions; after first real compaction",
    "target": 380000, "cumulativeExactInputTokens": cumulative, "postCompactionCumulativeExactInputTokens": post_compaction,
    "rounds": rows, "compactions": sum(r["compacted"] for r in rows), "toolErrors": errors,
    "pages": len(pages), "lastLine": pages[-1].get("endLine") if pages else None,
    "pageCoverageContiguous": contiguous, "pageHashesStable": stable, "fileReadComplete": complete,
    "answers": answers, "requests": requests, "reads": reads,
    "semanticSummaryCalls": [e for e in events if e.get("event") == "semantic_handoff"],
    "completed": bool(rounds and rounds[-1].get("finishReason") in ["eosFound", "stopStringFound"]),
    "limitations": ["promptTokensCount is a backend statistic, not a cache/prefill token count.",
        "prefillMs is time, not tokens; cache counters are not inferred.", "Source coverage and final answer correctness are validated separately."]}
(output / "live-gui.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf8")
(output / "live-gui-events.json").write_text(json.dumps(events, ensure_ascii=False, indent=2), encoding="utf8")
print(json.dumps({k: summary[k] for k in ["cumulativeExactInputTokens", "postCompactionCumulativeExactInputTokens", "compactions", "pages", "lastLine", "fileReadComplete", "completed"]}))
print(json.dumps({"lastRounds": rows[-3:], "errors": errors}, ensure_ascii=True))
