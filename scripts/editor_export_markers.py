"""Own Editor export job files and exact-run completion marker polling."""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

from atomic_io import atomic_write_text
from editor_export_settings import ExportScope

REQUEST_NAME = "lmstudio_export_request.json"
DONE_NAME = "lmstudio_export_done.json"
ERROR_NAME = "lmstudio_export_error.json"


def clear_export_markers(export_dir: Path) -> None:
    for name in (DONE_NAME, ERROR_NAME, REQUEST_NAME):
        (export_dir / name).unlink(missing_ok=True)


def build_export_job(
    *,
    export_dir: Path,
    tools_dir: Path,
    content_path: str,
    maps_path: str,
    scope: ExportScope,
    workspace: Path,
    project_file: Path | None = None,
) -> dict[str, Any]:
    job_id = uuid.uuid4().hex
    job_dir = workspace / "data" / "editor_export_jobs"
    job_dir.mkdir(parents=True, exist_ok=True)
    job_path = job_dir / f"{job_id}.json"
    job = {
        "jobId": job_id,
        "exportDir": str(export_dir),
        "contentPath": content_path,
        "mapsPath": maps_path,
        "scope": scope,
        "projectFile": str(project_file.resolve()) if project_file is not None else "",
        "toolsDir": str(tools_dir),
        "donePath": str(export_dir / DONE_NAME),
        "errorPath": str(export_dir / ERROR_NAME),
        "requestPath": str(export_dir / REQUEST_NAME),
        "jobPath": str(job_path),
    }
    atomic_write_text(job_path, json.dumps(job, ensure_ascii=False, indent=2))
    return job


def submit_export_request(job: dict[str, Any]) -> None:
    export_dir = Path(str(job["exportDir"]))
    clear_export_markers(export_dir)
    request = {
        "jobId": str(job.get("jobId") or ""),
        "projectFile": str(job.get("projectFile") or ""),
        "contentPath": str(job.get("contentPath") or "/Game"),
        "mapsPath": str(job.get("mapsPath") or job.get("contentPath") or "/Game"),
        "scope": str(job.get("scope") or "all"),
        "requestedAt": time.time(),
    }
    atomic_write_text(
        export_dir / REQUEST_NAME,
        json.dumps(request, ensure_ascii=False, indent=2),
    )


def wait_for_export_markers(
    export_dir: Path,
    *,
    timeout_sec: int = 1800,
    poll_sec: float = 2.0,
    expected_run_id: str = "",
) -> dict[str, Any]:
    done_path = export_dir / DONE_NAME
    error_path = export_dir / ERROR_NAME
    deadline = time.monotonic() + timeout_sec
    last_invalid = ""
    while time.monotonic() < deadline:
        for marker_path, is_error in ((error_path, True), (done_path, False)):
            if not marker_path.is_file():
                continue
            try:
                payload = json.loads(marker_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                last_invalid = f"Invalid export marker {marker_path}: {exc}"
                continue
            if not isinstance(payload, dict):
                last_invalid = f"Invalid export marker object: {marker_path}"
                continue
            if expected_run_id and payload.get("runId") != expected_run_id:
                marker_path.unlink(missing_ok=True)
                last_invalid = f"Ignored stale export marker for another run: {marker_path}"
                continue
            payload["ok"] = False if is_error else bool(payload.get("ok", True))
            return payload
        time.sleep(poll_sec)
    detail = f" Last marker error: {last_invalid}" if last_invalid else ""
    return {
        "ok": False,
        "errorCode": "EDITOR_EXPORT_MARKER_TIMEOUT",
        "error": f"Timed out after {timeout_sec}s waiting for export completion.{detail}",
    }


__all__ = [
    "DONE_NAME",
    "ERROR_NAME",
    "REQUEST_NAME",
    "build_export_job",
    "clear_export_markers",
    "submit_export_request",
    "wait_for_export_markers",
]
