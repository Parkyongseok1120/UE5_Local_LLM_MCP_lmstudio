"""Derive tables/metrics from recorded actual GUI calls. Does not control LM Studio."""
import csv
import datetime
import json
from pathlib import Path

root = Path(__file__).parent
data = json.loads((root / "live-gui.json").read_text(encoding="utf8"))
events = json.loads((root / "live-gui-events.json").read_text(encoding="utf8"))
run = json.loads((root / "live-run.json").read_text(encoding="utf8"))
measurements = {e["modelInputId"]: e for e in events if e.get("event") == "context_low_water"}
rows = []
for row in data["rounds"]:
    water = measurements.get(row["modelInputId"], {})
    rows.append({"modelInputId": row["modelInputId"], "roundIndex": row["roundIndex"],
        "beforeExactInput": water.get("beforeExactInputTokens"), "finalExactInput": row["exactInputTokens"],
        "compacted": row["compacted"], "configuredLow": water.get("configuredLowWaterTokens"),
        "effectiveLow": row["low"], "high": row["high"], "mandatoryFloor": row["mandatoryFloor"],
        "generationReserve": water.get("requiredGenerationReserve"), "hardCeiling": water.get("hardInputCeiling"),
        "toolSchemaTokens": water.get("toolSchemaTokens"),
        "backendPromptTokens": (row.get("backendStats") or {}).get("promptTokensCount"),
        "prefillMs": row["prefillMs"], "finishReason": row["finishReason"],
        "postCompactionCumulative": row["postCompactionCumulativeExactInputTokens"]})
if rows:
    with (root / "live-rounds.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=rows[0]); writer.writeheader(); writer.writerows(rows)
compacted = [r for r in rows if r["compacted"]]
crossing = next((r for r in rows if r["postCompactionCumulative"] >= data["target"]), None)
if crossing and not (root / "milestone-380k.json").exists():
    (root / "milestone-380k.json").write_text(json.dumps({
        "observedAt": datetime.datetime.now().astimezone().isoformat(), "firstCrossingRound": crossing,
        "model": run["model"], "contextLength": run["contextLength"], "runtimeRevision": run["installedRevision"],
        "metric": data["metric"], "criterion": "quantity only; correctness and final delivery assessed separately"
    }, indent=2), encoding="utf8")
log = Path(run["logPath"])
new_log = log.read_bytes()[run["logStartBytes"]:] if log.stat().st_size >= run["logStartBytes"] else b"LOG_ROTATED_REVIEW_REQUIRED"
(root / "live-runtime-errors.log").write_bytes(new_log)
summary = {"completedCalls": len(rows), "actualCompactions": len(compacted),
    "postCompactionCumulativeExactInputTokens": data["postCompactionCumulativeExactInputTokens"],
    "allCompactedLowMet": bool(compacted) and all(r["finalExactInput"] <= r["effectiveLow"] for r in compacted),
    "compactedMin": min((r["finalExactInput"] for r in compacted), default=None),
    "compactedMax": max((r["finalExactInput"] for r in compacted), default=None),
    "compactedFirst": compacted[0]["finalExactInput"] if compacted else None,
    "compactedLast": compacted[-1]["finalExactInput"] if compacted else None,
    "allBackendPromptMatchesExact": all(r["backendPromptTokens"] == r["finalExactInput"] for r in rows),
    "continueMessages": run["continueMessages"], "toolErrors": len(data["toolErrors"]),
    "newRuntimeLogBytes": len(new_log),
    "newUnknownChannelWarnings": new_log.count(b"Received channelSend for unknown channel"),
    "semanticAttempts": sum(bool(e.get("modelCalled")) for e in data["semanticSummaryCalls"]),
    "semanticAccepted": sum(e.get("accepted") is True for e in data["semanticSummaryCalls"]),
    "fileComplete": data["fileReadComplete"], "pages": data["pages"], "lastLine": data["lastLine"],
    "finalModelResponseCompleted": data["completed"]}
(root / "live-summary.json").write_text(json.dumps(summary, indent=2), encoding="utf8")
print(json.dumps(summary))
