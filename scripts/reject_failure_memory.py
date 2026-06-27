#!/usr/bin/env python
"""Reject a failure memory record by id."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

from failure_memory_rerank import reject_failure_record  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Reject failure memory record.")
    parser.add_argument("--project-name", required=True)
    parser.add_argument("--record-id", required=True)
    parser.add_argument("--memory-dir", default="data/failure_memory")
    args = parser.parse_args()
    root = SCRIPTS.parent
    ok = reject_failure_record(root / args.memory_dir, args.project_name, args.record_id)
    if ok:
        print(f"Rejected {args.record_id} in {args.project_name}")
        return 0
    print("Record not found", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
