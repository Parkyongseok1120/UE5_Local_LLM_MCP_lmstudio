#!/usr/bin/env python
"""Benchmark Unreal RAG/MCP stack operations (Phase H baseline)."""

from __future__ import annotations

import argparse
import json
import shutil
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from rag_embeddings import embedding_status
from rag_index_ops import index_health
from rag_search import SearchOptions, search, search_hybrid
from workspace_paths import resolve_index_path

FALLBACK_INDEX = Path("data/unreal58/rag.sqlite")

DEFAULT_UBT = Path(
    r"C:\Program Files\Epic Games\UE_5.8\Engine\Binaries\DotNET\UnrealBuildTool\UnrealBuildTool.exe"
)

SEARCH_QUERIES = [
    ("codegen_component", "UActorComponent C++ example BeginPlay", "codegen"),
    ("enhanced_input", "Enhanced Input SetupInputComponent UEnhancedInputComponent", "codegen"),
    ("compile_fix", "fatal error C2065 undeclared identifier compile fix", "compile_fix"),
    ("runtime_debug", "runtime crash null UObject UPROPERTY access violation", "runtime_debug"),
    ("shooter", "third person shooter line trace weapon prototype", "prototype_component"),
    ("action_combat", "action combat stamina hit reaction component", "prototype_component"),
    ("platformer", "platformer character jump movement gravity", "prototype_component"),
    ("replication", "Server RPC replication OnRep replicated property", "codegen"),
    ("module_fix", "Build.cs module dependency PublicDependencyModuleNames", "module_fix"),
    ("include_fix", "GameFramework Character.h include path compile", "compile_fix"),
]

SLO_MS = {
    "health": 2500,
    "search_p95": 250,
    "search_p95_interim": 400,
    "search_hybrid_p95": 2000,
    "read_file_p95": 500,
    "ubt_help": 15000,
}

INTERIM_CHUNK_THRESHOLD = 120_000


def resolve_search_slo(chunk_count: int) -> int:
    if chunk_count > INTERIM_CHUNK_THRESHOLD:
        return SLO_MS["search_p95_interim"]
    return SLO_MS["search_p95"]


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(0, min(len(ordered) - 1, int(round((pct / 100.0) * (len(ordered) - 1)))))
    return ordered[rank]


def timed(fn) -> tuple[Any, float]:
    start = time.perf_counter()
    result = fn()
    elapsed_ms = (time.perf_counter() - start) * 1000.0
    return result, elapsed_ms


def bench_health(index: Path) -> dict[str, Any]:
    def run():
        health = index_health(index)
        health["embeddings"] = embedding_status(index)
        return health

    payload, elapsed_ms = timed(run)
    return {
        "name": "health",
        "elapsedMs": round(elapsed_ms, 2),
        "sloMs": SLO_MS["health"],
        "pass": elapsed_ms <= SLO_MS["health"],
        "chunkCount": int(payload.get("chunkCount") or 0),
        "hybridReady": bool((payload.get("embeddings") or {}).get("hybridV2Ready")),
    }


def bench_search(index: Path, top_k: int = 6, *, warmup: bool = True, chunk_count: int = 0) -> dict[str, Any]:
    if warmup:
        search(index, "warmup UActorComponent", top_k, SearchOptions(mode="codegen"))

    samples: list[dict[str, Any]] = []
    timings: list[float] = []
    for query_id, query, mode in SEARCH_QUERIES:
        options = SearchOptions(mode=mode, candidate_limit=max(40, top_k * 10))

        def run(q=query, o=options):
            return search(index, q, top_k, o)

        rows, elapsed_ms = timed(run)
        timings.append(elapsed_ms)
        samples.append(
            {
                "id": query_id,
                "mode": mode,
                "elapsedMs": round(elapsed_ms, 2),
                "hitCount": len(rows),
            }
        )

    p95 = percentile(timings, 95)
    slo = resolve_search_slo(chunk_count)
    return {
        "name": "search_fts_x10",
        "count": len(timings),
        "elapsedMs": {
            "min": round(min(timings), 2),
            "mean": round(statistics.mean(timings), 2),
            "p95": round(p95, 2),
            "max": round(max(timings), 2),
        },
        "sloMs": slo,
        "pass": p95 <= slo,
        "samples": samples,
    }


def bench_hybrid(index: Path, top_k: int = 6, *, warmup: bool = True, chunk_count: int = 0) -> dict[str, Any]:
    if warmup:
        search_hybrid(index, "warmup UActorComponent", top_k, SearchOptions(mode="codegen"))

    samples: list[dict[str, Any]] = []
    timings: list[float] = []
    for query_id, query, mode in SEARCH_QUERIES:
        options = SearchOptions(mode=mode, candidate_limit=max(40, top_k * 10))

        def run(q=query, o=options):
            return search_hybrid(index, q, top_k, o)

        rows, elapsed_ms = timed(run)
        timings.append(elapsed_ms)
        samples.append(
            {
                "id": query_id,
                "mode": mode,
                "elapsedMs": round(elapsed_ms, 2),
                "hitCount": len(rows),
            }
        )

    p95 = percentile(timings, 95)
    slo = SLO_MS["search_hybrid_p95"]
    return {
        "name": "search_hybrid_x10",
        "count": len(timings),
        "elapsedMs": {
            "min": round(min(timings), 2),
            "mean": round(statistics.mean(timings), 2),
            "p95": round(p95, 2),
            "max": round(max(timings), 2),
        },
        "sloMs": slo,
        "pass": p95 <= slo,
        "gateCritical": False,
        "samples": samples,
    }


def resolve_read_targets(rag_root: Path) -> list[Path]:
    shared = Path.home() / ".lmstudio" / "config" / "unreal-workspace.json"
    targets: list[Path] = []
    if shared.is_file():
        try:
            config = json.loads(shared.read_text(encoding="utf-8-sig"))
            active = Path(str(config.get("activeProject") or ""))
            if active.suffix.lower() == ".uproject" and active.is_file():
                source_dir = active.parent / "Source"
                if source_dir.is_dir():
                    for pattern in ("*.Build.cs", "*.h", "*.cpp"):
                        found = sorted(source_dir.rglob(pattern))[:2]
                        targets.extend(found)
        except (OSError, json.JSONDecodeError):
            pass

    if not targets:
        targets = [
            rag_root / "scripts" / "rag_search.py",
            rag_root / "scripts" / "unreal_rag_mcp.py",
            rag_root / "RAG_Project_Guidelines" / "Unreal_Programming" / "02_Codegen_Recipes_Core_Types.md",
        ]
    return [path for path in targets if path.is_file()][:5]


def bench_read_files(rag_root: Path, max_bytes: int = 524288) -> dict[str, Any]:
    targets = resolve_read_targets(rag_root)
    timings: list[float] = []
    samples: list[dict[str, Any]] = []
    for path in targets:
        def run(p=path):
            data = p.read_bytes()
            if len(data) > max_bytes:
                data = data[:max_bytes]
            return len(data)

        size, elapsed_ms = timed(run)
        timings.append(elapsed_ms)
        samples.append(
            {
                "path": str(path),
                "bytesRead": size,
                "elapsedMs": round(elapsed_ms, 2),
            }
        )

    p95 = percentile(timings, 95)
    return {
        "name": "read_file",
        "count": len(timings),
        "elapsedMs": {
            "min": round(min(timings), 2) if timings else 0.0,
            "mean": round(statistics.mean(timings), 2) if timings else 0.0,
            "p95": round(p95, 2),
            "max": round(max(timings), 2) if timings else 0.0,
        },
        "sloMs": SLO_MS["read_file_p95"],
        "pass": p95 <= SLO_MS["read_file_p95"],
        "samples": samples,
    }


def bench_ubt_help(ubt_path: Path) -> dict[str, Any]:
    if not ubt_path.is_file():
        return {
            "name": "ubt_help",
            "pass": False,
            "detail": f"missing {ubt_path}",
        }

    start = time.perf_counter()
    try:
        proc = subprocess.run(
            [str(ubt_path), "-Help"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        ok = proc.returncode in {0, 1} and bool((proc.stdout or proc.stderr or "").strip())
    except (OSError, subprocess.TimeoutExpired) as exc:
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        return {
            "name": "ubt_help",
            "elapsedMs": round(elapsed_ms, 2),
            "sloMs": SLO_MS["ubt_help"],
            "pass": False,
            "detail": str(exc),
        }

    return {
        "name": "ubt_help",
        "elapsedMs": round(elapsed_ms, 2),
        "sloMs": SLO_MS["ubt_help"],
        "pass": ok and elapsed_ms <= SLO_MS["ubt_help"],
        "returnCode": proc.returncode,
    }


def print_summary(report: dict[str, Any]) -> None:
    for bench in report["benchmarks"]:
        name = bench["name"]
        status = "PASS" if bench.get("pass") else "FAIL"
        if name in ("search_fts_x10", "search_hybrid_x10"):
            elapsed = bench["elapsedMs"]["p95"]
            slo = bench["sloMs"]
            print(f"[{status}] {name} p95={elapsed}ms (slo {slo}ms)")
        elif name == "read_file":
            elapsed = bench["elapsedMs"]["p95"]
            slo = bench["sloMs"]
            print(f"[{status}] {name} p95={elapsed}ms (slo {slo}ms)")
        elif "elapsedMs" in bench:
            print(f"[{status}] {name} {bench['elapsedMs']}ms (slo {bench.get('sloMs')}ms)")
        else:
            print(f"[{status}] {name} - {bench.get('detail', '')}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark Unreal MCP stack")
    parser.add_argument("--rag-root", type=Path, default=Path.cwd())
    parser.add_argument("--index", type=Path, default=None)
    parser.add_argument("--ubt-path", type=Path, default=DEFAULT_UBT)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--no-warmup", action="store_true", help="Skip embedding warmup before search bench")
    args = parser.parse_args()

    rag_root = args.rag_root.resolve()
    if args.index:
        index = args.index.resolve()
    else:
        try:
            index = resolve_index_path(rag_root)
        except Exception:
            index = (rag_root / FALLBACK_INDEX).resolve()
    if not index.is_file():
        print(f"[FAIL] index missing: {index}")
        return 1

    health_bench = bench_health(index)
    chunk_count = int(health_bench.get("chunkCount") or 0)
    benchmarks = [
        health_bench,
        bench_search(index, warmup=not args.no_warmup, chunk_count=chunk_count),
        bench_read_files(rag_root),
        bench_ubt_help(args.ubt_path),
    ]
    if health_bench.get("hybridReady"):
        benchmarks.insert(2, bench_hybrid(index, warmup=not args.no_warmup, chunk_count=chunk_count))

    gate_failures = [
        item for item in benchmarks
        if not item.get("pass") and item.get("gateCritical", True)
    ]
    fail_count = len(gate_failures)
    report: dict[str, Any] = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "ragRoot": str(rag_root),
        "indexPath": str(index),
        "sloMs": SLO_MS,
        "benchmarks": benchmarks,
        "summary": {
            "passCount": len(benchmarks) - fail_count,
            "failCount": fail_count,
            "allPass": fail_count == 0,
        },
    }

    out = args.output
    if out is None:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out = rag_root / "data" / "baseline" / f"mcp-bench-{stamp}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    out.write_text(payload, encoding="utf-8")
    report["outputPath"] = str(out)

    latest = rag_root / "data" / "baseline" / "mcp-bench-latest.json"
    latest.parent.mkdir(parents=True, exist_ok=True)
    try:
        if latest.exists() or latest.is_symlink():
            latest.unlink()
        latest.symlink_to(out.name)
    except OSError:
        shutil.copy2(out, latest)

    print_summary(report)
    print(f"\nSaved: {out}")
    print(f"Latest: {latest}")
    if fail_count:
        print(f"{fail_count} benchmark(s) missed SLO (baseline recorded anyway).")
        return 1
    print("All benchmarks within SLO.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
