#!/usr/bin/env python
"""Migrate legacy JSON wrapper jobs into the SQLite job store."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from job_store import write_job_record  # noqa: E402
from wrapper_job_manager import jobs_root, job_path  # noqa: E402


def migrate_jobs(workspace: Path, *, dry_run: bool = False) -> dict[str, int]:
    root = jobs_root(workspace)
    migrated = 0
    skipped = 0
    failed = 0
    for path in sorted(root.glob("*.json")):
        try:
            job = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            failed += 1
            continue
        job_id = str(job.get("jobId") or path.stem)
        job["jobId"] = job_id
        if dry_run:
            migrated += 1
            continue
        try:
            if write_job_record(job, workspace=workspace):
                migrated += 1
            else:
                skipped += 1
        except Exception:
            failed += 1
    return {"migrated": migrated, "skipped": skipped, "failed": failed, "legacyDir": str(root)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrate legacy JSON wrapper jobs to SQLite.")
    parser.add_argument("--workspace", required=True, help="Workspace root containing data/mcp_wrapper_jobs")
    parser.add_argument("--dry-run", action="store_true", help="Count jobs without writing to SQLite")
    args = parser.parse_args()
    summary = migrate_jobs(Path(args.workspace).resolve(), dry_run=bool(args.dry_run))
    print(json.dumps(summary, indent=2))
    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
