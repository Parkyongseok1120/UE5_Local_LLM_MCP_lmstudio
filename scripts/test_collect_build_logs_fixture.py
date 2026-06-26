#!/usr/bin/env python
"""Regression test for synthetic Unreal build log collection."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


EXPECTED = {
    ("C1083", "module_fix"),
    ("LNK2019", "link_fix"),
    ("UNREALHEADERTOOL", "reflection_fix"),
    ("C1083", "reflection_fix"),
}


def load_records(path: Path) -> list[dict]:
    records: list[dict] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def main(args: argparse.Namespace) -> int:
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        "scripts/collect_build_logs.py",
        "--root",
        args.fixture_root,
        "--out",
        str(out_path),
        "--group-following-lines",
        "0",
        "--logs-only",
    ]
    subprocess.run(command, check=True)
    records = load_records(out_path)
    found = {
        (
            str(record.get("metadata", {}).get("error_code") or "").upper(),
            str(record.get("metadata", {}).get("error_kind") or ""),
        )
        for record in records
    }
    missing = EXPECTED - found
    if missing:
        print(f"[FAIL] missing expected build log records: {sorted(missing)}")
        print(f"found: {sorted(found)}")
        return 1
    print(f"[PASS] build log fixture records found: {len(EXPECTED)} expected, {len(records)} collected")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Test build-log collector with synthetic Unreal logs.")
    parser.add_argument("--fixture-root", default="tests/fixtures/build_logs")
    parser.add_argument("--out", default="data/test/raw_build_logs_fixture.jsonl")
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(main(parse_args()))
