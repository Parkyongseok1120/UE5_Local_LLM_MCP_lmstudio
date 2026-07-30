#!/usr/bin/env python
"""Evidence-bearing release verification for repository and local installs."""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
DEFAULT_TIMEOUT_SEC = 180

sys.path.insert(0, str(SCRIPTS))

from atomic_io import atomic_write_text  # noqa: E402


def check(
    label: str,
    ok: bool,
    detail: str = "",
    *,
    duration_ms: float = 0.0,
    proof_level: str = "direct",
    required: bool = True,
    skipped: bool = False,
) -> dict[str, Any]:
    row = {
        "label": label,
        "pass": bool(ok),
        "detail": detail,
        "durationMs": round(duration_ms, 2),
        "proofLevel": proof_level,
        "required": required,
    }
    if skipped:
        row["skipped"] = True
    status = "SKIP" if skipped else ("PASS" if ok else "FAIL")
    print(f"[{status}] {label}" + (f" - {detail}" if detail else ""))
    return row


def run_command_check(
    label: str,
    command: list[str],
    *,
    timeout_sec: int,
    detail_slice: str = "tail",
    required: bool = True,
) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        proc = subprocess.run(
            command,
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_sec,
        )
    except subprocess.TimeoutExpired:
        return check(
            label,
            False,
            f"timed out after {timeout_sec}s",
            duration_ms=(time.perf_counter() - started) * 1000,
            proof_level="executed",
            required=required,
        )
    stdout = (proc.stdout or "").strip()
    stderr = (proc.stderr or "").strip()
    combined = stdout or stderr
    detail = combined[:300] if detail_slice == "head" else combined[-300:]
    return check(
        label,
        proc.returncode == 0,
        detail,
        duration_ms=(time.perf_counter() - started) * 1000,
        proof_level="executed",
        required=required,
    )


def index_release_health(index: Path) -> dict[str, Any]:
    if not index.is_file():
        return {"ok": False, "chunkCount": 0, "quickCheck": "missing", "error": "index file is missing"}
    try:
        uri = f"{index.resolve().as_uri()}?mode=ro"
        with sqlite3.connect(uri, uri=True) as connection:
            quick_check = str(connection.execute("pragma quick_check").fetchone()[0])
            chunk_count = int(connection.execute("select count(*) from chunks").fetchone()[0])
    except (OSError, sqlite3.DatabaseError) as exc:
        return {
            "ok": False,
            "chunkCount": 0,
            "quickCheck": "error",
            "error": f"{type(exc).__name__}: {exc}",
        }
    return {
        "ok": quick_check.lower() == "ok" and chunk_count > 0,
        "chunkCount": chunk_count,
        "quickCheck": quick_check,
    }


def verify_tool_manifest(root: Path = ROOT) -> dict[str, Any]:
    try:
        manifest = json.loads(
            (root / "config" / "stable_tool_manifest.json").read_text(encoding="utf-8-sig")
        )
        from plan_consistency import RAG_ESSENTIAL_TOOLS
        from unreal_rag_mcp import ESSENTIAL_TOOL_NAMES, McpServer

        declared = list(manifest.get("ragEssential") or [])
        declared_set = set(declared)
        # Tool definitions are pure; bypass __init__ so a contract check never
        # reconciles machine-local jobs or touches runtime state.
        definition_server = object.__new__(McpServer)
        registered = {
            row["name"]
            for row in definition_server._all_tool_definitions_unfiltered()
        }
        issues: list[str] = []
        if len(declared) != len(declared_set):
            issues.append("ragEssential contains duplicate names")
        if declared_set != set(RAG_ESSENTIAL_TOOLS):
            issues.append("manifest and plan_consistency essential sets differ")
        if declared_set != set(ESSENTIAL_TOOL_NAMES):
            issues.append("manifest and MCP essential sets differ")
        missing = declared_set - registered
        if missing:
            issues.append(f"essential tools are not registered: {sorted(missing)}")
        return {
            "ok": not issues,
            "essentialToolCount": len(declared_set),
            "issues": issues,
        }
    except (ImportError, OSError, ValueError, json.JSONDecodeError) as exc:
        return {"ok": False, "issues": [f"{type(exc).__name__}: {exc}"]}


def installed_mcp_status(mcp_path: Path) -> dict[str, Any]:
    if not mcp_path.is_file():
        return {"ok": False, "detail": f"missing: {mcp_path}"}
    try:
        payload = json.loads(mcp_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"ok": False, "detail": f"{type(exc).__name__}: {exc}"}
    servers = payload.get("mcpServers") or {}
    matching = []
    for name, entry in servers.items():
        env = entry.get("env") or {}
        essential = str(env.get("MCP_ESSENTIAL_TOOLS") or "").strip().lower()
        if essential in {"1", "true", "yes", "on"}:
            matching.append(str(name))
    return {
        "ok": bool(matching),
        "detail": (
            f"essential profile configured for: {', '.join(matching)}"
            if matching
            else "no MCP server has MCP_ESSENTIAL_TOOLS=1"
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify Unreal RAG release readiness.")
    parser.add_argument("--skip-lmstudio", action="store_true")
    parser.add_argument("--skip-wrapper-dry", action="store_true")
    parser.add_argument(
        "--repo-only",
        action="store_true",
        help="Verify repository/package contracts without requiring a local LM Studio install or RAG index.",
    )
    parser.add_argument(
        "--timeout-sec",
        type=int,
        default=DEFAULT_TIMEOUT_SEC,
        help=f"Per-command timeout (default: {DEFAULT_TIMEOUT_SEC}s).",
    )
    args = parser.parse_args()
    timeout_sec = max(10, int(args.timeout_sec))

    results: list[dict[str, Any]] = []
    py = sys.executable
    results.append(check("python", True, py))
    node = shutil.which("node")
    results.append(check("node", node is not None, node or "missing"))

    manifest = verify_tool_manifest()
    results.append(
        check(
            "stable_tool_manifest",
            bool(manifest.get("ok")),
            (
                f"{manifest.get('essentialToolCount', 0)} essential tools"
                if manifest.get("ok")
                else "; ".join(manifest.get("issues") or [])
            ),
            proof_level="contract",
        )
    )

    index = ROOT / "data" / "unreal58" / "rag.sqlite"
    if args.repo_only:
        results.append(
            check(
                "rag_index_integrity",
                True,
                "not required in repo-only verification",
                required=False,
                skipped=True,
            )
        )
    else:
        index_health = index_release_health(index)
        results.append(
            check(
                "rag_index_integrity",
                bool(index_health.get("ok")),
                (
                    f"quick_check={index_health.get('quickCheck')}, chunks={index_health.get('chunkCount')}"
                    if index_health.get("ok")
                    else str(index_health.get("error") or index_health)
                ),
                proof_level="sqlite_integrity",
            )
        )

    mcp = Path.home() / ".lmstudio" / "mcp.json"
    if args.repo_only:
        results.append(
            check(
                "mcp_essential_tools",
                True,
                "not required in repo-only verification",
                required=False,
                skipped=True,
            )
        )
    else:
        mcp_status = installed_mcp_status(mcp)
        results.append(
            check(
                "mcp_essential_tools",
                bool(mcp_status.get("ok")),
                str(mcp_status.get("detail") or ""),
                proof_level="installed_config",
            )
        )

    doctor_command = [py, str(SCRIPTS / "rag_doctor.py")]
    if args.repo_only:
        doctor_command.append("--repo-only")
    results.append(
        run_command_check(
            "doctor",
            doctor_command,
            timeout_sec=timeout_sec,
        )
    )
    results.append(
        run_command_check(
            "encoding_contract",
            [py, str(SCRIPTS / "verify_encoding.py")],
            timeout_sec=timeout_sec,
        )
    )

    if args.skip_lmstudio or args.repo_only:
        results.append(
            check(
                "lmstudio_preflight",
                True,
                "explicitly skipped",
                required=False,
                skipped=True,
            )
        )
    else:
        results.append(
            run_command_check(
                "lmstudio_preflight",
                [py, str(SCRIPTS / "preflight_lmstudio.py")],
                timeout_sec=timeout_sec,
                detail_slice="head",
            )
        )

    if args.repo_only:
        results.append(
            check(
                "sample_rag_query",
                True,
                "not required in repo-only verification",
                required=False,
                skipped=True,
            )
        )
    else:
        results.append(
            run_command_check(
                "sample_rag_query",
                [
                    py,
                    str(SCRIPTS / "evaluate_rag_queries.py"),
                    "--query-set",
                    "config/rag_eval_genre_queries.json",
                ],
                timeout_sec=timeout_sec,
            )
        )

    results.append(
        run_command_check(
            "orchestrator_plan",
            [
                py,
                str(SCRIPTS / "agent_orchestrator.py"),
                "--request",
                "Fix missing generated.h",
                "--mode",
                "compile_fix",
                "--json",
            ],
            timeout_sec=timeout_sec,
        )
    )

    if args.skip_wrapper_dry or args.repo_only:
        results.append(
            check(
                "wrapper_dry_run",
                True,
                "explicitly skipped",
                required=False,
                skipped=True,
            )
        )
    else:
        results.append(
            run_command_check(
                "wrapper_dry_run",
                [py, str(SCRIPTS / "lmstudio_unreal_wrapper.py"), "--request", "noop", "--dry-run"],
                timeout_sec=timeout_sec,
            )
        )

    required = [row for row in results if row.get("required")]
    passed = sum(1 for row in required if row["pass"])
    payload = {
        "schemaVersion": 2,
        "mode": "repo-only" if args.repo_only else "installed",
        "passCount": passed,
        "total": len(required),
        "allResultCount": len(results),
        "results": results,
    }
    out = ROOT / "data" / "baseline" / "verify-release-latest.json"
    atomic_write_text(out, json.dumps(payload, ensure_ascii=False, indent=2))
    print(f"\nVerify release: {passed}/{len(required)} required checks")
    return 0 if passed == len(required) else 1


if __name__ == "__main__":
    raise SystemExit(main())
