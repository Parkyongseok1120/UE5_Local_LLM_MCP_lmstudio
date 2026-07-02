#!/usr/bin/env python
"""Pass@K compile-fix eval — golden dry-run (Tier A) or wrapper+LM Studio (Tier B)."""

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
) -> tuple[bool, str, int]:
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
    proc = subprocess.run(
        cmd,
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    tail = (proc.stdout or proc.stderr or "")[-1500:]
    attempts = count_wrapper_attempts(run_dir)
    return proc.returncode == 0, tail, attempts


def count_wrapper_attempts(run_dir: Path) -> int:
    if not run_dir.is_dir():
        return 0
    return sum(1 for path in run_dir.iterdir() if path.is_dir() and path.name.startswith("attempt_"))


def run_case(
    case: dict,
    *,
    dry_run: bool,
    ubt_path: Path,
    ubt_timeout: int,
    max_attempts: int,
    url: str,
    model: str,
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

        ok, detail, attempts = run_wrapper_live(
            work_dir,
            project_file,
            request_text,
            mode,
            max_attempts,
            url,
            model,
            ubt_path,
        )
        return {
            "id": case_id,
            "pass": ok,
            "mode": "live",
            "detail": detail[:800],
            "attempts": attempts,
            "passAt1": ok and attempts <= 1,
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
    args = parser.parse_args()

    config = json.loads((ROOT / args.config).read_text(encoding="utf-8-sig"))
    defaults = config.get("defaults") or {}
    max_attempts = int(args.max_attempts or defaults.get("maxAttempts") or 4)
    min_pass_rate = float(defaults.get("minPassRate") or 0.67)
    ubt_timeout = int(defaults.get("ubtTimeout") or 900)

    dry_run = args.dry_run or not args.live
    resolved_model = args.model
    if args.live:
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

    results = [
        run_case(
            case,
            dry_run=dry_run,
            ubt_path=args.ubt_path,
            ubt_timeout=ubt_timeout,
            max_attempts=max_attempts,
            url=args.url,
            model=resolved_model,
        )
        for case in config.get("cases") or []
    ]

    passed = sum(1 for r in results if r.get("pass"))
    total = len(results)
    pass_rate = passed / total if total else 0.0
    pass_at_1 = sum(1 for r in results if r.get("passAt1"))
    pass_at_1_rate = pass_at_1 / total if total else 0.0

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
        "mode": "dry-run" if dry_run else "live",
        "tier": config.get("tier") or "core",
        "config": args.config,
        "maxAttempts": max_attempts,
        "passCount": passed,
        "total": total,
        "passRate": round(pass_rate, 3),
        "passAt1Count": pass_at_1,
        "passAt1Rate": round(pass_at_1_rate, 3),
        "minPassRate": min_pass_rate,
        "pass": pass_rate >= min_pass_rate,
        "results": results,
    }
    out_name = "pass-at-k-ceiling-kpi.json" if config.get("tier") == "ceiling" else "pass-at-k-kpi.json"
    out = ROOT / "data" / "baseline" / out_name
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(kpi, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {out}")

    return 0 if pass_rate >= min_pass_rate else 1


if __name__ == "__main__":
    raise SystemExit(main())
