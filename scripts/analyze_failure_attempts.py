#!/usr/bin/env python
"""Classify failed wrapper attempts from saved eval artifacts."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

SCRIPTS = Path(__file__).resolve().parent
ROOT = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))

from eval_pass_at_k import changed_files_from_diff, infer_eval_tier  # noqa: E402


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def load_cases(config_path: Path | None) -> dict[str, dict[str, Any]]:
    if not config_path:
        return {}
    path = config_path if config_path.is_absolute() else ROOT / config_path
    if not path.is_file():
        return {}
    data = load_json(path)
    return {str(case.get("id") or ""): case for case in data.get("cases") or []}


def normalize_path(value: str) -> str:
    return str(value or "").strip().replace("\\", "/")


def changed_files_from_model_response(path: Path) -> list[str]:
    if not path.is_file():
        return []
    try:
        data = load_json(path)
    except (OSError, json.JSONDecodeError):
        return []
    changed: list[str] = []
    seen: set[str] = set()
    for collection in ("files", "patches"):
        for row in data.get(collection) or []:
            rel = normalize_path(str(row.get("path") or ""))
            if rel and rel not in seen:
                changed.append(rel)
                seen.add(rel)
    return changed


def changed_files_from_attempt(attempt_dir: Path) -> list[str]:
    changed = changed_files_from_model_response(attempt_dir / "model_response.json")
    if changed:
        return changed
    for name in ("diff.patch", "final_diff.patch", "attempt_diff.patch"):
        path = attempt_dir / name
        if path.is_file():
            try:
                return changed_files_from_diff(path.read_text(encoding="utf-8-sig", errors="replace"))
            except OSError:
                return []
    return []


def read_attempt_text(attempt_dir: Path) -> str:
    chunks: list[str] = []
    for name in ("model_response.txt", "static_validation.txt", "retry_state.json"):
        path = attempt_dir / name
        if path.is_file():
            try:
                chunks.append(path.read_text(encoding="utf-8-sig", errors="replace"))
            except OSError:
                pass
    return "\n".join(chunks)


def expected_target_words(case: dict[str, Any]) -> str:
    return " ".join(str(item) for item in case.get("expectedPatchTargets") or []).lower()


def classify_attempt(attempt_dir: Path, case: dict[str, Any]) -> dict[str, Any]:
    changed = changed_files_from_attempt(attempt_dir)
    changed_lower = [path.lower() for path in changed]
    text = read_attempt_text(attempt_dir)
    text_lower = text.lower()
    tier = infer_eval_tier(case)
    expected = expected_target_words(case)

    has_header = any(path.endswith((".h", ".hpp")) for path in changed_lower)
    has_cpp = any(path.endswith((".cpp", ".cc", ".cxx")) for path in changed_lower)
    expects_pair = tier == "multifile_refactor" or "cpp/header" in expected or "header" in expected and "cpp" in expected
    expected_targets = [str(item).lower() for item in case.get("expectedPatchTargets") or []]
    changed_build_cs = any(path.endswith(".build.cs") for path in changed_lower)

    patterns: list[str] = []
    if "oldtext" in text_lower and ("not found" in text_lower or "expectedoccurrences" in text_lower):
        patterns.append("patch_application_failed")
    if "validationrejected" in text_lower or "static validation" in text_lower and "error" in text_lower:
        patterns.append("validation_rejected")
    if expects_pair and has_cpp and not has_header:
        patterns.append("cpp_only_no_header")
        patterns.append("partial_coverage")
    if expects_pair and has_header and not has_cpp:
        patterns.append("header_only_no_cpp")
        patterns.append("partial_coverage")
    if expected_targets and changed and not all(_target_is_covered(target, changed_lower) for target in expected_targets):
        patterns.append("partial_coverage")
    if changed_build_cs and "build.cs-first" in " ".join(str(item).lower() for item in case.get("forbiddenPatchTargets") or []):
        patterns.append("wrong_direction")
    if not changed:
        patterns.append("no_detected_edit")
    if not patterns:
        patterns.append("unclassified")
    patterns = list(dict.fromkeys(patterns))

    return {
        "attempt": attempt_dir.name,
        "changedFiles": changed,
        "failurePatterns": patterns,
    }


def failed_attempt_names(run_dir: Path) -> set[str] | None:
    state_path = run_dir / "retry_state.json"
    if not state_path.is_file():
        return None
    try:
        data = load_json(state_path)
    except (OSError, json.JSONDecodeError):
        return None
    names: set[str] = set()
    for row in data.get("attempts") or []:
        attempt = row.get("attempt")
        if attempt is None:
            continue
        if row.get("passed") is True:
            continue
        names.add(f"attempt_{attempt}")
    return names


def passed_without_failed_attempts(run_dir: Path) -> bool:
    if (run_dir / "retry_state.json").is_file():
        return False
    final_answer = run_dir / "final_answer.md"
    if not final_answer.is_file():
        return False
    try:
        text = final_answer.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return False
    return "Status: BUILD_OK" in text or "Status: NO_FILE_CHANGES" in text


def _target_is_covered(target: str, changed_lower: list[str]) -> bool:
    if "build.cs" in target:
        return any(path.endswith(".build.cs") for path in changed_lower)
    if "cpp/header" in target or "source/header" in target:
        return any(path.endswith((".h", ".hpp", ".cpp", ".cc", ".cxx")) for path in changed_lower)
    if "header" in target:
        return any(path.endswith((".h", ".hpp")) for path in changed_lower)
    if "cpp" in target or "source" in target:
        return any(path.endswith((".cpp", ".cc", ".cxx")) for path in changed_lower)
    if "failing file" in target or "module boundary" in target:
        return bool(changed_lower)
    return True


def analyze_artifacts(artifact_dir: Path, cases: dict[str, dict[str, Any]]) -> dict[str, Any]:
    artifact_dir = artifact_dir if artifact_dir.is_absolute() else ROOT / artifact_dir
    case_reports: list[dict[str, Any]] = []
    pattern_counts: dict[str, int] = {}
    if not artifact_dir.is_dir():
        return {"artifactDir": str(artifact_dir), "caseCount": 0, "patternCounts": {}, "cases": []}

    for case_dir in sorted(path for path in artifact_dir.iterdir() if path.is_dir()):
        case_id = case_dir.name
        run_dir = case_dir / "wrapper_run"
        if not run_dir.is_dir():
            continue
        case = cases.get(case_id, {"id": case_id})
        attempts: list[dict[str, Any]] = []
        failed_names = failed_attempt_names(run_dir)
        if failed_names is None and passed_without_failed_attempts(run_dir):
            continue
        for attempt_dir in sorted(run_dir.glob("attempt_*")):
            if not attempt_dir.is_dir():
                continue
            if failed_names is not None and attempt_dir.name not in failed_names:
                continue
            report = classify_attempt(attempt_dir, case)
            attempts.append(report)
            for pattern in report["failurePatterns"]:
                pattern_counts[pattern] = pattern_counts.get(pattern, 0) + 1
        if attempts:
            case_reports.append(
                {
                    "id": case_id,
                    "evalTier": infer_eval_tier(case),
                    "attempts": attempts,
                }
            )

    return {
        "artifactDir": str(artifact_dir),
        "caseCount": len(case_reports),
        "patternCounts": dict(sorted(pattern_counts.items())),
        "cases": case_reports,
    }


def write_markdown(report: dict[str, Any], path: Path) -> None:
    lines = [
        "# Failure Attempt Analysis",
        "",
        f"Artifact dir: `{report.get('artifactDir', '')}`",
        f"Cases analyzed: {report.get('caseCount', 0)}",
        "",
        "## Pattern Counts",
        "",
    ]
    counts = report.get("patternCounts") or {}
    if counts:
        for pattern, count in counts.items():
            lines.append(f"- `{pattern}`: {count}")
    else:
        lines.append("- none")
    lines.extend(["", "## Cases", ""])
    for case in report.get("cases") or []:
        lines.append(f"### {case.get('id')}")
        lines.append(f"- tier: `{case.get('evalTier')}`")
        for attempt in case.get("attempts") or []:
            patterns = ", ".join(f"`{item}`" for item in attempt.get("failurePatterns") or [])
            files = ", ".join(f"`{item}`" for item in attempt.get("changedFiles") or []) or "none"
            lines.append(f"- {attempt.get('attempt')}: {patterns}; files: {files}")
        lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def attach_to_kpi(kpi_path: Path, report: dict[str, Any]) -> None:
    path = kpi_path if kpi_path.is_absolute() else ROOT / kpi_path
    data = load_json(path)
    data["failureAnalysis"] = report
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Analyze failed wrapper attempts from eval artifacts.")
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--out-json", type=Path, default=None)
    parser.add_argument("--out-md", type=Path, default=None)
    parser.add_argument("--attach-failure-analysis", type=Path, default=None,
                        help="Attach the analysis to an existing KPI JSON file in place.")
    args = parser.parse_args()

    report = analyze_artifacts(args.artifact_dir, load_cases(args.config))
    if args.out_json:
        out = args.out_json if args.out_json.is_absolute() else ROOT / args.out_json
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.out_md:
        out_md = args.out_md if args.out_md.is_absolute() else ROOT / args.out_md
        write_markdown(report, out_md)
    if args.attach_failure_analysis:
        attach_to_kpi(args.attach_failure_analysis, report)
    print(json.dumps({"caseCount": report["caseCount"], "patternCounts": report["patternCounts"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
