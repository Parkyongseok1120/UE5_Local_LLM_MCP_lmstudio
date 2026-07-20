#!/usr/bin/env python3
"""Evaluate project-neutral evidence-first audit answers against public holdouts."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASES = ROOT / "config" / "evidence_first_audit_cases.json"


def load_cases(path: Path = DEFAULT_CASES) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    cases = payload.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("cases must be a non-empty array")
    return cases


def evaluate_answer(case: dict[str, Any], answer: str) -> dict[str, Any]:
    def missing(patterns: list[str]) -> list[str]:
        return [pattern for pattern in patterns if re.search(pattern, answer, re.I | re.S) is None]

    def present(patterns: list[str]) -> list[str]:
        return [pattern for pattern in patterns if re.search(pattern, answer, re.I | re.S) is not None]

    missing_findings = missing(case.get("requiredFindings", []))
    missing_output = missing(case.get("requiredOutputPatterns", []))
    forbidden_hits = present(case.get("forbiddenClaims", []))
    passed = not missing_findings and not missing_output and not forbidden_hits
    return {
        "id": case["id"],
        "language": case["language"],
        "passed": passed,
        "missingFindings": missing_findings,
        "missingOutputPatterns": missing_output,
        "forbiddenHits": forbidden_hits,
    }


def evaluate_answers(cases: list[dict[str, Any]], answers: dict[str, str]) -> dict[str, Any]:
    results = [evaluate_answer(case, str(answers.get(case["id"], ""))) for case in cases]
    return {
        "ok": all(result["passed"] for result in results),
        "passed": sum(result["passed"] for result in results),
        "total": len(results),
        "results": results,
    }


def evaluate_fixtures(cases: list[dict[str, Any]]) -> dict[str, Any]:
    good = evaluate_answers(cases, {case["id"]: case["goodAnswerFixture"] for case in cases})
    bad_results = [evaluate_answer(case, case["badAnswerFixture"]) for case in cases]
    rejected_bad = sum(not result["passed"] for result in bad_results)
    return {
        "ok": good["ok"] and rejected_bad == len(cases),
        "goodFixtures": good,
        "badFixturesRejected": rejected_bad,
        "badFixtureTotal": len(cases),
        "badFixtureResults": bad_results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument(
        "--answers",
        type=Path,
        help="Optional JSON object mapping case id to an independently produced answer.",
    )
    args = parser.parse_args()
    try:
        cases = load_cases(args.cases)
        if args.answers:
            answers = json.loads(args.answers.read_text(encoding="utf-8-sig"))
            if not isinstance(answers, dict):
                raise ValueError("answers must be a JSON object keyed by case id")
            result = evaluate_answers(cases, answers)
        else:
            result = evaluate_fixtures(cases)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
