#!/usr/bin/env python
"""Benchmark token budget KPIs against Phase 4b targets (no LLM)."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

from token_budget import (  # noqa: E402
    effective_rag_assembly_chars,
    estimate_tokens_from_chars,
    load_token_budget_file,
    mode_budget,
)


def bench_mode(mode: str, kpi: dict) -> dict:
    budget = mode_budget(mode)
    rag_chars = effective_rag_assembly_chars(mode)
    est_input_tokens = estimate_tokens_from_chars(rag_chars)
    max_output = int(budget.get("maxOutputTokens") or 4096)
    total_est = est_input_tokens + max_output

    limits = {
        "execute": int(kpi.get("executeSliceInputTokensMax") or 12000),
        "review": int(kpi.get("reviewInputTokensMax") or 12000),
    }
    cap = limits.get(mode, 12000)
    pab_max = int(budget.get("pabSummaryMaxChars") or kpi.get("pabSummaryMaxChars") or 2000)

    return {
        "mode": mode,
        "ragAssemblyChars": rag_chars,
        "estimatedInputTokens": est_input_tokens,
        "maxOutputTokens": max_output,
        "estimatedTotalTokens": total_est,
        "inputTokenCap": cap,
        "passInputCap": est_input_tokens <= cap,
        "readFileMaxBytes": int(budget.get("readFileMaxBytes") or 65536),
        "pabSummaryMaxChars": pab_max,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark token budget configuration.")
    parser.add_argument("--out", default="", help="Optional JSON output path")
    args = parser.parse_args()

    workspace = SCRIPTS.parent
    data = load_token_budget_file()
    kpi = data.get("kpi") or {}
    modes = ["execute", "review", "plan", "critique", "compile_fix"]
    results = [bench_mode(mode, kpi) for mode in modes]
    failed = [r for r in results if not r["passInputCap"]]

    for row in results:
        status = "PASS" if row["passInputCap"] else "FAIL"
        print(
            f"[{status}] {row['mode']}: est input {row['estimatedInputTokens']} tok "
            f"(cap {row['inputTokenCap']}), ragAssemblyChars={row['ragAssemblyChars']}"
        )

    payload = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "pass": len(failed) == 0,
        "failedModes": [r["mode"] for r in failed],
        "results": results,
    }

    out = Path(args.out) if args.out else workspace / "data" / "baseline" / "token-budget-kpi.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {out}")

    return 0 if payload["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
