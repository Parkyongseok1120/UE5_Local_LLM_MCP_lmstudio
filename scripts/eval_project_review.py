#!/usr/bin/env python
"""Static fixture eval for grounded project review (Phase 9)."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))


def load_cases(workspace: Path) -> dict:
    path = workspace / "config" / "rag_eval_project_review_cases.json"
    return json.loads(path.read_text(encoding="utf-8-sig"))


def regex_match(pattern: str, text: str) -> bool:
    return re.search(pattern, text, flags=re.IGNORECASE | re.MULTILINE | re.DOTALL) is not None


def score_case(case: dict, answer: str) -> dict:
    case_id = case["id"]
    must_detect = [str(p) for p in case.get("mustDetect") or []]
    must_not = [str(p) for p in case.get("mustNotSuggest") or []]
    forbidden_claims = [str(p) for p in case.get("forbiddenClaims") or []]

    detect_hits = [p for p in must_detect if regex_match(p, answer)]
    detect_miss = [p for p in must_detect if p not in detect_hits]
    forbidden_hits = [p for p in must_not if regex_match(p, answer)]
    claim_failures = [p for p in forbidden_claims if regex_match(p, answer)]

    recall = len(detect_hits) / len(must_detect) if must_detect else 1.0
    pass_recall = recall >= float(case.get("minRecall", 0.8))
    claim_validate_ok = len(forbidden_hits) == 0 and len(claim_failures) == 0
    claim_detail = {
        "forbiddenPatternHits": forbidden_hits,
        "forbiddenClaimHits": claim_failures,
    }

    citation_ok = bool(re.search(r"[A-Za-z0-9_./\\-]+:\d+", answer))
    if case.get("requireCitation"):
        pass_citation = citation_ok
    else:
        pass_citation = True

    passed = pass_recall and claim_validate_ok and pass_citation

    return {
        "id": case_id,
        "pass": passed,
        "recall": round(recall, 3),
        "detectHits": detect_hits,
        "detectMiss": detect_miss,
        "forbiddenHits": forbidden_hits,
        "claimFailures": claim_failures,
        "hasCitation": citation_ok,
        "claimValidate": claim_detail,
    }


def fixture_answer(case: dict) -> str:
    """Build a passing static fixture answer from case metadata."""
    lines = [f"Review findings for {case['id']}:"]
    for pattern in case.get("mustDetect") or []:
        lines.append(f"- Existing: {pattern} (Source/Sample/Example.cpp:42)")
    for snippet in case.get("snippets") or []:
        path = snippet.get("path", "Source/Sample/Example.h")
        content = str(snippet.get("content") or "")
        for line in content.splitlines():
            if "class" in line or "UCLASS" in line:
                lines.append(f"- {path}: cited {line.strip()[:80]}")
    lines.append("DoNotDuplicate: reuse existing Subsystem/DataAsset instead of new classes.")
    return "\n".join(lines)


def run_live(workspace: Path, cases: list[dict], model: str, url: str) -> list[dict]:
    from urllib.request import Request, urlopen

    results = []
    for case in cases:
        prompt_parts = [str(case.get("prompt") or "Review this Unreal project architecture.")]
        for snippet in case.get("snippets") or []:
            prompt_parts.append(
                f"\nFile: {snippet.get('path')}\n```cpp\n{snippet.get('content')}\n```"
            )
        body = {
            "model": model,
            "messages": [{"role": "user", "content": "\n".join(prompt_parts)}],
            "temperature": 0.3,
            "max_tokens": 2048,
        }
        req = Request(
            f"{url.rstrip('/')}/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(req, timeout=120) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            answer = str(
                (payload.get("choices") or [{}])[0]
                .get("message", {})
                .get("content", "")
            )
            results.append(score_case(case, answer))
        except Exception as exc:
            results.append({"id": case["id"], "pass": False, "error": str(exc)})
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="Eval grounded project review fixtures.")
    parser.add_argument("--live", action="store_true", help="Call LM Studio (Tier B)")
    parser.add_argument("--model", default="")
    parser.add_argument("--url", default="http://localhost:1234/v1")
    args = parser.parse_args()

    workspace = SCRIPTS.parent
    config = load_cases(workspace)
    cases = list(config.get("cases") or [])
    min_recall = float(config.get("defaults", {}).get("minRecall", 0.8))

    if args.live:
        model = args.model or "local-model"
        results = run_live(workspace, cases, model, args.url)
    else:
        results = []
        for case in cases:
            answer = fixture_answer(case)
            row = score_case(case, answer)
            # Bad fixture answer for forbidden pattern check
            bad_answer = case.get("badAnswerFixture") or ""
            if bad_answer:
                bad_row = score_case(case, bad_answer)
                row["badFixtureWouldFail"] = not bad_row["pass"]
            results.append(row)

    failed = [r for r in results if not r.get("pass")]
    passed = len(results) - len(failed)
    aggregate_recall = (
        sum(r.get("recall", 0) for r in results) / len(results) if results else 0
    )

    for row in results:
        status = "PASS" if row.get("pass") else "FAIL"
        print(f"[{status}] {row['id']} recall={row.get('recall', 0):.0%}")

    print(f"\nSummary: {passed}/{len(results)} passed, aggregate recall {aggregate_recall:.0%}")

    kpi = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "mode": "live" if args.live else "static",
        "passCount": passed,
        "total": len(results),
        "aggregateRecall": round(aggregate_recall, 3),
        "minRecall": min_recall,
        "pass": len(failed) == 0 and aggregate_recall >= min_recall,
        "results": results,
    }
    out = workspace / "data" / "baseline" / "project-review-kpi.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(kpi, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {out}")

    if failed or aggregate_recall < min_recall:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
