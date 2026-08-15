#!/usr/bin/env python
"""Consolidate data/failure_memory/*.jsonl into RAG-ready raw_failure_memory.jsonl."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from failure_memory_rerank import load_failure_records
from workspace_paths import find_workspace_root, resolve_index_dir


def collect(memory_dir: Path, out_path: Path) -> int:
    rows = load_failure_records(memory_dir)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as handle:
        for doc in rows:
            handle.write(json.dumps(doc, ensure_ascii=False) + "\n")
    print(f"Wrote {len(rows)} failure memory records to {out_path}")
    return len(rows)


def main() -> int:
    root = find_workspace_root()
    parser = argparse.ArgumentParser(description="Collect failure memory into RAG JSONL.")
    parser.add_argument("--memory-dir", default=str(root / "data" / "failure_memory"))
    parser.add_argument("--out", default="", help="Output JSONL (default: configured RAG data directory).")
    args = parser.parse_args()
    out_path = Path(args.out) if args.out else resolve_index_dir(root) / "raw_failure_memory.jsonl"
    collect(Path(args.memory_dir), out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
