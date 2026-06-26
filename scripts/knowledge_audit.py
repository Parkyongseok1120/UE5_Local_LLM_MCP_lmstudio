#!/usr/bin/env python
"""Run knowledge coverage audit (FTS search hit@5)."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from rag_search import SearchOptions, search

DEFAULT_CONFIG = Path("config/knowledge_audit_queries.json")


def audit_category(index: Path, queries: list[dict], top_k: int = 5) -> dict:
    results = []
    pass_count = 0
    for item in queries:
        query = str(item.get("query") or "")
        mode = str(item.get("mode") or "auto")
        expect = str(item.get("expect_title") or "")
        rows = search(index, query, top_k, SearchOptions(mode=mode, candidate_limit=60))
        hit = any(expect.lower() in str(row.get("title") or "").lower() for row in rows[:top_k])
        if hit:
            pass_count += 1
        results.append(
            {
                "query": query,
                "mode": mode,
                "expectTitle": expect,
                "pass": hit,
                "topTitles": [row.get("title") for row in rows[:3]],
            }
        )
    total = len(queries)
    return {
        "total": total,
        "pass": pass_count,
        "rate": round(pass_count / total, 3) if total else 0.0,
        "results": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Knowledge audit")
    parser.add_argument("--index", type=Path, default=Path("data/unreal58/rag.sqlite"))
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    rag_root = Path(__file__).resolve().parent.parent
    index = args.index if args.index.is_absolute() else rag_root / args.index
    config_path = args.config if args.config.is_absolute() else rag_root / args.config
    payload_config = json.loads(config_path.read_text(encoding="utf-8-sig"))
    categories = payload_config.get("categories") or {}

    report = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "indexPath": str(index),
        "categories": {},
    }
    overall_pass = 0
    overall_total = 0
    for name, block in categories.items():
        cat = audit_category(index, list(block.get("queries") or []))
        report["categories"][name] = cat
        overall_pass += cat["pass"]
        overall_total += cat["total"]
        print(f"[{name}] {cat['pass']}/{cat['total']} ({cat['rate']:.0%})")

    report["summary"] = {
        "pass": overall_pass,
        "total": overall_total,
        "rate": round(overall_pass / overall_total, 3) if overall_total else 0.0,
    }
    print(f"Overall: {overall_pass}/{overall_total} ({report['summary']['rate']:.0%})")

    out = args.output
    if out is None:
        stamp = datetime.now().strftime("%Y%m%d")
        out = rag_root / "data" / "baseline" / f"knowledge-coverage-{stamp}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Saved: {out}")
    return 0 if report["summary"]["rate"] >= 0.8 else 1


if __name__ == "__main__":
    sys.exit(main())
