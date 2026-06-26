#!/usr/bin/env python
"""Score model answers against Unreal C++ review E2E cases."""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class FindingScore:
    finding_id: str
    points: float
    earned: float
    diagnosis_ok: bool
    fix_ok: bool


@dataclass
class Penalty:
    penalty_id: str
    points: float
    pattern: str


@dataclass
class CaseScore:
    case_id: str
    possible: float
    earned: float
    penalties: list[Penalty]
    findings: list[FindingScore]
    missing_answer: bool = False

    @property
    def ratio(self) -> float:
        if self.possible <= 0:
            return 1.0
        return max(0.0, self.earned) / self.possible


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"invalid JSON: {path}:{exc.lineno}:{exc.colno} {exc.msg}") from exc


def iter_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise SystemExit(f"invalid JSONL: {path}:{line_number}:{exc.colno} {exc.msg}") from exc
            if not isinstance(value, dict):
                raise SystemExit(f"invalid JSONL: {path}:{line_number} expected object")
            rows.append(value)
    return rows


def load_answers_file(path: Path) -> dict[str, str]:
    if not path.exists():
        raise SystemExit(f"answers file does not exist: {path}")

    if path.suffix.lower() == ".jsonl":
        rows = iter_jsonl(path)
    else:
        value = load_json(path)
        if isinstance(value, dict) and isinstance(value.get("answers"), list):
            rows = list(value["answers"])
        elif isinstance(value, list):
            rows = value
        elif isinstance(value, dict):
            answers: dict[str, str] = {}
            for key, item in value.items():
                if isinstance(item, str):
                    answers[str(key)] = item
                elif isinstance(item, dict):
                    text = item.get("answer") or item.get("text") or item.get("content")
                    if text is not None:
                        answers[str(key)] = str(text)
            return answers
        else:
            raise SystemExit(f"answers file must be JSON object/list or JSONL: {path}")

    answers = {}
    for row in rows:
        case_id = row.get("case_id") or row.get("id")
        answer = row.get("answer") or row.get("text") or row.get("content")
        if not case_id or answer is None:
            raise SystemExit(f"answer rows must contain case_id and answer/text/content: {path}")
        answers[str(case_id)] = str(answer)
    return answers


def load_answers_dir(path: Path) -> dict[str, str]:
    if not path.exists():
        raise SystemExit(f"answers dir does not exist: {path}")
    answers: dict[str, str] = {}
    for file_path in sorted(path.iterdir()):
        if not file_path.is_file() or file_path.suffix.lower() not in {".md", ".txt"}:
            continue
        answers[file_path.stem] = file_path.read_text(encoding="utf-8", errors="replace")
    return answers


def regex_match(pattern: str, text: str) -> bool:
    return re.search(pattern, text, flags=re.IGNORECASE | re.MULTILINE | re.DOTALL) is not None


def all_patterns_match(patterns: list[str], text: str) -> bool:
    return all(regex_match(pattern, text) for pattern in patterns)


def any_pattern_matches(patterns: list[str], text: str) -> tuple[bool, str]:
    for pattern in patterns:
        if regex_match(pattern, text):
            return True, pattern
    return False, ""


def score_finding(finding: dict[str, Any], answer: str) -> FindingScore:
    points = float(finding.get("points") or 1.0)
    diagnosis_patterns = [str(pattern) for pattern in finding.get("diagnosis") or []]
    fix_patterns = [str(pattern) for pattern in finding.get("fix") or []]

    diagnosis_ok = all_patterns_match(diagnosis_patterns, answer) if diagnosis_patterns else False
    fix_ok = True
    if fix_patterns:
        fix_ok, _ = any_pattern_matches(fix_patterns, answer)

    if diagnosis_ok and fix_ok:
        earned = points
    elif diagnosis_ok:
        earned = points * 0.5
    else:
        earned = 0.0

    return FindingScore(
        finding_id=str(finding.get("id") or "<unnamed>"),
        points=points,
        earned=earned,
        diagnosis_ok=diagnosis_ok,
        fix_ok=fix_ok,
    )


def case_penalties(case: dict[str, Any], defaults: dict[str, Any], answer: str) -> list[Penalty]:
    penalties: list[Penalty] = []
    for forbidden in case.get("forbidden_patterns") or []:
        matched, pattern = any_pattern_matches([str(item) for item in forbidden.get("patterns") or []], answer)
        if matched:
            penalties.append(
                Penalty(
                    penalty_id=str(forbidden.get("id") or "forbidden_pattern"),
                    points=float(forbidden.get("penalty") or 1.0),
                    pattern=pattern,
                )
            )

    compile_claims = [str(item) for item in defaults.get("compile_ready_claim_patterns") or []]
    verification = [str(item) for item in defaults.get("verification_patterns") or []]
    claims_ready, ready_pattern = any_pattern_matches(compile_claims, answer)
    verified, _ = any_pattern_matches(verification, answer)
    if claims_ready and not verified:
        penalties.append(
            Penalty(
                penalty_id="unverified_compile_ready_claim",
                points=float(defaults.get("compile_ready_penalty") or 1.0),
                pattern=ready_pattern,
            )
        )
    return penalties


def score_case(case: dict[str, Any], defaults: dict[str, Any], answer: str | None) -> CaseScore:
    case_id = str(case.get("id") or "<unnamed>")
    findings = [score_finding(item, answer or "") for item in case.get("expected_findings") or []]
    possible = sum(item.points for item in findings)
    if answer is None:
        return CaseScore(case_id, possible, 0.0, [], findings, missing_answer=True)

    penalties = case_penalties(case, defaults, answer)
    earned = sum(item.earned for item in findings) - sum(item.points for item in penalties)
    earned = max(0.0, earned)
    return CaseScore(case_id, possible, earned, penalties, findings)


def print_prompts(case_set: dict[str, Any], case_ids: set[str]) -> None:
    for case in case_set.get("cases") or []:
        case_id = str(case.get("id") or "")
        if case_ids and case_id not in case_ids:
            continue
        prompt_parts = [str(case.get("prompt") or "Review this Unreal C++ code.")]
        for file_item in case.get("files") or []:
            prompt_parts.append(f"\nFile: {file_item.get('path')}\n```cpp\n{file_item.get('content')}\n```")
        print(json.dumps({"case_id": case_id, "prompt": "\n".join(prompt_parts)}, ensure_ascii=False))


def format_case_score(score: CaseScore, threshold: float) -> str:
    status = "FAIL" if score.missing_answer or score.ratio < threshold else "PASS"
    lines = [
        f"[{status}] {score.case_id}: {score.earned:.2f}/{score.possible:.2f} ({score.ratio * 100:.1f}%)"
    ]
    if score.missing_answer:
        lines.append("  missing answer")
    for finding in score.findings:
        if finding.earned >= finding.points:
            marker = "ok"
        elif finding.earned > 0:
            marker = "partial"
        else:
            marker = "miss"
        lines.append(
            "  "
            + f"{marker}: {finding.finding_id} "
            + f"{finding.earned:.2f}/{finding.points:.2f} "
            + f"diagnosis={finding.diagnosis_ok} fix={finding.fix_ok}"
        )
    for penalty in score.penalties:
        lines.append(f"  penalty: -{penalty.points:.2f} {penalty.penalty_id} via /{penalty.pattern}/")
    return "\n".join(lines)


def main(args: argparse.Namespace) -> int:
    case_set = load_json(Path(args.case_set))
    cases = list(case_set.get("cases") or [])
    if not cases:
        print("case set has no cases", file=sys.stderr)
        return 2

    case_ids = set(args.case_id or [])
    if args.print_prompts:
        print_prompts(case_set, case_ids)
        return 0

    answers: dict[str, str] = {}
    if args.answers:
        answers.update(load_answers_file(Path(args.answers)))
    if args.answers_dir:
        answers.update(load_answers_dir(Path(args.answers_dir)))
    if not answers:
        print("pass --answers, --answers-dir, or --print-prompts", file=sys.stderr)
        return 2

    defaults = dict(case_set.get("defaults") or {})
    threshold = float(args.threshold)
    scores: list[CaseScore] = []
    for case in cases:
        case_id = str(case.get("id") or "")
        if case_ids and case_id not in case_ids:
            continue
        scores.append(score_case(case, defaults, answers.get(case_id)))

    if not scores:
        print("no matching cases", file=sys.stderr)
        return 2

    failed = 0
    total_possible = 0.0
    total_earned = 0.0
    for score in scores:
        print(format_case_score(score, threshold))
        if args.verbose:
            print()
        total_possible += score.possible
        total_earned += score.earned
        if score.missing_answer or score.ratio < threshold:
            failed += 1

    ratio = total_earned / total_possible if total_possible else 1.0
    print(
        f"\nsummary: {len(scores) - failed} passed, {failed} failed, "
        f"{len(scores)} total, aggregate {total_earned:.2f}/{total_possible:.2f} ({ratio * 100:.1f}%)"
    )
    return 1 if failed else 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate Unreal C++ review answers.")
    parser.add_argument("--case-set", default="config/unreal_review_eval_cases.json")
    parser.add_argument("--answers", default="", help="JSON/JSONL file with case_id and answer fields.")
    parser.add_argument("--answers-dir", default="", help="Directory with <case_id>.md or <case_id>.txt answers.")
    parser.add_argument("--case-id", action="append", default=[])
    parser.add_argument("--threshold", type=float, default=0.75)
    parser.add_argument("--print-prompts", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(main(parse_args()))
