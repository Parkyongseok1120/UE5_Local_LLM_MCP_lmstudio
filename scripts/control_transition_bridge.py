#!/usr/bin/env python
"""JSON bridge exposing the canonical Python task-control reducer to adapters.

The Node Agent MCP is an execution adapter.  It may record committed facts, but
it must not carry a second hand-maintained semantic transition table.  This
stdin/stdout bridge keeps task state and authorization material off argv while
reusing the production Python reducer directly.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Any

from phase_tool_router import (
    _authoritative_project_file,
    _authoritative_project_root,
    _mutation_tool_for_state,
    _path_identity,
    _pre_gate_source_read_path,
    commit_control_transition,
    derive_next_obligation,
    failed_gate_attempt_for_current_scope,
    reduce_committed_event,
    validation_finding_recovery,
)
from synthesis_readiness import (
    derive_synthesis_readiness,
    is_source_evidence_task,
    synthesis_latch_matches,
)


def _object(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def dispatch(payload: dict[str, Any]) -> dict[str, Any]:
    operation = str(payload.get("operation") or "").strip()
    state = _object(payload.get("state"))
    arguments = _object(payload.get("arguments"))

    if operation == "commit_control_transition":
        return {"ok": True, "state": commit_control_transition(state)}
    if operation == "derive_next_obligation":
        return {"ok": True, "control": derive_next_obligation(state)}
    if operation == "reduce_committed_event":
        return {
            "ok": True,
            "state": reduce_committed_event(state, _object(arguments.get("event"))),
        }
    if operation == "derive_synthesis_readiness":
        return {"ok": True, "readiness": derive_synthesis_readiness(state)}
    if operation == "synthesis_latch_matches":
        return {"ok": True, "value": synthesis_latch_matches(state)}
    if operation == "failed_gate_attempt_for_current_scope":
        return {
            "ok": True,
            "attempt": failed_gate_attempt_for_current_scope(
                state,
                str(arguments.get("gate") or ""),
            ),
        }
    if operation == "mutation_tool_for_state":
        return {
            "ok": True,
            "tool": _mutation_tool_for_state(
                state,
                _object(arguments.get("route")),
                host_platform=str(arguments.get("hostPlatform") or "") or None,
            ),
        }
    if operation == "pre_gate_source_read_path":
        pending = arguments.get("pendingGates")
        return {
            "ok": True,
            "path": _pre_gate_source_read_path(
                state,
                [str(item) for item in pending] if isinstance(pending, list) else [],
                host_platform=str(arguments.get("hostPlatform") or "") or None,
            ),
        }
    if operation == "transition_path_identity":
        return {
            "ok": True,
            "identity": _path_identity(
                arguments.get("value"),
                host_platform=str(arguments.get("hostPlatform") or "") or None,
            ),
        }
    if operation == "validation_finding_recovery":
        status, scope, tool, targets = validation_finding_recovery(
            _object(arguments.get("finding"))
        )
        return {
            "ok": True,
            "recovery": {
                "status": status,
                "scopeDisposition": scope,
                "requiredTool": tool,
                "targetFiles": targets,
            },
        }
    if operation == "authoritative_project_file":
        return {"ok": True, "path": _authoritative_project_file(state)}
    if operation == "authoritative_project_root":
        return {"ok": True, "path": _authoritative_project_root(state)}
    if operation == "is_source_evidence_task":
        return {"ok": True, "value": is_source_evidence_task(state)}
    raise ValueError(f"unsupported canonical control operation: {operation}")


def main() -> int:
    try:
        raw = json.load(sys.stdin)
        if not isinstance(raw, dict):
            raise ValueError("bridge payload must be an object")
        result_file = raw.get("resultFile")
        if raw.get("payloadFile"):
            payload_path = Path(str(raw["payloadFile"])).resolve()
            result_path = Path(str(result_file or "")).resolve()
            if (
                not payload_path.parent.name.startswith("unreal-control-bridge-")
                or result_path.parent != payload_path.parent
                or payload_path.name != "request.json"
                or result_path.name != "response.json"
            ):
                raise ValueError("invalid canonical bridge file transport")
            with payload_path.open("r", encoding="utf-8") as handle:
                raw = json.load(handle)
            if not isinstance(raw, dict):
                raise ValueError("bridge file payload must be an object")
            rendered = json.dumps(dispatch(raw), ensure_ascii=False, separators=(",", ":"))
            with result_path.open("x", encoding="utf-8") as handle:
                handle.write(rendered)
            print(
                json.dumps(
                    {"ok": True, "resultFile": str(result_path)},
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            )
        else:
            print(json.dumps(dispatch(raw), ensure_ascii=False, separators=(",", ":")))
        return 0
    except Exception as exc:  # fail closed at the adapter boundary
        print(
            json.dumps(
                {
                    "ok": False,
                    "errorCode": "TASK_PYTHON_BRIDGE_FAILED",
                    "error": str(exc)[:800],
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
