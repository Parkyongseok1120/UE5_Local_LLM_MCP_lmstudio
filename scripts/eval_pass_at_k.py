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
    return {
        "sameErrorRepeated": bool(data.get("sameErrorRepeated")),
        "noOpEdit": bool(data.get("noOpEdit")),
        "sameErrorRepeatedAttempts": sum(1 for row in attempts or [] if row.get("sameErrorRepeated")),
        "noOpEditAttempts": sum(1 for row in attempts or [] if row.get("noOpEdit")),
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


def build_metrics_only_results(cases: list[dict], retry_state_fixture: Path | None = None) -> list[dict]:
    """Create fast smoke results that exercise KPI aggregation only.

    This mode intentionally does not call UBT or LM Studio and does not prove
    compile-fix success.
    """
    results: list[dict] = []
    for case in cases:
        case_id = str(case.get("id") or "")
        retry_metrics = retry_state_metrics_for_case(case_id, retry_state_fixture)
        results.append(
            {
                "id": case_id,
                "pass": True,
                "mode": "metrics-only",
                "detail": "KPI aggregation smoke only; UBT and LM Studio were not invoked.",
                "attempts": 0,
                "passAt1": False,
                **retry_metrics,
            }
        )
    return results


def calculate_kpi_metrics(results: list[dict]) -> dict:
    """Calculate extended Pass@K metrics without changing CLI behavior."""
    total = len(results)
    attempts_values = [int(row.get("attempts") or 0) for row in results if row.get("attempts") is not None]
    pass_at_1 = sum(1 for row in results if row.get("passAt1"))
    histogram: dict[str, int] = {}
    for attempts in attempts_values:
        key = str(attempts)
        histogram[key] = histogram.get(key, 0) + 1
    return {
        "passAt1Count": pass_at_1,
        "passAt1Rate": round(pass_at_1 / total, 3) if total else 0.0,
        "averageAttempts": round(sum(attempts_values) / len(attempts_values), 3) if attempts_values else 0.0,
        "failedCaseIds": [str(row.get("id")) for row in results if not row.get("pass")],
        "attemptHistogram": dict(sorted(histogram.items(), key=lambda item: int(item[0]))),
        "sameErrorRepeatedCount": sum(1 for row in results if row.get("sameErrorRepeated")),
        "noOpEditCount": sum(1 for row in results if row.get("noOpEdit")),
        "repeatedErrorCaseIds": [str(row.get("id")) for row in results if row.get("sameErrorRepeated")],
        "noOpCaseIds": [str(row.get("id")) for row in results if row.get("noOpEdit")],
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
) -> dict:
    case_id = case["id"]
    fixture_dir = (ROOT / case["fixtureDir"]).resolve()
    if not fixture_dir.is_dir():
        return {"id": case_id, "pass": False, "error": f"fixture missing: {fixture_dir}"}

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
            return {
                "id": case_id,
                "pass": ok,
                "mode": "dry-run",
                "goldenFiles": applied,
                "detail": detail[:800],
                "attempts": 1 if ok else 0,
                "passAt1": ok,
            }

        ok, detail, attempts, retry_metrics = run_wrapper_live(
            work_dir,
            project_file,
            request_text,
            mode,
            max_attempts,
            url,
            model,
            ubt_path,
            wrapper_timeout=wrapper_timeout,
        )
        return {
            "id": case_id,
            "pass": ok,
            "mode": "live",
            "detail": detail[:800],
            "attempts": attempts,
            "passAt1": ok and attempts <= 1,
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
        results = build_metrics_only_results(cases, args.retry_state_fixture)
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
    total = len(results)
    pass_rate = passed / total if total else 0.0
    extended_metrics = calculate_kpi_metrics(results)
    pass_at_1 = int(extended_metrics["passAt1Count"])
    pass_at_1_rate = float(extended_metrics["passAt1Rate"])

    for row in results:
        status = "PASS" if row.get("pass") else "FAIL"
        attempts = row.get("attempts")
        attempt_note = f", attempts={attempts}" if attempts is not None else ""
        p1 = " pass@1" if row.get("passAt1") else ""
        print(f"[{status}] {row['id']} ({row.get('mode')}){attempt_note}{p1}")

    print(f"\nPass@K summary: {passed}/{total} ({pass_rate:.0%}), min {min_pass_rate:.0%}")
    if defaults.get("reportPassAt1") or config.get("tier") == "ceiling":
        print(f"Pass@1 summary: {pass_at_1}/{total} ({pass_at_1_rate:.0%})")

    kpi = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "mode": "metrics-only" if args.metrics_only else ("dry-run" if dry_run else "live"),
        "tier": config.get("tier") or "core",
        "config": args.config,
        "maxAttempts": max_attempts,
        "passCount": passed,
        "total": total,
        "passRate": round(pass_rate, 3),
        "minPassRate": min_pass_rate,
        "pass": pass_rate >= min_pass_rate,
        "results": results,
    }
    kpi.update(extended_metrics)
    out_name = "pass-at-k-ceiling-kpi.json" if config.get("tier") == "ceiling" else "pass-at-k-kpi.json"
    out = ROOT / "data" / "baseline" / out_name
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(kpi, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {out}")

    return 0 if args.metrics_only or pass_rate >= min_pass_rate else 1


if __name__ == "__main__":
    raise SystemExit(main())
