#!/usr/bin/env python
"""Cross-server task telemetry and deterministic AgentRunReport generation."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from atomic_io import atomic_write_text
from state_root import ensure_state_root_layout, resolve_agent_state_root, task_state_dir

REPORT_VERSION = 1
TERMINAL_STATUSES = frozenset(
    {"completed", "cancelled", "failed", "cancellation_uncertain"}
)
_VOLATILE_ARGUMENT_KEYS = frozenset(
    {
        "authToken",
        "auth_token",
        "ownerCapability",
        "owner_capability",
        "conversationId",
        "conversation_id",
    }
)


def _utc_now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _task_id_from_mapping(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    control = value.get("control") if isinstance(value.get("control"), dict) else {}
    auth = (
        value.get("taskAuthorization")
        if isinstance(value.get("taskAuthorization"), dict)
        else value.get("task_authorization")
        if isinstance(value.get("task_authorization"), dict)
        else {}
    )
    return str(
        control.get("taskId")
        or auth.get("taskSessionId")
        or auth.get("task_session_id")
        or value.get("taskSessionId")
        or value.get("task_session_id")
        or ""
    ).strip()


def task_id_for_event(*values: Any) -> str:
    for value in values:
        task_id = _task_id_from_mapping(value)
        if task_id:
            return task_id
    return ""


def _stable_arguments(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _stable_arguments(child)
            for key, child in sorted(value.items(), key=lambda item: str(item[0]))
            if str(key) not in _VOLATILE_ARGUMENT_KEYS
            and str(key) not in {"taskAuthorization", "task_authorization"}
        }
    if isinstance(value, list):
        return [_stable_arguments(item) for item in value]
    return value


def arguments_hash(arguments: dict[str, Any] | None) -> str:
    encoded = json.dumps(
        _stable_arguments(arguments or {}),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:24]


def event_path(workspace: Path, task_session_id: str) -> Path:
    return task_state_dir(
        task_session_id,
        resolve_agent_state_root(workspace),
    ) / "run-events.jsonl"


def report_path(workspace: Path, task_session_id: str) -> Path:
    root = ensure_state_root_layout(resolve_agent_state_root(workspace))
    return root / "reports" / f"{task_session_id}.json"


def append_run_event(
    workspace: Path,
    task_session_id: str,
    event: dict[str, Any],
) -> bool:
    task_id = str(task_session_id or "").strip()
    if not task_id:
        return False
    path = event_path(workspace, task_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "version": REPORT_VERSION,
        "timestamp": _utc_now(),
        "taskSessionId": task_id,
        **dict(event),
    }
    line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(line + "\n")
    return True


def record_tool_started(
    workspace: Path,
    *,
    tool_name: str,
    arguments: dict[str, Any] | None,
    call_id: str,
    source: str,
) -> str:
    task_id = task_id_for_event(arguments)
    if not task_id:
        return ""
    append_run_event(
        workspace,
        task_id,
        {
            "kind": "tool_started",
            "source": str(source or "unknown"),
            "callId": str(call_id or ""),
            "tool": str(tool_name or "unknown"),
            "argumentsHash": arguments_hash(arguments),
        },
    )
    return task_id


def record_tool_result(
    workspace: Path,
    *,
    tool_name: str,
    arguments: dict[str, Any] | None,
    structured: dict[str, Any] | None,
    is_error: bool,
    call_id: str,
    source: str,
    duration_ms: float = 0.0,
) -> str:
    payload = structured if isinstance(structured, dict) else {}
    task_id = task_id_for_event(payload, arguments)
    if not task_id:
        return ""
    control = payload.get("control") if isinstance(payload.get("control"), dict) else {}
    append_run_event(
        workspace,
        task_id,
        {
            "kind": "tool_result",
            "source": str(source or "unknown"),
            "callId": str(call_id or ""),
            "tool": str(tool_name or "unknown"),
            "argumentsHash": arguments_hash(arguments),
            "ok": payload.get("ok") is not False and not bool(is_error),
            "isError": bool(is_error),
            "errorCode": str(payload.get("errorCode") or ""),
            "blockerFingerprint": str(control.get("blockerFingerprint") or ""),
            "nextAction": str(control.get("nextAction") or ""),
            "nextActionIsTool": control.get("nextActionIsTool") is True,
            "durationMs": round(max(0.0, float(duration_ms or 0.0)), 3),
        },
    )
    return task_id


def _read_events(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    events: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    for line in lines:
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            events.append(value)
    return events


def _parse_time(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _safe_nonnegative_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError, OverflowError):
        return 0


def _compactor_roots() -> Iterable[Path]:
    configured = str(os.environ.get("LMS_CONTEXT_COMPACTOR_STATE_DIR") or "").strip()
    if configured:
        yield Path(configured).expanduser()
    yield Path.home() / ".lmstudio" / "unreal-context-compactor" / "sessions"


def _compaction_count(task_session_id: str) -> int:
    maximum = 0
    seen_roots: set[str] = set()
    for root in _compactor_roots():
        key = os.path.normcase(str(root.resolve()))
        if key in seen_roots or not root.is_dir():
            continue
        seen_roots.add(key)
        try:
            checkpoints = root.glob("*/active-checkpoint.json")
            for path in checkpoints:
                try:
                    checkpoint = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                if not isinstance(checkpoint, dict):
                    continue
                controls = [checkpoint.get("protocolControl"), checkpoint.get("architectureControl")]
                if not any(
                    isinstance(control, dict)
                    and str(control.get("taskId") or "") == task_session_id
                    for control in controls
                ):
                    continue
                maximum = max(
                    maximum,
                    _safe_nonnegative_int(checkpoint.get("compactionGeneration")),
                )
        except OSError:
            continue
    return maximum


def _call_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    calls: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for index, event in enumerate(events):
        if event.get("kind") not in {"tool_started", "tool_result"}:
            continue
        call_id = str(event.get("callId") or f"legacy-{index}")
        if call_id not in calls:
            calls[call_id] = dict(event)
            order.append(call_id)
        elif event.get("kind") == "tool_result":
            calls[call_id].update(event)
    return [calls[item] for item in order]


def build_agent_run_report(
    workspace: Path,
    state: dict[str, Any],
    *,
    write: bool = True,
) -> dict[str, Any]:
    task_id = str(state.get("taskSessionId") or "").strip()
    if not task_id:
        raise ValueError("taskSessionId is required")
    events = _read_events(event_path(workspace, task_id))
    calls = _call_events(events)
    repeated = 0
    previous_signature = ""
    for call in calls:
        signature = f"{call.get('tool')}:{call.get('argumentsHash')}"
        if signature == previous_signature:
            repeated += 1
        previous_signature = signature
    results = [event for event in events if event.get("kind") == "tool_result"]
    validation_failures = sum(
        1
        for event in results
        if event.get("ok") is False
        and (
            "validat" in str(event.get("tool") or "").casefold()
            or "VALIDAT" in str(event.get("errorCode") or "").upper()
            or "CLAIM" in str(event.get("errorCode") or "").upper()
        )
    )
    compiler_failures = sum(
        1
        for event in results
        if event.get("ok") is False
        and (
            "build" in str(event.get("tool") or "").casefold()
            or "COMPIL" in str(event.get("errorCode") or "").upper()
            or "UBT" in str(event.get("errorCode") or "").upper()
        )
    )
    build_calls = sum(1 for event in calls if "build" in str(event.get("tool") or "").casefold())
    build_attempts = max(build_calls, len(state.get("buildProofHistory") or []))
    recovery_count = sum(
        1
        for event in results
        if "recover" in str(event.get("tool") or "").casefold()
        or "RECOVER" in str(event.get("errorCode") or "").upper()
        or str(event.get("nextAction") or "").casefold().endswith("recover")
    )
    supervisor = state.get("autonomySupervisor") if isinstance(state.get("autonomySupervisor"), dict) else {}
    recovery_count = max(
        recovery_count,
        _safe_nonnegative_int(supervisor.get("recoveryCount")),
    )
    human_interventions = len(state.get("humanInterventions") or [])
    start = _parse_time(state.get("createdAt"))
    end = _parse_time(state.get("completedAt") or state.get("updatedAt"))
    elapsed_seconds = max(0.0, (end - start).total_seconds()) if start and end else 0.0
    status = str(state.get("status") or "unknown")
    report = {
        "version": REPORT_VERSION,
        "generatedAt": _utc_now(),
        "taskSessionId": task_id,
        "model": str(
            state.get("model")
            or os.environ.get("LMSTUDIO_MODEL_IDENTIFIER")
            or os.environ.get("LMSTUDIO_MODEL")
            or "unknown"
        ),
        "task": str(state.get("request") or ""),
        "toolCalls": len(calls),
        "repeatedCalls": repeated,
        "validationFailures": validation_failures,
        "compilerFailures": compiler_failures,
        "buildAttempts": build_attempts,
        "compactions": _compaction_count(task_id),
        "recoveryCount": recovery_count,
        "humanInterventions": human_interventions,
        "elapsedSeconds": round(elapsed_seconds, 3),
        "status": status,
        "final": "PASS" if status == "completed" else "FAIL",
    }
    if write:
        target = report_path(workspace, task_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(target, json.dumps(report, ensure_ascii=False, indent=2))
        task_copy = task_state_dir(task_id, resolve_agent_state_root(workspace)) / "agent-run-report.json"
        atomic_write_text(task_copy, json.dumps(report, ensure_ascii=False, indent=2))
        report["reportPath"] = str(target)
    return report


def refresh_terminal_report(workspace: Path, task_session_id: str) -> dict[str, Any] | None:
    state_file = task_state_dir(
        task_session_id,
        resolve_agent_state_root(workspace),
    ) / "state.json"
    try:
        state = json.loads(state_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(state, dict) or str(state.get("status") or "") not in TERMINAL_STATUSES:
        return None
    return build_agent_run_report(workspace, state)


def _main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--task", required=True)
    args = parser.parse_args()
    report = refresh_terminal_report(Path(args.workspace).resolve(), args.task)
    print(json.dumps(report or {"ok": False, "reason": "task_not_terminal"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
