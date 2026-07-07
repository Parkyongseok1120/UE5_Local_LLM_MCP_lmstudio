#!/usr/bin/env python
"""Pass@K compile-fix eval - golden dry-run (Tier A) or wrapper+LM Studio (Tier B)."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
ROOT = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))

from eval_e2e_compile import DEFAULT_UBT, run_ubt  # noqa: E402
from preflight_lmstudio import check_lmstudio  # noqa: E402
from ubt_utils import split_ubt_target_spec  # noqa: E402


def count_wrapper_attempts(run_dir: Path) -> int:
    if not run_dir.is_dir():
        return 0
    return sum(1 for path in run_dir.iterdir() if path.is_dir() and path.name.startswith("attempt_"))


def read_retry_state_metrics(run_dir: Path) -> dict:
    path = run_dir / "retry_state.json"
    return read_retry_state_metrics_file(path)


def read_retry_state_metrics_file(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    attempts = data.get("attempts") if isinstance(data, dict) else []
    validation_rejected_attempts = sum(1 for row in attempts or [] if row.get("validationRejected"))
    pre_apply_noop_attempts = sum(
        1
        for row in attempts or []
        if row.get("validationRejected") and (row.get("noOpEdit") or row.get("noEffectiveEdit"))
    )
    return {
        "sameErrorRepeated": bool(data.get("sameErrorRepeated")),
        "noOpEdit": bool(data.get("noOpEdit")),
        "validationRejected": bool(data.get("validationRejected")) or validation_rejected_attempts > 0,
        "preApplyNoOp": pre_apply_noop_attempts > 0,
        "sameErrorRepeatedAttempts": sum(1 for row in attempts or [] if row.get("sameErrorRepeated")),
        "noOpEditAttempts": sum(1 for row in attempts or [] if row.get("noOpEdit")),
        "validationRejectedAttempts": validation_rejected_attempts,
        "preApplyNoOpAttempts": pre_apply_noop_attempts,
    }


def normalize_patch_path(path: str) -> str:
    value = str(path or "").strip().replace("\\", "/")
    if value.startswith(("a/", "b/")):
        value = value[2:]
    return value


def changed_files_from_diff(diff_text: str) -> list[str]:
    changed: list[str] = []
    seen: set[str] = set()
    for raw_line in (diff_text or "").splitlines():
        if not raw_line.startswith(("--- ", "+++ ")):
            continue
        value = raw_line[4:].split("\t", 1)[0].strip()
        if value == "/dev/null":
            continue
        path = normalize_patch_path(value)
        if not path or path.startswith("wrapper_run/"):
            continue
        if path not in seen:
            changed.append(path)
            seen.add(path)
    return changed


def changed_files_from_run_dir(run_dir: Path) -> list[str]:
    diff_path = run_dir / "final_diff.patch"
    if not diff_path.is_file():
        return []
    try:
        return changed_files_from_diff(diff_path.read_text(encoding="utf-8-sig", errors="replace"))
    except OSError:
        return []


def _source_file(path: str) -> bool:
    return path.startswith("Source/") and path.endswith((".h", ".hpp", ".cpp", ".cc", ".cxx", ".cs"))


def _matches_target_descriptor(path: str, descriptor: str) -> bool:
    lowered = str(descriptor or "").lower()
    path_lower = path.lower()
    if "build.cs" in lowered:
        return path_lower.endswith(".build.cs")
    if "cpp/header" in lowered or "source/header" in lowered or "cpp file" in lowered:
        return _source_file(path) and not path_lower.endswith(".build.cs")
    if "header" in lowered:
        return path_lower.endswith((".h", ".hpp"))
    if "cpp" in lowered or "source" in lowered:
        return path_lower.endswith((".cpp", ".cc", ".cxx"))
    if "failing file" in lowered or "module boundary" in lowered:
        return _source_file(path)
    return False


def _case_expects_build_cs(case: dict) -> bool:
    text = " ".join(str(item) for item in case.get("expectedPatchTargets") or []).lower()
    category = str(case.get("category") or "").lower()
    mode = str(case.get("mode") or "").lower()
    return "build.cs" in text or bool(case.get("expectedModules")) or "dependency issue" in category or mode == "module_fix"


def infer_eval_tier(case_or_result: dict) -> str:
    tier = str(case_or_result.get("evalTier") or "").strip()
    if tier:
        return tier
    category = str(case_or_result.get("category") or "").lower()
    mode = str(case_or_result.get("mode") or "").lower()
    case_id = str(case_or_result.get("id") or "").lower()
    if "multifile" in mode or "multi-file" in category or "multifile" in case_id:
        return "multifile_refactor"
    if mode == "module_fix" or "dependency issue" in category or case_or_result.get("expectedModules"):
        return "module_fix"
    if mode == "reflection_fix" or "uht" in category or "blueprint" in category:
        return "uht_reflection"
    if "editor-only" in category:
        return "editor_runtime_boundary"
    return "single_file_compile_fix"


def _forbidden_target_hits(case: dict, changed_files: list[str], diff_text: str = "") -> list[str]:
    hits: list[str] = []
    changed_build_cs = any(path.lower().endswith(".build.cs") for path in changed_files)
    expects_build_cs = _case_expects_build_cs(case)
    for target in case.get("forbiddenPatchTargets") or []:
        lowered = str(target or "").lower()
        hit = False
        if "build.cs-first" in lowered and changed_build_cs and not expects_build_cs:
            hit = True
        elif "adding unrealed" in lowered and changed_build_cs and "UnrealEd" in diff_text:
            hit = True
        elif "build.cs" in lowered and changed_build_cs:
            hit = True
        elif any(_matches_target_descriptor(path, lowered) for path in changed_files):
            hit = True
        if hit:
            hits.append(str(target))
    return hits


def patch_target_metrics(case: dict, run_dir: Path) -> dict:
    changed_files = changed_files_from_run_dir(run_dir)
    diff_text = ""
    diff_path = run_dir / "final_diff.patch"
    if diff_path.is_file():
        try:
            diff_text = diff_path.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            diff_text = ""

    expected = [str(item) for item in case.get("expectedPatchTargets") or []]
    expected_matches = [target for target in expected if any(_matches_target_descriptor(path, target) for path in changed_files)]
    unexpected_files: list[str] = []
    if expected:
        for path in changed_files:
            if not any(_matches_target_descriptor(path, target) for target in expected):
                unexpected_files.append(path)

    forbidden_hits = _forbidden_target_hits(case, changed_files, diff_text)
    build_cs_touched = any(path.lower().endswith(".build.cs") for path in changed_files)
    build_cs_false_positive = build_cs_touched and not _case_expects_build_cs(case)
    wrong_file_edit = bool(forbidden_hits) or bool(unexpected_files) or (bool(expected) and not expected_matches and bool(changed_files))

    return {
        "changedSourceFiles": changed_files,
        "expectedPatchTargets": expected,
        "expectedPatchTargetMatches": expected_matches,
        "expectedPatchTargetMatched": (not expected) or bool(expected_matches),
        "unexpectedPatchTargets": unexpected_files,
        "forbiddenPatchTargetHits": forbidden_hits,
        "wrongFileEdit": wrong_file_edit,
        "buildCsTouched": build_cs_touched,
        "buildCsFalsePositive": build_cs_false_positive,
    }


def retry_state_metrics_for_case(case_id: str, fixture_root: Path | None) -> dict:
    if not fixture_root:
        return {}
    candidates = [
        fixture_root / case_id / "retry_state.json",
        fixture_root / f"{case_id}.json",
    ]
    for candidate in candidates:
        metrics = read_retry_state_metrics_file(candidate)
        if metrics:
            return metrics
    return {}


def build_metrics_only_results(
    cases: list[dict],
    retry_state_fixture: Path | None = None,
    artifact_dir: Path | None = None,
) -> list[dict]:
    """Create fast smoke results that exercise KPI aggregation only.

    This mode intentionally does not call UBT or LM Studio and does not prove
    compile-fix success.
    """
    results: list[dict] = []
    for case in cases:
        case_id = str(case.get("id") or "")
        retry_metrics = retry_state_metrics_for_case(case_id, retry_state_fixture)
        patch_metrics: dict = {}
        if artifact_dir:
            run_dir = artifact_dir / case_id / "wrapper_run"
            if not retry_metrics:
                retry_metrics = read_retry_state_metrics(run_dir)
            if run_dir.is_dir():
                patch_metrics = patch_target_metrics(case, run_dir)
        results.append(
            {
                "id": case_id,
                "category": case.get("category"),
                "evalTier": infer_eval_tier(case),
                "pass": True,
                "mode": "metrics-only",
                "detail": "KPI aggregation smoke only; UBT and LM Studio were not invoked.",
                "attempts": 0,
                "passAt1": False,
                **patch_metrics,
                **retry_metrics,
            }
        )
    return results


def calculate_kpi_metrics(results: list[dict]) -> dict:
    """Calculate extended Pass@K metrics without changing CLI behavior."""
    total = len(results)
    attempts_values = [int(row.get("attempts") or 0) for row in results if row.get("attempts") is not None]
    pass_at_1 = sum(1 for row in results if row.get("passAt1"))
    patch_metric_rows = [row for row in results if "expectedPatchTargetMatched" in row]
    expected_patch_covered = sum(1 for row in patch_metric_rows if row.get("expectedPatchTargetMatched"))
    histogram: dict[str, int] = {}
    for attempts in attempts_values:
        key = str(attempts)
        histogram[key] = histogram.get(key, 0) + 1

    def _group_summary(rows: list[dict]) -> dict:
        row_attempts = [int(row.get("attempts") or 0) for row in rows if row.get("attempts") is not None]
        row_pass_at_1 = sum(1 for row in rows if row.get("passAt1"))
        row_pass_at_k = sum(1 for row in rows if row.get("pass"))
        row_count = len(rows)
        return {
            "cases": row_count,
            "pass_at_1": row_pass_at_1,
            "pass_at_k": row_pass_at_k,
            "pass_at_1_rate": round(row_pass_at_1 / row_count, 3) if row_count else 0.0,
            "pass_at_k_rate": round(row_pass_at_k / row_count, 3) if row_count else 0.0,
            "avg_attempts": round(sum(row_attempts) / len(row_attempts), 3) if row_attempts else 0.0,
            "max_attempts_used": max(row_attempts) if row_attempts else 0,
            "wrong_file_edits": sum(1 for row in rows if row.get("wrongFileEdit")),
            "build_cs_false_positives": sum(1 for row in rows if row.get("buildCsFalsePositive")),
            "same_error_repeated": sum(1 for row in rows if row.get("sameErrorRepeated")),
            "no_op_edits": sum(1 for row in rows if row.get("noOpEdit")),
        }

    tier_groups: dict[str, list[dict]] = {}
    for row in results:
        tier = infer_eval_tier(row)
        tier_groups.setdefault(tier, []).append(row)
    return {
        "passAt1Count": pass_at_1,
        "passAt1Rate": round(pass_at_1 / total, 3) if total else 0.0,
        "averageAttempts": round(sum(attempts_values) / len(attempts_values), 3) if attempts_values else 0.0,
        "overall": _group_summary(results),
        "tiers": {tier: _group_summary(rows) for tier, rows in sorted(tier_groups.items())},
        "failedCaseIds": [str(row.get("id")) for row in results if not row.get("pass")],
        "attemptHistogram": dict(sorted(histogram.items(), key=lambda item: int(item[0]))),
        "sameErrorRepeatedCount": sum(1 for row in results if row.get("sameErrorRepeated")),
        "noOpEditCount": sum(1 for row in results if row.get("noOpEdit")),
        "validationRejectedCount": sum(1 for row in results if row.get("validationRejected")),
        "preApplyNoOpCount": sum(1 for row in results if row.get("preApplyNoOp")),
        "wrongFileEditCount": sum(1 for row in results if row.get("wrongFileEdit")),
        "buildCsTouchedCount": sum(1 for row in results if row.get("buildCsTouched")),
        "buildCsFalsePositiveCount": sum(1 for row in results if row.get("buildCsFalsePositive")),
        "forbiddenPatchHitCount": sum(1 for row in results if row.get("forbiddenPatchTargetHits")),
        "expectedPatchCoverageCount": expected_patch_covered,
        "expectedPatchCoverageRate": round(
            expected_patch_covered / len(patch_metric_rows),
            3,
        )
        if patch_metric_rows
        else 0.0,
        "repeatedErrorCaseIds": [str(row.get("id")) for row in results if row.get("sameErrorRepeated")],
        "noOpCaseIds": [str(row.get("id")) for row in results if row.get("noOpEdit")],
        "validationRejectedCaseIds": [str(row.get("id")) for row in results if row.get("validationRejected")],
        "preApplyNoOpCaseIds": [str(row.get("id")) for row in results if row.get("preApplyNoOp")],
        "wrongFileCaseIds": [str(row.get("id")) for row in results if row.get("wrongFileEdit")],
        "buildCsFalsePositiveCaseIds": [str(row.get("id")) for row in results if row.get("buildCsFalsePositive")],
        "forbiddenPatchCaseIds": [str(row.get("id")) for row in results if row.get("forbiddenPatchTargetHits")],
    }


def copy_fixture(fixture_dir: Path, work_dir: Path) -> None:
    ignore = shutil.ignore_patterns("golden", "request.txt")
    for item in fixture_dir.iterdir():
        if item.name in {"golden", "request.txt"}:
            continue
        dest = work_dir / item.name
        if item.is_dir():
            shutil.copytree(item, dest, ignore=ignore)
        else:
            shutil.copy2(item, dest)


def apply_golden(fixture_dir: Path, work_dir: Path) -> list[str]:
    golden = fixture_dir / "golden"
    written: list[str] = []
    if not golden.is_dir():
        return written
    for path in golden.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(golden)
        dest = work_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dest)
        written.append(str(rel).replace("\\", "/"))
    return written


def run_wrapper_live(
    work_dir: Path,
    project_file: Path,
    request_text: str,
    mode: str,
    target: str,
    max_attempts: int,
    url: str,
    model: str,
    ubt_path: Path,
    wrapper_timeout: int = 0,
) -> tuple[bool, str, int, dict]:
    run_dir = work_dir / "wrapper_run"
    run_dir.mkdir(parents=True, exist_ok=True)
    request_path = run_dir / "request.txt"
    request_path.write_text(request_text, encoding="utf-8")
    target_name, platform, configuration = split_ubt_target_spec(target)
    cmd = [
        sys.executable,
        str(SCRIPTS / "lmstudio_unreal_wrapper.py"),
        "--request-file",
        str(request_path),
        "--project-file",
        str(project_file),
        "--allow-direct-project-write",
        "--mode",
        mode,
        "--target",
        target_name,
        "--platform",
        platform,
        "--configuration",
        configuration,
        "--max-attempts",
        str(max_attempts),
        "--lmstudio-url",
        url,
        "--run-dir",
        str(run_dir),
        "--ubt-path",
        str(ubt_path),
    ]
    if model:
        cmd.extend(["--model", model])
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=wrapper_timeout if wrapper_timeout > 0 else None,
        )
        tail = (proc.stdout or proc.stderr or "")[-1500:]
        ok = proc.returncode == 0
    except subprocess.TimeoutExpired:
        tail = f"[TIMEOUT] wrapper killed after {wrapper_timeout}s"
        ok = False
    attempts = count_wrapper_attempts(run_dir)
    return ok, tail, attempts, read_retry_state_metrics(run_dir)


def run_case(
    case: dict,
    *,
    dry_run: bool,
    ubt_path: Path,
    ubt_timeout: int,
    max_attempts: int,
    url: str,
    model: str,
    wrapper_timeout: int = 0,
    artifact_dir: Path | None = None,
) -> dict:
    case_id = case["id"]
    mode_label = "dry-run" if dry_run else "live"
    if not case.get("fixtureDir") or not case.get("projectFile"):
        return {
            "id": case_id,
            "category": case.get("category"),
            "evalTier": infer_eval_tier(case),
            "pass": False,
            "mode": mode_label,
            "error": "fixture-only holdout case is not live-applicable: missing fixtureDir/projectFile",
            "detail": "This case can be used for metrics-only validation, taxonomy checks, and module resolver checks. A live UBT run requires a fixtureDir and projectFile.",
            "attempts": 0,
            "passAt1": False,
        }
    fixture_dir = (ROOT / case["fixtureDir"]).resolve()
    if not fixture_dir.is_dir():
        return {
            "id": case_id,
            "category": case.get("category"),
            "evalTier": infer_eval_tier(case),
            "pass": False,
            "mode": mode_label,
            "error": f"fixture missing: {fixture_dir}",
        }

    with tempfile.TemporaryDirectory(prefix=f"passatk_{case_id}_") as tmp:
        work_dir = Path(tmp)
        copy_fixture(fixture_dir, work_dir)
        project_file = work_dir / case["projectFile"]
        target = str(case.get("target") or "")
        mode = str(case.get("mode") or "compile_fix")
        request_path = fixture_dir / str(case.get("requestFile") or "request.txt")
        request_text = request_path.read_text(encoding="utf-8-sig") if request_path.is_file() else ""

        if dry_run:
            applied = apply_golden(fixture_dir, work_dir)
            ok, detail = run_ubt(project_file, target, ubt_path, ubt_timeout)
            blocked_by_live_coding = "blocked-by-live-coding" in detail
            if blocked_by_live_coding:
                print(
                    f"[pass-at-k] {case_id}: SKIP-worthy environment failure — "
                    "Live Coding is active. Close Unreal Editor and re-run.",
                    flush=True,
                )
            return {
                "id": case_id,
                "category": case.get("category"),
                "evalTier": infer_eval_tier(case),
                "pass": ok,
                "mode": "dry-run",
                "goldenFiles": applied,
                "detail": detail[:800],
                "attempts": 1 if ok else 0,
                "passAt1": ok,
                "environmentBlocked": blocked_by_live_coding,
            }

        ok, detail, attempts, retry_metrics = run_wrapper_live(
            work_dir,
            project_file,
            request_text,
            mode,
            target,
            max_attempts,
            url,
            model,
            ubt_path,
            wrapper_timeout=wrapper_timeout,
        )
        artifact_run_dir = ""
        patch_metrics = patch_target_metrics(case, work_dir / "wrapper_run")
        if artifact_dir:
            src_run_dir = work_dir / "wrapper_run"
            dest_run_dir = artifact_dir / case_id / "wrapper_run"
            if src_run_dir.is_dir():
                if dest_run_dir.exists():
                    shutil.rmtree(dest_run_dir)
                dest_run_dir.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(src_run_dir, dest_run_dir)
                artifact_run_dir = str(dest_run_dir)
        return {
            "id": case_id,
            "category": case.get("category"),
            "evalTier": infer_eval_tier(case),
            "pass": ok,
            "mode": "live",
            "detail": detail[:800],
            "attempts": attempts,
            "passAt1": ok and attempts <= 1,
            "artifactRunDir": artifact_run_dir,
            **patch_metrics,
            **retry_metrics,
        }


def main() -> int:
    parser = argparse.ArgumentParser(description="Pass@K compile-fix eval")
    parser.add_argument("--config", default="config/rag_eval_pass_at_k_cases.json")
    parser.add_argument("--dry-run", action="store_true", help="Apply golden/ + UBT only")
    parser.add_argument("--live", action="store_true", help="Run wrapper + LM Studio")
    parser.add_argument("--require-live", action="store_true")
    parser.add_argument("--url", default="http://localhost:1234/v1")
    parser.add_argument("--model", default="")
    parser.add_argument("--ubt-path", type=Path, default=DEFAULT_UBT)
    parser.add_argument("--max-attempts", type=int, default=0, help="Override config maxAttempts (e.g. 1 for Pass@1)")
    parser.add_argument("--early-exit", action="store_true",
                        help="Stop after min_pass_rate is met (saves time on passing runs)")
    parser.add_argument("--wrapper-timeout", type=int, default=0,
                        help="Per-case wrapper subprocess timeout in seconds (0 = no limit)")
    parser.add_argument("--metrics-only", action="store_true",
                        help="Fast KPI aggregation smoke; does not invoke UBT or LM Studio.")
    parser.add_argument("--retry-state-fixture", type=Path, default=None,
                        help="Optional directory containing <case-id>/retry_state.json or <case-id>.json fixtures.")
    parser.add_argument("--artifact-dir", type=Path, default=None,
                        help="Optional directory to preserve live wrapper run artifacts per case.")
    parser.add_argument("--output", type=Path, default=None,
                        help="Optional KPI JSON output path. Defaults to data/baseline/pass-at-k*.json.")
    args = parser.parse_args()

    config = json.loads((ROOT / args.config).read_text(encoding="utf-8-sig"))
    defaults = config.get("defaults") or {}
    max_attempts = int(args.max_attempts or defaults.get("maxAttempts") or 4)
    min_pass_rate = float(defaults.get("minPassRate") or 0.67)
    ubt_timeout = int(defaults.get("ubtTimeout") or 900)

    dry_run = args.dry_run or not args.live
    resolved_model = args.model
    if args.metrics_only:
        dry_run = False
    elif args.live:
        preflight = check_lmstudio(args.url, args.model)
        if not preflight.get("ok"):
            msg = preflight.get("error") or "LM Studio not reachable"
            print(f"[SKIP] {msg}", file=sys.stderr)
            if args.require_live:
                return 1
            dry_run = True
        else:
            resolved_model = str(preflight.get("resolvedModel") or args.model)
            print(f"Using LM Studio model: {resolved_model}")

    # Run cases sequentially; use --early-exit to stop once min_pass_rate is met.
    cases = config.get("cases") or []
    if args.metrics_only:
        results = build_metrics_only_results(cases, args.retry_state_fixture, args.artifact_dir)
    else:
        results = []
        for case in cases:
            result = run_case(
                case,
                dry_run=dry_run,
                ubt_path=args.ubt_path,
                ubt_timeout=ubt_timeout,
                max_attempts=max_attempts,
                url=args.url,
                model=resolved_model,
                wrapper_timeout=args.wrapper_timeout,
                artifact_dir=args.artifact_dir,
            )
            results.append(result)
            if args.early_exit:
                done = len(results)
                passed_so_far = sum(1 for r in results if r.get("pass"))
                if done > 0 and passed_so_far / done >= min_pass_rate and done >= min(3, len(cases)):
                    remaining = len(cases) - done
                    if remaining > 0:
                        print(f"[early-exit] {passed_so_far}/{done} passed ({passed_so_far/done:.0%} >= {min_pass_rate:.0%}), skipping {remaining} remaining cases")
                        break

    passed = sum(1 for r in results if r.get("pass"))
    env_blocked = sum(1 for r in results if r.get("environmentBlocked"))
    total = len(results)
    eligible_total = total - env_blocked
    pass_rate = (passed / eligible_total) if eligible_total else 1.0
    extended_metrics = calculate_kpi_metrics(results)
    pass_at_1 = int(extended_metrics["passAt1Count"])
    pass_at_1_rate = float(extended_metrics["passAt1Rate"])

    for row in results:
        status = "PASS" if row.get("pass") else "FAIL"
        attempts = row.get("attempts")
        attempt_note = f", attempts={attempts}" if attempts is not None else ""
        p1 = " pass@1" if row.get("passAt1") else ""
        print(f"[{status}] {row['id']} ({row.get('mode')}){attempt_note}{p1}")

    print(f"\nPass@K summary: {passed}/{eligible_total} ({pass_rate:.0%}), min {min_pass_rate:.0%}")
    if env_blocked:
        print(f"Environment-blocked (excluded from rate): {env_blocked}/{total}")
    if defaults.get("reportPassAt1") or config.get("tier") == "ceiling":
        print(f"Pass@1 summary: {pass_at_1}/{total} ({pass_at_1_rate:.0%})")

    kpi = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "mode": "metrics-only" if args.metrics_only else ("dry-run" if dry_run else "live"),
        "tier": config.get("tier") or "core",
        "config": args.config,
        "maxAttempts": max_attempts,
        "passCount": passed,
        "total": eligible_total,
        "totalCases": total,
        "environmentBlockedCount": env_blocked,
        "passRate": round(pass_rate, 3),
        "minPassRate": min_pass_rate,
        "pass": pass_rate >= min_pass_rate,
        "results": results,
    }
    kpi.update(extended_metrics)
    out_name = "pass-at-k-ceiling-kpi.json" if config.get("tier") == "ceiling" else "pass-at-k-kpi.json"
    out = args.output or (ROOT / "data" / "baseline" / out_name)
    if not out.is_absolute():
        out = (ROOT / out).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(kpi, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {out}")

    return 0 if args.metrics_only or pass_rate >= min_pass_rate else 1


if __name__ == "__main__":
    raise SystemExit(main())
