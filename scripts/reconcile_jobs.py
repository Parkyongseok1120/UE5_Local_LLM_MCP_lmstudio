#!/usr/bin/env python
"""Reconcile stale wrapper jobs on MCP startup."""

from __future__ import annotations

from pathlib import Path
from typing import Any

ACTIVE_STATUSES = frozenset({"starting", "running", "cancel_requested"})


def reconcile_stale_jobs(workspace: Path, *, limit: int = 200) -> dict[str, Any]:
    from job_store import list_job_records
    from process_identity import verify_job_process
    from wrapper_job_manager import (
        _process_alive,
        append_progress,
        read_job,
        save_job,
        transition_job_status,
    )

    summary: dict[str, Any] = {"checked": 0, "terminalized": 0, "leftRunning": 0, "jobs": []}
    jobs = list_job_records(workspace, limit=limit)
    for job in jobs:
        status = str(job.get("status") or "")
        if status not in ACTIVE_STATUSES:
            continue
        job_id = str(job.get("jobId") or "")
        if not job_id:
            continue
        summary["checked"] += 1
        pid = int(job.get("pid") or 0)
        if status == "starting" and pid <= 0:
            latest = read_job(workspace, job_id) or job
            transition_job_status(latest, "failed")
            latest["reconciledStaleStarting"] = True
            append_progress(latest, "Reconciled stale starting job without PID.", level="error")
            save_job(workspace, latest)
            summary["terminalized"] += 1
            summary["jobs"].append({"jobId": job_id, "action": "failed_stale_starting"})
            continue
        if pid <= 0:
            continue
        alive = _process_alive(pid)
        identity_ok = verify_job_process(job) if alive == "alive" else False
        if alive == "alive" and identity_ok:
            summary["leftRunning"] += 1
            summary["jobs"].append({"jobId": job_id, "action": "left_running"})
            continue
        latest = read_job(workspace, job_id) or job
        if alive == "unknown":
            transition_job_status(latest, "cancellation_uncertain")
            latest["orphanProcessSuspected"] = True
            latest["reconciledOnStartup"] = True
            append_progress(latest, "Startup reconciliation: process state unknown.", level="error")
        elif alive == "dead" or not identity_ok:
            transition_job_status(latest, "failed")
            latest["reconciledOnStartup"] = True
            append_progress(latest, "Startup reconciliation: process no longer matches job.", level="error")
        save_job(workspace, latest)
        summary["terminalized"] += 1
        summary["jobs"].append({"jobId": job_id, "action": str(latest.get("status") or "terminalized")})
    return summary
