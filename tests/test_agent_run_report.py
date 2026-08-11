from __future__ import annotations

import json
from pathlib import Path

from agent_run_report import (
    append_run_event,
    build_agent_run_report,
    record_tool_result,
    record_tool_started,
    refresh_terminal_report,
)


def _state(task_id: str, status: str = "completed") -> dict:
    return {
        "taskSessionId": task_id,
        "status": status,
        "request": "Add a generic replicated component",
        "createdAt": "2026-08-12T00:00:00+00:00",
        "completedAt": "2026-08-12T00:00:12.500000+00:00",
        "buildProofHistory": [{"kind": "build"}],
    }


def test_report_counts_calls_failures_repeats_and_compactions(tmp_path: Path, monkeypatch) -> None:
    state_root = tmp_path / "state"
    compactor_root = tmp_path / "compactor"
    monkeypatch.setenv("AGENT_STATE_ROOT", str(state_root))
    monkeypatch.setenv("LMS_CONTEXT_COMPACTOR_STATE_DIR", str(compactor_root))
    task_id = "task-report-1"
    auth = {"taskAuthorization": {"taskSessionId": task_id, "ownerCapability": "secret"}}

    record_tool_started(
        tmp_path,
        tool_name="unreal_code_sketch_claim_validate",
        arguments={**auth, "claims": [{"symbol": "FVector::Size"}]},
        call_id="1",
        source="test",
    )
    record_tool_result(
        tmp_path,
        tool_name="unreal_code_sketch_claim_validate",
        arguments={**auth, "claims": [{"symbol": "FVector::Size"}]},
        structured={"ok": False, "errorCode": "CLAIM_VALIDATION_FAILED"},
        is_error=True,
        call_id="1",
        source="test",
    )
    record_tool_started(
        tmp_path,
        tool_name="unreal_code_sketch_claim_validate",
        arguments={**auth, "claims": [{"symbol": "FVector::Size"}]},
        call_id="2",
        source="test",
    )
    record_tool_result(
        tmp_path,
        tool_name="unreal_code_sketch_claim_validate",
        arguments={**auth, "claims": [{"symbol": "FVector::Size"}]},
        structured={"ok": True},
        is_error=False,
        call_id="2",
        source="test",
    )
    record_tool_started(
        tmp_path,
        tool_name="build_unreal_project",
        arguments=auth,
        call_id="3",
        source="test",
    )
    record_tool_result(
        tmp_path,
        tool_name="build_unreal_project",
        arguments=auth,
        structured={"ok": False, "errorCode": "UBT_FAILED"},
        is_error=True,
        call_id="3",
        source="test",
    )

    session = compactor_root / "session-a"
    session.mkdir(parents=True)
    (session / "active-checkpoint.json").write_text(
        json.dumps(
            {
                "protocolControl": {"taskId": task_id},
                "compactionGeneration": 3,
            }
        ),
        encoding="utf-8",
    )

    report = build_agent_run_report(tmp_path, _state(task_id))
    assert report["toolCalls"] == 3
    assert report["repeatedCalls"] == 1
    assert report["validationFailures"] == 1
    assert report["compilerFailures"] == 1
    assert report["buildAttempts"] == 1
    assert report["compactions"] == 3
    assert report["elapsedSeconds"] == 12.5
    assert report["final"] == "PASS"
    assert Path(report["reportPath"]).is_file()


def test_terminal_refresh_is_fail_closed_to_known_terminal_state(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    task_id = "task-report-2"
    task_dir = tmp_path / "state" / "tasks" / task_id
    task_dir.mkdir(parents=True)
    state = _state(task_id, status="running")
    (task_dir / "state.json").write_text(json.dumps(state), encoding="utf-8")
    assert refresh_terminal_report(tmp_path, task_id) is None

    state["status"] = "failed"
    (task_dir / "state.json").write_text(json.dumps(state), encoding="utf-8")
    report = refresh_terminal_report(tmp_path, task_id)
    assert report is not None
    assert report["final"] == "FAIL"


def test_malformed_event_line_does_not_hide_valid_report_data(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    task_id = "task-report-3"
    append_run_event(tmp_path, task_id, {"kind": "tool_started", "callId": "1", "tool": "read_file"})
    event_file = tmp_path / "state" / "tasks" / task_id / "run-events.jsonl"
    with event_file.open("a", encoding="utf-8") as handle:
        handle.write("{not-json}\n")
    report = build_agent_run_report(tmp_path, _state(task_id), write=False)
    assert report["toolCalls"] == 1


def test_corrupt_numeric_telemetry_cannot_break_terminal_report(tmp_path: Path, monkeypatch) -> None:
    state_root = tmp_path / "state"
    compactor_root = tmp_path / "compactor"
    monkeypatch.setenv("AGENT_STATE_ROOT", str(state_root))
    monkeypatch.setenv("LMS_CONTEXT_COMPACTOR_STATE_DIR", str(compactor_root))
    task_id = "task-report-corrupt"
    session = compactor_root / "session"
    session.mkdir(parents=True)
    (session / "active-checkpoint.json").write_text(
        json.dumps(
            {
                "protocolControl": {"taskId": task_id},
                "compactionGeneration": "not-a-number",
            }
        ),
        encoding="utf-8",
    )
    state = _state(task_id)
    state["autonomySupervisor"] = {"recoveryCount": {"invalid": True}}
    report = build_agent_run_report(tmp_path, state, write=False)
    assert report["compactions"] == 0
    assert report["recoveryCount"] == 0
