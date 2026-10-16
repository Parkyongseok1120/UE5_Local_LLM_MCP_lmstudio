"""Verify captured read-only GUI results against source bytes, without prompting the model."""
import hashlib
import json
import pathlib
import re

directory = pathlib.Path(__file__).parent
data = json.loads((directory / "live-gui.json").read_text(encoding="utf8"))
root = pathlib.Path(r"REDACTED_HOME\Documents\Git\Human-Bartender\HumanBartender")
checks = []
for read in data["reads"]:
    if read.get("path") not in ["Packages/manifest.json", "Packages/packages-lock.json"]:
        continue
    raw = (root / read["path"]).read_bytes()
    lines = re.split(r"\r\n|\n|\r", raw.decode("utf-8-sig"))
    start, end = read.get("startLine"), read.get("endLine")
    if not isinstance(start, int) or not isinstance(end, int):
        continue
    checks.append({"path": read["path"], "start": start, "end": end,
        "hashMatchesSource": read.get("hash") == hashlib.sha256(raw).hexdigest(),
        "textMatchesSource": read.get("text") == "\n".join(lines[start-1:end]),
        "countMatchesRange": read.get("returnedLineCount") == end-start+1,
        "totalLinesMatchesSource": read.get("totalLines") == len(lines),
        "continuationMatches": read.get("nextStartLine") == (end+1 if end < len(lines) else None)})
manifest = json.loads((root / "Packages/manifest.json").read_text(encoding="utf8"))
packages = list(json.loads((root / "Packages/packages-lock.json").read_text(encoding="utf8"))["dependencies"].items())
oracle = {"bridge": manifest["dependencies"]["com.evidencefirst.unity-bridge"],
    "firstThree": [{"name": k, "version": v["version"]} for k, v in packages[:3]],
    "last": {"name": packages[-1][0], "version": packages[-1][1]["version"]}}
compactions = [r for r in data["rounds"] if r["compacted"]]
archives = [r for r in data["reads"] if r.get("kind") == "historical_evidence_range"]
archive_checks = [{"ok": r.get("ok"), "sourceRange": r.get("sourceRange"),
    "sourceVersionMatchesObservedFile": any(p.get("hash") == r.get("sourceVersion") for p in data["reads"]),
    "returnedRange": r.get("returnedRange"), "coverageState": r.get("coverageState"),
    "rangeLengthMatchesContent": (r.get("returnedRange", [0, 0])[1] - r.get("returnedRange", [0, 0])[0]
        == len(r.get("content", "").encode("utf-16-le")) // 2),
    "historicalReadOnly": r.get("currentFile") is False and r.get("grantsMutation") is False,
    "sourcePageHasMore": r.get("sourcePageHasMore"), "fullRawProvided": r.get("fullRawProvided")}
    for r in archives]
result = {"targetReached": data["postCompactionCumulativeExactInputTokens"] >= data["target"],
    "postCompactionCumulativeExactInputTokens": data["postCompactionCumulativeExactInputTokens"],
    "verifiedReadResults": len(checks), "allReturnedPagesMatchSource": bool(checks) and all(
        all(v for k, v in c.items() if k not in ["path", "start", "end"]) for c in checks),
    "fileCoverageComplete": data["fileReadComplete"],
    "contiguousCoverage": data["pageCoverageContiguous"], "checks": checks,
    "compactedExactInputs": [r["exactInputTokens"] for r in compactions],
    "allCompactedInputsMeetEffectiveLow": bool(compactions) and all(r["lowMet"] for r in compactions),
    "backendPromptMatchesExactInput": all(r["backendStats"].get("promptTokensCount") == r["exactInputTokens"] for r in data["rounds"]),
    "archiveChecks": archive_checks,
    "semanticSummaryAttempts": sum(bool(e.get("modelCalled")) for e in data["semanticSummaryCalls"]),
    "acceptedSemanticSummaries": sum(e.get("accepted") is True for e in data["semanticSummaryCalls"]),
    "oracleForIndependentReviewOnly": oracle,
    "limits": ["Source byte/range integrity does not prove that the model retained all facts.",
        "Meeting a rising effective Low-water does not prove a stable compaction baseline.",
        "Final report and archive rehydration must be assessed separately."]}
(directory / "live-verification.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf8")
print(json.dumps({k: v for k, v in result.items() if k not in ["checks", "oracleForIndependentReviewOnly", "limits"]}))
