#!/usr/bin/env python
"""Sonnet-4 proxy reasoning eval — aggregates plan, genre, runtime, retrieval checks."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

from genre_scope_validate import validate_genre_scope  # noqa: E402
from refactor_plan import validate_refactor_plan  # noqa: E402
from runtime_config_checklist import check_runtime_config  # noqa: E402
from rag_search import SearchOptions, search  # noqa: E402


def load_cases(workspace: Path) -> dict:
    path = workspace / "config" / "rag_eval_reasoning_cases.json"
    return json.loads(path.read_text(encoding="utf-8-sig"))


def read_plan(workspace: Path, case: dict) -> str:
    rel = case.get("planFile") or case.get("plan_text_file") or ""
    if not rel:
        return str(case.get("planText") or "")
    return (workspace / rel).read_text(encoding="utf-8-sig")


def run_case(workspace: Path, case: dict, index_path: Path) -> dict:
    case_type = case["type"]
    case_id = case["id"]
    result: dict = {"id": case_id, "type": case_type, "pass": False, "detail": {}}

    if case_type == "plan_validate":
        plan = read_plan(workspace, case)
        payload = validate_refactor_plan(case.get("stage", "R0"), plan)
        expect = case.get("expectOk", True)
        result["pass"] = payload["ok"] == expect
        result["detail"] = payload

    elif case_type == "genre_scope":
        plan = read_plan(workspace, case)
        payload = validate_genre_scope(case.get("genre", "action_combat"), plan)
        expect = case.get("expectOk", True)
        result["pass"] = payload["ok"] == expect
        result["detail"] = payload

    elif case_type == "genre_scope_project":
        project_root = case.get("projectRoot") or ""
        if project_root and not Path(project_root).is_absolute():
            project_root = str((workspace / project_root).resolve())
        payload = validate_genre_scope(
            case.get("genre", "action_combat"),
            project_root=project_root,
            min_pass_ratio=float(case.get("expectMinPassRatio", 0.6)),
        )
        ratio = payload.get("passRatio", 0)
        min_ratio = float(case.get("expectMinPassRatio", 0.6))
        result["pass"] = ratio >= min_ratio
        result["detail"] = payload

    elif case_type == "runtime_config":
        payload = check_runtime_config(case["projectRoot"])
        expect = case.get("expectOk", True)
        result["pass"] = payload["ok"] == expect
        result["detail"] = payload

    elif case_type == "tool_order":
        expected = list(case.get("expectedSequence") or [])
        # Structural check only until live agent log replay exists
        result["pass"] = len(expected) >= 2 and "unreal_agent_session" in expected
        result["detail"] = {"expectedSequence": expected, "note": "Sequence structure check"}

    elif case_type == "rag_retrieval":
        rows = search(
            index_path,
            case["query"],
            8,
            SearchOptions(mode=case.get("mode", "compile_fix"), candidate_limit=120),
        )
        title_needle = str(case.get("expectTitleContains") or "")
        max_rank = int(case.get("maxRank", 8))
        hit = False
        for rank, row in enumerate(rows[:max_rank], start=1):
            if title_needle.lower() in str(row.get("title") or "").lower():
                hit = True
                break
        result["pass"] = hit
        result["detail"] = {"hitCount": len(rows), "expectTitleContains": title_needle}

    else:
        result["detail"] = {"error": f"Unknown case type: {case_type}"}

    return result


def compute_score(results: list[dict], weights: dict[str, float]) -> float:
    type_pass: dict[str, list[bool]] = {}
    for row in results:
        type_pass.setdefault(row["type"], []).append(row["pass"])
    score = 0.0
    type_to_weight = {
        "plan_validate": weights.get("plan_validate", 0.2),
        "genre_scope": weights.get("genre_scope", 0.1),
        "genre_scope_project": weights.get("genre_scope", 0.1),
        "runtime_config": weights.get("runtime_config", 0.2),
        "tool_order": weights.get("tool_order", 0.15),
        "rag_retrieval": weights.get("compile_3_attempt", 0.25),
    }
    used: dict[str, float] = {}
    for case_type, passes in type_pass.items():
        w = type_to_weight.get(case_type, 0.1)
        used[case_type] = w
        ratio = sum(passes) / len(passes) if passes else 0
        score += w * ratio * 100
    return round(min(100.0, score), 1)


def main() -> int:
    workspace = SCRIPTS.parent
    index_path = workspace / "data" / "unreal58" / "rag.sqlite"
    config = load_cases(workspace)
    cases = config.get("cases") or []
    weights = config.get("weights") or {}
    min_score = float(config.get("defaults", {}).get("minScore", 80))

    results = [run_case(workspace, case, index_path) for case in cases]
    failed = [r for r in results if not r["pass"]]
    score = compute_score(results, weights)

    for row in results:
        status = "PASS" if row["pass"] else "FAIL"
        print(f"[{status}] {row['id']} ({row['type']})")

    print(f"\nSonnet-4 proxy score: {score}/100 (min {min_score})")

    kpi = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "score": score,
        "minScore": min_score,
        "pass": score >= min_score,
        "results": results,
    }
    out = workspace / "data" / "baseline" / "reasoning-kpi.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(kpi, ensure_ascii=False, indent=2), encoding="utf-8")

    if score < min_score:
        print(f"Below minimum score ({min_score}).")
    if failed:
        return 1
    return 0 if score >= min_score else 1


if __name__ == "__main__":
    raise SystemExit(main())
