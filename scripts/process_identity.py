#!/usr/bin/env python
"""Verify wrapper job subprocess identity against stored job metadata."""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from datetime import datetime, timezone
from typing import Any

_CREATION_TOLERANCE_SEC = 30.0


def _parse_iso(value: str) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _process_alive(pid: int) -> bool:
    from process_probe import probe_process_alive

    return probe_process_alive(pid) != "dead"


def _probe_process(pid: int) -> tuple[str, datetime | None]:
    from process_probe import ProbeTimeout, run_probe

    if sys.platform == "win32":
        result = run_probe(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                (
                    f"$p = Get-CimInstance Win32_Process -Filter \"ProcessId={pid}\"; "
                    "if ($null -eq $p) { exit 1 }; "
                    "Write-Output ($p.CommandLine); "
                    "Write-Output ($p.CreationDate.ToUniversalTime().ToString('o'))"
                ),
            ],
        )
        if isinstance(result, ProbeTimeout) or result.returncode != 0:
            return "", None
        lines = [line for line in (result.stdout or "").splitlines() if line.strip()]
        if not lines:
            return "", None
        command_line = lines[0]
        created = _parse_iso(lines[1]) if len(lines) > 1 else None
        return command_line, created
    cmdline_path = f"/proc/{pid}/cmdline"
    try:
        raw = open(cmdline_path, "rb").read().replace(b"\x00", b" ").decode("utf-8", errors="replace").strip()
    except OSError:
        raw = ""
    created = None
    try:
        stat = open(f"/proc/{pid}/stat", encoding="utf-8").read().split()
        start_ticks = int(stat[21])
        uptime_sec = float(open("/proc/uptime", encoding="utf-8").read().split()[0])
        hz = os.sysconf("SC_CLK_TCK")
        boot = datetime.now(tz=timezone.utc).timestamp() - uptime_sec
        created = datetime.fromtimestamp(boot + (start_ticks / hz), tz=timezone.utc)
    except (OSError, ValueError, IndexError):
        created = None
    return raw, created


def command_fingerprint(command: list[str]) -> str:
    return hashlib.sha256("\x00".join(str(part) for part in command).encode("utf-8")).hexdigest()


def _command_line_matches(command: list[str], command_line: str) -> bool:
    if not command_line:
        return False
    for part in command:
        token = str(part or "").strip()
        if not token:
            continue
        if token in command_line:
            continue
        base = os.path.basename(token)
        if base and base in command_line:
            continue
        return False
    return True


def verify_job_process(job: dict[str, Any]) -> bool:
    pid = int(job.get("pid") or 0)
    if pid <= 0 or not _process_alive(pid):
        return False
    command = job.get("command")
    expected_fp = str(job.get("commandFingerprint") or "").strip()
    expected_started = _parse_iso(str(job.get("pidStartedAt") or ""))
    if isinstance(command, list) and command:
        if expected_fp and command_fingerprint(command) != expected_fp:
            return False
        command_line, created = _probe_process(pid)
        if not _command_line_matches(command, command_line):
            return False
        if expected_started and created:
            delta = abs((created - expected_started).total_seconds())
            if delta > _CREATION_TOLERANCE_SEC:
                return False
        return True
    if expected_fp or expected_started:
        command_line, created = _probe_process(pid)
        if expected_started and created:
            delta = abs((created - expected_started).total_seconds())
            if delta > _CREATION_TOLERANCE_SEC:
                return False
        return bool(command_line)
    return True
