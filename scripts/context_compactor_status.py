"""Read recent LM Studio context-compactor telemetry for fail-closed agent startup."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


MAX_EVENT_FILES = 256
MAX_EVENT_TAIL_BYTES = 1_048_576
MAX_SCAN_DIRECTORIES = 4_096
MAX_SCAN_ENTRIES = 100_000
MAX_FUTURE_SKEW_SECONDS = 60
VALID_DECISIONS = {"normal", "soft_compact", "hard_compact"}
EVENT_FILE_RE = re.compile(r"events(?:-\d+)?\.jsonl\Z")


def _event_time(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed


def context_compactor_state_root() -> Path:
    configured = os.environ.get("LMS_CONTEXT_COMPACTOR_STATE_DIR", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return Path.home() / ".lmstudio" / "unreal-context-compactor" / "sessions"


def _recent_event_files(root: Path) -> tuple[list[Path], OSError | None]:
    candidates: list[tuple[int, Path]] = []
    pending = [root]
    directory_count = 0
    entry_count = 0
    try:
        while pending:
            directory = pending.pop()
            directory_count += 1
            if directory_count > MAX_SCAN_DIRECTORIES:
                raise OSError(
                    f"context telemetry scan exceeded {MAX_SCAN_DIRECTORIES} directories"
                )
            with os.scandir(directory) as entries:
                for entry in entries:
                    entry_count += 1
                    if entry_count > MAX_SCAN_ENTRIES:
                        raise OSError(
                            f"context telemetry scan exceeded {MAX_SCAN_ENTRIES} entries"
                        )
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            pending.append(Path(entry.path))
                        elif (
                            entry.is_file(follow_symlinks=False)
                            and EVENT_FILE_RE.fullmatch(entry.name)
                        ):
                            candidates.append((entry.stat(follow_symlinks=False).st_mtime_ns, Path(entry.path)))
                    except OSError:
                        continue
    except OSError as exc:
        return [], exc
    candidates.sort(key=lambda item: item[0], reverse=True)
    return [item[1] for item in candidates[:MAX_EVENT_FILES]], None


def _event_tail_lines(event_file: Path) -> list[str]:
    with event_file.open("rb") as handle:
        size = os.fstat(handle.fileno()).st_size
        start = max(0, size - MAX_EVENT_TAIL_BYTES)
        handle.seek(start)
        payload = handle.read(MAX_EVENT_TAIL_BYTES)
    if start:
        separator = payload.find(b"\n")
        payload = b"" if separator < 0 else payload[separator + 1 :]
    return payload.decode("utf-8", errors="replace").splitlines()


def _is_proxy_measurement(event: dict[str, Any]) -> bool:
    decision = event.get("decision") if isinstance(event.get("decision"), dict) else {}
    try:
        context_length = int(event.get("contextLength"))
        input_tokens = int(event.get("inputTokens"))
    except (TypeError, ValueError):
        return False
    return (
        event.get("type") == "context_measurement"
        and event.get("proxyActive") is True
        and bool(str(event.get("targetModel") or "").strip())
        and context_length > 0
        and input_tokens >= 0
        and str(decision.get("action") or "") in VALID_DECISIONS
    )


def recent_context_compactor_status(
    *,
    state_root: Path | None = None,
    max_age_seconds: int = 300,
    now: datetime | None = None,
) -> dict[str, Any]:
    try:
        root = (state_root or context_compactor_state_root()).expanduser().resolve()
        is_directory = root.is_dir()
    except OSError as exc:
        return {
            "active": False,
            "reason": "state_root_unreadable",
            "stateRoot": str(state_root or ""),
            "error": str(exc),
        }
    if not is_directory:
        return {"active": False, "reason": "state_root_missing", "stateRoot": str(root)}
    newest: tuple[datetime, dict[str, Any]] | None = None
    event_files, traversal_error = _recent_event_files(root)
    if traversal_error is not None:
        return {
            "active": False,
            "reason": "state_root_unreadable",
            "stateRoot": str(root),
            "error": str(traversal_error),
        }
    for event_file in event_files:
        try:
            lines = _event_tail_lines(event_file)
        except OSError:
            continue
        for line in lines:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue
            if not _is_proxy_measurement(event):
                continue
            measured_at = _event_time(event.get("at"))
            if measured_at and (newest is None or measured_at > newest[0]):
                newest = (measured_at, event)
    if newest is None:
        return {"active": False, "reason": "no_proxy_measurement", "stateRoot": str(root)}
    current = now or datetime.now(tz=timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    future_seconds = (newest[0] - current).total_seconds()
    event = newest[1]
    if future_seconds > MAX_FUTURE_SKEW_SECONDS:
        return {
            "active": False,
            "reason": "future_proxy_measurement",
            "stateRoot": str(root),
            "measuredAt": str(event.get("at") or ""),
            "futureSkewSeconds": future_seconds,
            "maxFutureSkewSeconds": MAX_FUTURE_SKEW_SECONDS,
        }
    age_seconds = max(0.0, (current - newest[0]).total_seconds())
    if age_seconds > max(1, int(max_age_seconds)):
        return {
            "active": False,
            "reason": "stale_proxy_measurement",
            "stateRoot": str(root),
            "measuredAt": str(event.get("at") or ""),
            "ageSeconds": age_seconds,
            "maxAgeSeconds": int(max_age_seconds),
        }
    return {
        "active": True,
        "reason": "fresh_proxy_measurement",
        "stateRoot": str(root),
        "measuredAt": str(event.get("at") or ""),
        "ageSeconds": age_seconds,
        "maxAgeSeconds": int(max_age_seconds),
        "targetModel": str(event.get("targetModel") or ""),
        "workingDirectory": str(event.get("workingDirectory") or ""),
    }
