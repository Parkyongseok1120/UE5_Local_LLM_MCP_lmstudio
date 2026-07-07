#!/usr/bin/env python
"""E2E compile-readiness eval: static validate fixtures (+ optional UBT)."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from glob import glob
from pathlib import Path

from lmstudio_unreal_wrapper import has_static_errors, validate_unreal_readiness
from ubt_utils import build_ubt_command, split_ubt_target_spec
from workspace_paths import resolve_ubt_path

DEFAULT_UBT = resolve_ubt_path()


def latest_glob(root: Path, pattern: str) -> Path | None:
    matches = sorted(root.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
    return matches[0] if matches else None


def run_static(project_root: Path, expect_codes: list[str], forbid_errors: bool) -> tuple[bool, str]:
    findings = validate_unreal_readiness(project_root)
    codes = {f.code for f in findings}
    missing = [code for code in expect_codes if code not in codes]
    if missing:
        return False, f"expected codes not found: {missing} (got {sorted(codes)})"
    if forbid_errors and has_static_errors(findings):
        errors = [f for f in findings if f.severity == "error"]
        return False, f"unexpected errors: {[e.code for e in errors[:5]]}"
    return True, f"findings={len(findings)} codes={sorted(codes)}"


LIVE_CODING_BLOCK_MARKER = "Unable to build while Live Coding is active"
LIVE_CODING_HINT = (
    "[blocked-by-live-coding] Unreal Editor is running with Live Coding active. "
    "Close the editor (or disable Live Coding) and re-run this eval."
)


def run_ubt(project_file: Path, target: str, ubt_path: Path, timeout: int) -> tuple[bool, str]:
    if not ubt_path.is_file():
        return False, f"UBT missing: {ubt_path}"
    if not project_file.is_file():
        return False, f"uproject missing: {project_file}"
    cmd = build_ubt_command(ubt_path, project_file, target)
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(project_file.parent),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return False, "UBT timeout"
    ok = proc.returncode == 0
    output = proc.stdout or proc.stderr or ""
    tail = output[-2000:]
    if not ok and LIVE_CODING_BLOCK_MARKER in output:
        return False, f"{LIVE_CODING_HINT}\nreturncode={proc.returncode}\n{tail}"
    return ok, f"returncode={proc.returncode}\n{tail}"


def main() -> int:
    parser = argparse.ArgumentParser(description="E2E compile eval")
    parser.add_argument("--config", type=Path, default=Path("config/rag_eval_e2e_compile_cases.json"))
    parser.add_argument("--run-ubt", action="store_true")
    parser.add_argument("--ubt-path", type=Path, default=DEFAULT_UBT)
    parser.add_argument("--ubt-timeout", type=int, default=1200)
    args = parser.parse_args()

    rag_root = Path(__file__).resolve().parent.parent
    config = json.loads((rag_root / args.config).read_text(encoding="utf-8-sig"))
    fail = 0
    for case in config.get("cases") or []:
        case_id = case.get("id", "?")
        case_type = case.get("type", "")
        ok = False
        detail = ""

        if case_type == "static_validate":
            root = Path(str(case.get("projectRoot") or ""))
            expect = list(case.get("expectErrorCodes") or [])
            ok, detail = run_static(root, expect, forbid_errors=False)
        elif case_type == "static_validate_glob":
            pattern = str(case.get("projectGlob") or "")
            latest = latest_glob(rag_root, pattern)
            if not latest:
                ok, detail = False, f"no match for {pattern}"
            else:
                uproject = next(latest.glob("*.uproject"), None)
                root = latest if uproject is None else latest
                expect = list(case.get("expectErrorCodes") or [])
                ok, detail = run_static(root, expect, forbid_errors=True)
        elif case_type == "readiness_fixture":
            script = rag_root / "scripts" / "test_unreal_readiness_fixture.py"
            proc = subprocess.run(
                [sys.executable, str(script)],
                cwd=str(rag_root),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            ok = proc.returncode == 0
            detail = proc.stdout[-500:] or proc.stderr[-500:]
        elif case_type == "pass_at_k":
            cmd = [sys.executable, str(rag_root / "scripts" / "eval_pass_at_k.py"), "--dry-run"]
            proc = subprocess.run(
                cmd,
                cwd=str(rag_root),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            ok = proc.returncode == 0
            detail = (proc.stdout or proc.stderr or "")[-800:]
        elif case_type == "ubt_build":
            if not args.run_ubt:
                print(f"[SKIP] {case_id}: UBT (use -RunUbt)")
                continue
            project_path = Path(str(case.get("projectFile") or ""))
            if not project_path.is_absolute():
                project_path = rag_root / project_path
            ok, detail = run_ubt(
                project_path,
                str(case.get("target") or ""),
                args.ubt_path,
                args.ubt_timeout,
            )
        else:
            ok, detail = False, f"unknown type {case_type}"

        status = "PASS" if ok else "FAIL"
        print(f"[{status}] {case_id}: {detail}")
        if not ok:
            fail += 1

    print(f"\nsummary: {len(config.get('cases') or []) - fail} passed, {fail} failed")
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
