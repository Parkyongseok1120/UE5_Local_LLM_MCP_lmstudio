#!/usr/bin/env python
"""Consolidate data/failure_memory/*.jsonl into RAG-ready raw_failure_memory.jsonl."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def collect(memory_dir: Path, out_path: Path) -> int:
    seen: set[str] = set()
    rows: list[dict] = []
    if memory_dir.is_dir():
        for path in sorted(memory_dir.glob("*_failures.jsonl")):
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                doc = json.loads(line)
                if str(doc.get("status") or "accepted") == "rejected":
                    continue
                doc_id = str(doc.get("id") or "")
                if doc_id and doc_id in seen:
                    continue
                if doc_id:
                    seen.add(doc_id)
                rows.append(doc)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as handle:
        for doc in rows:
            handle.write(json.dumps(doc, ensure_ascii=False) + "\n")
    print(f"Wrote {len(rows)} failure memory records to {out_path}")
    return len(rows)


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description="Collect failure memory into RAG JSONL.")
    parser.add_argument("--memory-dir", default=str(root / "data" / "failure_memory"))
    parser.add_argument("--out", default=str(root / "data" / "unreal58" / "raw_failure_memory.jsonl"))
    args = parser.parse_args()
    collect(Path(args.memory_dir), Path(args.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
