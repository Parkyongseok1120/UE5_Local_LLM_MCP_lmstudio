"""Observe one GUI run without controlling the model. Join by ID, never hide collisions with roundIndex."""
import collections
import json
import pathlib
import sys

source = pathlib.Path(sys.argv[1])
out = pathlib.Path(sys.argv[2]) if len(sys.argv) > 2 else pathlib.Path(__file__).parent / "identity-only-live"
out.mkdir(exist_ok=True)
chat = json.loads(source.read_text(encoding="utf-8"))
events, results, answers = [], [], []
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
                if part.get("type") == "toolCallResult":
                    try:
                        value = json.loads(part["content"])
                        if isinstance(value, list):
                            value = json.loads(next(x["text"] for x in value if x.get("type") == "text"))
                        results.append({k: v for k, v in value.items()
                                        if k not in ["receipt", "cursor", "nextCursor", "transport", "approvalId"]})
                    except (ValueError, TypeError, KeyError, StopIteration):
                        results.append({"decodeError": True})

measurements = [e for e in events if e.get("event") == "direct_context_measurement"]
observations = [e for e in events if e.get("event") == "direct_round_observation"]
by_id = collections.defaultdict(list)
for e in measurements:
    by_id[e["modelInputId"]].append(e)
duplicates = {k: [m["roundIndex"] for m in v] for k, v in by_id.items() if len(v) > 1}
observation_counts = collections.Counter(e["modelInputId"] for e in observations)
rows, mismatches, causal_mismatches, exposure_mismatches = [], [], [], []
cumulative = post_compaction = 0
has_compacted = False
causal_count = exposure_count = 0
for e in observations:
    matches = by_id[e["modelInputId"]]
    m = matches[0] if len(matches) == 1 else {}
    exact = m.get("finalInputTokens") if m.get("finalExactMeasurement") else None
    backend = (e.get("predictionStats") or {}).get("promptTokensCount")
    matched = len(matches) == 1 and e.get("roundIndex") == m.get("roundIndex")
    numeric_match = matched and exact is not None and backend is not None and exact == backend
    has_compacted |= bool(numeric_match and m.get("compacted") and m.get("compactionAppliedCount", 0) > 0)
    if numeric_match:
        cumulative += exact
        if has_compacted:
            post_compaction += exact
    if backend is not None and not numeric_match:
        mismatches.append(e["modelInputId"])
    trace = e.get("toolTrace", {})
    captured_trace = trace.get("captured", {})
    for entry in [*trace.get("runtime", []), *captured_trace.get("requests", []), *captured_trace.get("results", [])]:
        causal_count += 1
        if entry.get("causalModelInputId") != e["modelInputId"]:
            causal_mismatches.append(entry)
    for entry in e.get("workingContextExposure", []):
        exposure_count += 1
        if entry.get("modelInputId") != e["modelInputId"]:
            exposure_mismatches.append(entry)
    rows.append({"id": e["modelInputId"], "roundIndex": e.get("roundIndex"),
                 "purpose": e.get("callPurpose"), "recoveryToolRounds": e.get("recoveryToolRounds"),
                 "exact": exact, "backend": backend, "matchedByIdOnly": numeric_match,
                 "compacted": m.get("compacted"), "finishReason": e.get("finishReason"),
                 "cumulativeExactInputTokens": cumulative,
                 "postCompactionCumulativeExactInputTokens": post_compaction,
                 "errors": (e.get("recoveryProgress") or {}).get("errorCount", 0)})
reset_transitions = []
for before, after in zip(rows, rows[1:]):
    if (before.get("recoveryToolRounds") or 0) > (after.get("recoveryToolRounds") or 0):
        reset_transitions.append({"before": before, "after": after})
identities = [e for e in events if e.get("event") == "direct_runtime_identity"]
compacted = [m for m in measurements if m.get("compacted")]
verified_compactions = [r for r in rows if r["compacted"] and r["matchedByIdOnly"]]
water_by_id = {e["modelInputId"]: e for e in events if e.get("event") == "context_low_water"}
summary = {
    "sourceConversation": str(source), "identities": identities,
    "targetPostCompactionTokens": 390000,
    "cumulativeExactInputTokens": cumulative,
    "postCompactionCumulativeExactInputTokens": post_compaction,
    "targetReached": post_compaction >= 390000,
    "measurements": len(measurements), "observedRounds": len(observations),
    "duplicateMeasurementIds": duplicates,
    "duplicateObservationIds": {k: n for k, n in observation_counts.items() if n > 1},
    "exactBackendMismatches": mismatches, "causalIdMismatches": causal_mismatches,
    "causalEntriesChecked": causal_count, "exposureEntriesChecked": exposure_count,
    "exposureIdMismatches": exposure_mismatches,
    "verifiedExactBackendPairs": sum(r["matchedByIdOnly"] for r in rows),
    "unknownBackendCalls": sum(r["backend"] is None for r in rows),
    "recoveryCounterResetsObserved": reset_transitions,
    "retryEvents": [e for e in events if e.get("event") == "fresh_tool_planning_retry_scheduled"],
    "preparedCompactions": len(compacted), "compactions": len(verified_compactions),
    "postCompactionInputs": [r["exact"] for r in verified_compactions],
    "compactionLowWaterChecks": [{"id": r["id"], "exact": r["exact"],
        "low": water_by_id.get(r["id"], {}).get("effectiveLowWaterTokens"),
        "meetsLow": r["exact"] <= water_by_id.get(r["id"], {}).get("effectiveLowWaterTokens", 0)}
        for r in verified_compactions],
    "firstTargetCrossing": next((r for r in rows if r["postCompactionCumulativeExactInputTokens"] >= 390000), None),
    "toolErrors": [r for r in results if r.get("error") or r.get("errorCode") or r.get("decodeError")],
    "sourceReadPages": len([r for r in results if r.get("path") and "startLine" in r]),
    "rows": rows, "finalDeliveryEvents": [e for e in events if e.get("event") in
        ["bounded_audit_final", "read_only_research_recovery_final", "partial_report_delivery", "terminal_delivery_exhausted"]],
    "answers": answers,
    "executionTransitions": [e for e in events if e.get("event") == "execution_transitions"],
    "limits": ["No reset transition means live re-entry is unverified; deterministic regression covers that branch.",
               "EOS and unique IDs do not establish task quality or complete data recall."]}
(out / "events.json").write_text(json.dumps(events, ensure_ascii=False, indent=2), encoding="utf8")
(out / "verification.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf8")
print(json.dumps({k: summary[k] for k in ["measurements", "observedRounds", "duplicateMeasurementIds",
    "duplicateObservationIds", "exactBackendMismatches", "verifiedExactBackendPairs", "unknownBackendCalls",
    "compactions", "postCompactionInputs", "sourceReadPages", "toolErrors", "postCompactionCumulativeExactInputTokens", "targetReached"]}, ensure_ascii=True))
print(json.dumps({"lastRows": rows[-2:], "resets": len(reset_transitions)}, ensure_ascii=True))
