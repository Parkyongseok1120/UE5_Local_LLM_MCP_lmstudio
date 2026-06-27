#!/usr/bin/env python
"""Release verification: install, MCP, index, sample query (Phase 22)."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"


def check(label: str, ok: bool, detail: str = "") -> dict:
    row = {"label": label, "pass": ok, "detail": detail}
    status = "PASS" if ok else "FAIL"
    print(f"[{status}] {label}" + (f" - {detail}" if detail else ""))
    return row


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify Unreal58-RAG release readiness.")
    parser.add_argument("--skip-lmstudio", action="store_true")
    parser.add_argument("--skip-wrapper-dry", action="store_true")
    args = parser.parse_args()

    results: list[dict] = []
    py = sys.executable
    results.append(check("python", True, py))

    node = shutil.which("node")
    results.append(check("node", node is not None, node or "missing"))

    index = ROOT / "data" / "unreal58" / "rag.sqlite"
    results.append(check("rag_index", index.is_file(), str(index)))

    mcp = Path.home() / ".lmstudio" / "mcp.json"
    results.append(check("mcp_json", mcp.is_file(), str(mcp)))

    proc = subprocess.run([py, str(SCRIPTS / "rag_doctor.py")], cwd=str(ROOT), capture_output=True, text=True)
    results.append(check("doctor", proc.returncode == 0, proc.stdout.strip()[-200:]))

    if not args.skip_lmstudio:
        proc = subprocess.run([py, str(SCRIPTS / "preflight_lmstudio.py")], cwd=str(ROOT), capture_output=True, text=True)
        results.append(check("lmstudio_preflight", proc.returncode == 0, proc.stdout.strip()[:200]))

    proc = subprocess.run(
        [py, str(SCRIPTS / "evaluate_rag_queries.py"), "--query-set", "config/rag_eval_genre_queries.json"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    results.append(check("sample_rag_query", proc.returncode == 0))

    proc = subprocess.run([py, str(SCRIPTS / "agent_orchestrator.py"), "--request", "Fix missing generated.h", "--mode", "compile_fix", "--json"], cwd=str(ROOT), capture_output=True, text=True)
    results.append(check("orchestrator_plan", proc.returncode == 0 and "compile_fix" in proc.stdout))

    if not args.skip_wrapper_dry:
        proc = subprocess.run(
            [py, str(SCRIPTS / "lmstudio_unreal_wrapper.py"), "--request", "noop", "--dry-run"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
        )
        results.append(check("wrapper_dry_run", proc.returncode == 0))

    passed = sum(1 for r in results if r["pass"])
    payload = {"passCount": passed, "total": len(results), "results": results}
    out = ROOT / "data" / "baseline" / "verify-release-latest.json"
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"\nVerify release: {passed}/{len(results)}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
