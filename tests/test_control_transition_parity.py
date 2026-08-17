from __future__ import annotations

import json
import shutil
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from phase_tool_router import (  # noqa: E402
    _mutation_tool_for_state,
    _pre_gate_source_read_path,
    commit_control_transition,
    derive_next_obligation,
    validation_finding_recovery,
)
from task_api import _control_args_match  # noqa: E402


SKETCH_GATE = "unreal_code_sketch_claim_validate"
REPEATED_GATE_ARGS = {
    "path": "project://Source/Demo/Feature.cpp",
    "startLine": 17,
    "endLine": 31,
}


def _base_state() -> dict:
    return {
        "taskSessionId": "parity-task",
        "status": "running",
        "workspaceRoot": str(ROOT / "fixtures" / "ParityProject"),
        "projectFile": str(
            ROOT / "fixtures" / "ParityProject" / "ParityProject.uproject"
        ),
        "taskKind": "edit",
        "planRevision": "3",
        "activeSliceId": "slice",
        "requiredGateSetHash": "gate-set",
        "mutationGeneration": 0,
        "completedGates": {
            "unreal_code_sketch_claim_validate": {
                "status": "completed",
                "gateSetHash": "gate-set",
                "planRevision": "3",
                "activeSliceId": "slice",
                "mutationGeneration": 0,
            }
        },
        "selectedTargetSnapshots": [
            {"path": "Source/Demo/Feature.cpp", "exists": True, "fileHash": "a"}
        ],
        "continuity": {"checkpoint": {}},
        "toolRoute": {
            "phase": "executor",
            "routeHash": "route",
            "pendingGates": [],
            "selectedSlice": {
                "sliceId": "slice",
                "files": ["Source/Demo/Feature.cpp"],
            },
            "activeTools": [
                "replace_in_file",
                "write_file",
                "apply_edit_bundle",
                "static_validate_project",
                "build_unreal_project",
                "run_unreal_automation_tests",
                "read_file",
                "read_file_range",
                "search_files",
                "unreal_code_sketch_claim_validate",
                "unreal_feature_intent_resolve",
            ],
        },
    }


def _failed_gate_state(*, attempt_count: int) -> dict:
    state = deepcopy(_base_state())
    state["completedGates"] = {}
    state["toolRoute"]["phase"] = "planner"
    state["toolRoute"]["pendingGates"] = [SKETCH_GATE]
    state["failedGateAttempts"] = {
        SKETCH_GATE: {
            "attemptCount": attempt_count,
            "fingerprint": "same-gate-input",
            "gateSetHash": state["requiredGateSetHash"],
            "planRevision": state["planRevision"],
            "activeSliceId": state["activeSliceId"],
            "mutationGeneration": state["mutationGeneration"],
            "validationErrorCode": "ENGINE_RETURN_TYPE_MISMATCH",
            "nextAction": "read_file_range",
            "nextActionIsTool": True,
            "nextActionArgs": deepcopy(REPEATED_GATE_ARGS),
        }
    }
    return state


def _recovery_obligation_state(
    status: str,
    *,
    required_tool: dict | None = None,
    attempt_count: int = 0,
) -> dict:
    state = deepcopy(_base_state())
    state["recoveryObligation"] = {
        "source": "parity-test",
        "status": status,
        "errorCode": f"RECOVERY_{status.upper()}",
        "fingerprint": f"fingerprint-{status}-{attempt_count}",
        "attemptCount": attempt_count,
        "requiredTool": deepcopy(required_tool or {}),
    }
    return state


def _state_corpus() -> list[dict]:
    base = _base_state()
    states = [deepcopy(base)]

    new_file = deepcopy(base)
    new_file["selectedTargetSnapshots"][0]["exists"] = False
    states.append(new_file)

    bundle = deepcopy(base)
    bundle["toolRoute"]["selectedSlice"]["files"].append(
        "Source/Demo/Feature.h"
    )
    states.append(bundle)

    static = deepcopy(base)
    static["mutationGeneration"] = 1
    static["continuity"]["checkpoint"] = {
        "mutationGeneration": 1,
        "validation": {},
    }
    states.append(static)

    build = deepcopy(static)
    build["continuity"]["checkpoint"]["validation"] = {"status": "passed"}
    states.append(build)

    contracted_build = deepcopy(build)
    contracted_build["buildContract"] = {
        "project": contracted_build["projectFile"],
        "engineRoot": str(ROOT / "fixtures" / "UE_5.5"),
        "target": "ParityProjectEditor",
        "platform": "Win64",
        "configuration": "Development",
        "allowAbsoluteProject": True,
        "allowEngineFallback": False,
    }
    states.append(contracted_build)

    automation = deepcopy(build)
    automation["buildVerification"] = {
        "status": "pending_automation",
        "testFilter": "Demo.Project",
    }
    states.append(automation)

    complete = deepcopy(automation)
    complete["status"] = "completed"
    states.append(complete)

    compile_first = deepcopy(base)
    compile_first.update(
        {
            "taskKind": "compile_fix",
            "completedGates": {},
            "buildBlocker": {},
        }
    )
    compile_first["toolRoute"].update(
        {
            "phase": "planner",
            "pendingGates": ["unreal_code_sketch_claim_validate"],
        }
    )
    states.append(compile_first)

    recovery = deepcopy(compile_first)
    recovery["buildRecovery"] = {
        "status": "evidence_required",
        "requiredNextTool": "read_file",
        "requiredNextToolArgs": {"path": "Source/Demo/Feature.cpp"},
    }
    states.append(recovery)

    discovery = deepcopy(base)
    discovery.update({"completedGates": {}, "slicePlanningRequired": True})
    discovery["toolRoute"]["phase"] = "planner"
    discovery["toolRoute"]["pendingGates"] = ["unreal_feature_intent_resolve"]
    states.append(discovery)

    pre_read = deepcopy(base)
    pre_read["completedGates"] = {}
    pre_read["writeGate"] = {"mustReadBeforeWrite": True}
    pre_read["directSourceEvidence"] = {"files": {}}
    pre_read["toolRoute"]["phase"] = "planner"
    pre_read["toolRoute"]["pendingGates"] = [
        "unreal_code_sketch_claim_validate"
    ]
    states.append(pre_read)

    recovery_tool = {
        "name": "read_file",
        "args": {"path": "project://Source/Demo/Feature.cpp"},
    }
    for recovery_status in (
        "evidence_required",
        "repair_planning_required",
        "revalidate_required",
    ):
        states.append(
            _recovery_obligation_state(
                recovery_status,
                required_tool=recovery_tool,
            )
        )
    states.append(
        _recovery_obligation_state(
            "checkpoint_rebase_required",
            required_tool={
                "name": "unreal_task_checkpoint",
                "args": {
                    "action": "rebase",
                    "acceptCurrentFiles": True,
                    "includeGitChanges": False,
                },
            },
        )
    )
    states.append(
        _recovery_obligation_state(
            "phase_budget_checkpoint_required",
            required_tool={
                "name": "unreal_task_checkpoint",
                "args": {
                    "action": "record",
                    "phase": "planner",
                    "requiredNextAction": "list_directory",
                    "includeGitChanges": False,
                },
            },
        )
    )
    states.extend(
        [
            _recovery_obligation_state("external_blocker"),
            _recovery_obligation_state("await_user"),
            _recovery_obligation_state("evidence_complete"),
            _recovery_obligation_state(
                "environment_recovery",
                required_tool=recovery_tool,
                attempt_count=1,
            ),
            _recovery_obligation_state(
                "environment_recovery",
                required_tool=recovery_tool,
                attempt_count=2,
            ),
            _recovery_obligation_state("evidence_required"),
            _recovery_obligation_state("repair_required"),
            _failed_gate_state(attempt_count=1),
            _failed_gate_state(attempt_count=2),
        ]
    )

    expired_recovery_sketch = _recovery_obligation_state("repair_required")
    expired_recovery_sketch["completedGates"] = {}
    expired_recovery_sketch["toolRoute"]["phase"] = "verifier"
    expired_recovery_sketch["toolRoute"]["pendingGates"] = [SKETCH_GATE]
    states.append(expired_recovery_sketch)

    stale_attempt = _failed_gate_state(attempt_count=2)
    stale_attempt["failedGateAttempts"][SKETCH_GATE]["activeSliceId"] = "old-slice"
    states.append(stale_attempt)

    repeated_recovery = _failed_gate_state(attempt_count=2)
    repeated_recovery["recoveryObligation"] = {
        "source": "gate-repair",
        "status": "repair_planning_required",
        "fingerprint": "recovery-would-loop",
        "requiredTool": {"name": SKETCH_GATE, "args": {}},
    }
    states.append(repeated_recovery)

    selected_snapshot_miss = deepcopy(base)
    selected_snapshot_miss["selectedTargetSnapshots"] = [
        {"path": "Source/Demo/Other.cpp", "exists": True, "fileHash": "other"}
    ]
    selected_snapshot_miss["featureTargetSnapshots"] = [
        {"path": "Source/Demo/Feature.cpp", "exists": True, "fileHash": "feature"}
    ]
    states.append(selected_snapshot_miss)

    audit_active = deepcopy(base)
    audit_active["taskKind"] = "read_only"
    audit_active["repoAuditLedger"] = {
        "required": True,
        "status": "active",
        "cursor": 1,
        "remainingCount": 1,
        "queuedTargets": ["Source/Demo/A.cpp", "Source/Demo/B.h"],
    }
    states.append(audit_active)

    audit_complete = deepcopy(audit_active)
    audit_complete["repoAuditLedger"].update(
        {"status": "complete", "cursor": 2, "remainingCount": 0}
    )
    states.append(audit_complete)

    audit_overflow = deepcopy(audit_active)
    audit_overflow["repoAuditLedger"].update({"status": "overflow"})
    states.append(audit_overflow)

    return states


def test_python_and_node_transition_projection_match_for_adversarial_corpus() -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is unavailable")
    states = _state_corpus()
    script = """
const fs = require('fs');
const { deriveNextObligation } = require('./lmstudio-unreal-agent-mcp/src/task-control-transition');
const states = JSON.parse(fs.readFileSync(0, 'utf8'));
process.stdout.write(JSON.stringify(states.map(deriveNextObligation)));
"""
    result = subprocess.run(
        [node, "-e", script],
        cwd=ROOT,
        input=json.dumps(states),
        text=True,
        capture_output=True,
        check=True,
    )
    node_controls = json.loads(result.stdout)
    python_controls = [derive_next_obligation(state) for state in states]

    assert node_controls == python_controls


def test_evidence_complete_is_a_no_tool_synthesis_transition() -> None:
    state = _recovery_obligation_state("evidence_complete")
    state.update({
        "mode": "read_only",
        "writesAllowed": False,
        "writeGate": {"writesAllowed": False},
        "taskKind": "cpp_analysis",
        "inspectionContract": {"intent": "cpp_analysis", "evidenceBudget": {"representativePairs": 1}},
        "sourceEvidence": {
            "planRevision": state["planRevision"],
            "files": {
                "header": {"path": "Source/Demo/Feature.h", "sourceKind": "declaration", "evidenceId": "header"},
                "source": {"path": "Source/Demo/Feature.cpp", "sourceKind": "implementation", "evidenceId": "source"},
            },
        },
    })
    control = derive_next_obligation(state)

    assert control["disposition"] == "continue"
    assert control["requiredTool"] is None
    assert control["allowedTools"] == []
    assert control["retryPolicy"] == {"sameSemanticInput": "forbidden"}
    assert control["blocker"] == {
        "code": "RECOVERY_EVIDENCE_COMPLETE",
        "fingerprint": "fingerprint-evidence_complete-0",
    }


def test_static_finding_recovery_is_total_and_never_emits_empty_read_args() -> None:
    cases = [
        (
            {"path": "Source/Demo/Foo.cpp", "line": 42},
            "read_file_range",
            {"path": "Source/Demo/Foo.cpp", "startLine": 22, "endLine": 62},
        ),
        (
            {"path": "Source/Demo/Foo.cpp"},
            "read_file",
            {"path": "Source/Demo/Foo.cpp"},
        ),
        (
            {"symbol": "UDemoSubsystem"},
            "unreal_symbol_lookup",
            {"query": "UDemoSubsystem", "access": "read"},
        ),
        (
            {"diagnosticSource": "build", "buildLogPath": r"C:\\Logs\\latest-build.log"},
            "read_unreal_logs",
            {
                "mode": "first_error",
                "maxFiles": 1,
                "maxLines": 200,
                "summaryOnly": True,
                "fileName": "latest-build.log",
            },
        ),
        (
            {},
            "unreal_task_checkpoint",
            {
                "action": "rebase",
                "acceptCurrentFiles": True,
                "includeGitChanges": False,
            },
        ),
    ]
    for finding, expected_name, expected_args in cases:
        _status, _scope, required, _targets = validation_finding_recovery(finding)
        assert required == {"name": expected_name, "args": expected_args}

    state = _base_state()
    state["mutationGeneration"] = 1
    state["continuity"]["checkpoint"] = {
        "mutationGeneration": 1,
        "validation": {"status": "failed", "firstFinding": {}},
    }
    control = derive_next_obligation(state)
    assert control["requiredTool"] == {
        "name": "unreal_task_checkpoint",
        "args": {
            "action": "rebase",
            "acceptCurrentFiles": True,
            "includeGitChanges": False,
        },
    }


def test_failed_gate_recovery_preserves_exact_tool_args_before_repeat_block() -> None:
    first = derive_next_obligation(_failed_gate_state(attempt_count=1))
    assert first["disposition"] == "require_tool"
    assert first["requiredTool"] == {
        "name": "read_file_range",
        "args": REPEATED_GATE_ARGS,
    }
    assert first["retryPolicy"] == {"sameSemanticInput": "once"}

    repeated = derive_next_obligation(_failed_gate_state(attempt_count=2))
    assert repeated["disposition"] == "rediscover"
    assert repeated["requiredTool"] is None
    assert repeated["retryPolicy"] == {"sameSemanticInput": "forbidden"}
    assert repeated["blocker"]["code"] == "REPEATED_GATE_BLOCKER"
    assert SKETCH_GATE not in repeated["allowedTools"]
    assert "read_file" in repeated["allowedTools"]


def test_failed_gate_attempts_are_consumed_only_in_their_exact_scope() -> None:
    for field, stale_value in (
        ("gateSetHash", "old-gate-set"),
        ("planRevision", "old-plan"),
        ("activeSliceId", "old-slice"),
        ("mutationGeneration", 99),
    ):
        state = _failed_gate_state(attempt_count=2)
        state["failedGateAttempts"][SKETCH_GATE][field] = stale_value
        control = derive_next_obligation(state)
        assert control["disposition"] == "require_tool"
        assert control["requiredTool"] == {"name": SKETCH_GATE, "args": {}}
        assert control["blocker"] is None

    unscoped = _failed_gate_state(attempt_count=2)
    for field in ("gateSetHash", "planRevision", "activeSliceId", "mutationGeneration"):
        unscoped["failedGateAttempts"][SKETCH_GATE].pop(field)
    assert derive_next_obligation(unscoped)["blocker"] is None


def test_repeated_same_gate_blocker_precedes_recovery_that_would_reinvoke_it() -> None:
    state = _failed_gate_state(attempt_count=2)
    state["recoveryObligation"] = {
        "status": "repair_planning_required",
        "fingerprint": "recovery-loop",
        "requiredTool": {"name": SKETCH_GATE, "args": {}},
    }

    control = derive_next_obligation(state)

    assert control["disposition"] == "rediscover"
    assert control["requiredTool"] is None
    assert control["blocker"] == {
        "code": "REPEATED_GATE_BLOCKER",
        "fingerprint": "same-gate-input",
    }


def test_selected_snapshot_authority_does_not_fall_through_to_feature_snapshot() -> None:
    state = _base_state()
    state["selectedTargetSnapshots"] = [
        {"path": "Source/Demo/Other.cpp", "exists": True, "fileHash": "other"}
    ]
    state["featureTargetSnapshots"] = [
        {"path": "Source/Demo/Feature.cpp", "exists": True, "fileHash": "feature"}
    ]

    assert derive_next_obligation(state)["requiredTool"] == {
        "name": "apply_edit_bundle",
        "args": {},
    }


def test_static_validation_obligation_is_bound_to_authoritative_project_root() -> None:
    state = _base_state()
    state["mutationGeneration"] = 1
    state["continuity"]["checkpoint"] = {
        "mutationGeneration": 1,
        "validation": {},
    }

    control = derive_next_obligation(state)

    assert control["requiredTool"] == {
        "name": "static_validate_project",
        "args": {
            "projectRoot": str(ROOT / "fixtures" / "ParityProject"),
            "fullAudit": False,
        },
    }


def test_python_node_transition_paths_fold_only_on_windows_and_preserve_unicode_spelling() -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is unavailable")
    route = {"selectedSlice": {"files": ["Source/Demo/Foo.cpp"]}}
    case_state = {
        "selectedTargetSnapshots": [
            {"path": "Source/Demo/foo.cpp", "exists": True, "fileHash": "lower"}
        ]
    }
    unicode_route = {"selectedSlice": {"files": ["Source/D\u00e9mo/Foo.cpp"]}}
    unicode_state = {
        "selectedTargetSnapshots": [
            {"path": "Source/De\u0301mo/Foo.cpp", "exists": True, "fileHash": "nfc"}
        ]
    }
    idot_route = {"selectedSlice": {"files": ["Source/\u0130/Foo.cpp"]}}
    idot_state = {
        "selectedTargetSnapshots": [
            {"path": "Source/i\u0307/Foo.cpp", "exists": True, "fileHash": "idot"}
        ]
    }
    read_state = {
        "writeGate": {"mustReadBeforeWrite": True},
        "selectedTargetSnapshots": [
            {"path": "Source/Demo/Foo.cpp", "exists": True, "fileHash": "upper"}
        ],
        "directSourceEvidence": {
            "files": {
                "Source/Demo/foo.cpp": {"path": "Source/Demo/foo.cpp"},
            }
        },
    }
    python_results = {
        "mutationLinux": _mutation_tool_for_state(
            case_state, route, host_platform="linux"
        ),
        "mutationWindows": _mutation_tool_for_state(
            case_state, route, host_platform="win32"
        ),
        "unicodeLinux": _mutation_tool_for_state(
            unicode_state, unicode_route, host_platform="linux"
        ),
        "idotWindows": _mutation_tool_for_state(
            idot_state, idot_route, host_platform="win32"
        ),
        "readLinux": _pre_gate_source_read_path(
            read_state,
            [SKETCH_GATE],
            host_platform="linux",
        ),
        "readWindows": _pre_gate_source_read_path(
            read_state,
            [SKETCH_GATE],
            host_platform="win32",
        ),
    }
    script = """
const fs = require('fs');
const {
  mutationToolForState,
  preGateSourceReadPath,
} = require('./lmstudio-unreal-agent-mcp/src/task-control-transition');
const payload = JSON.parse(fs.readFileSync(0, 'utf8'));
process.stdout.write(JSON.stringify({
  mutationLinux: mutationToolForState(payload.caseState, payload.route, 'linux'),
  mutationWindows: mutationToolForState(payload.caseState, payload.route, 'win32'),
  unicodeLinux: mutationToolForState(payload.unicodeState, payload.unicodeRoute, 'linux'),
  idotWindows: mutationToolForState(payload.idotState, payload.idotRoute, 'win32'),
  readLinux: preGateSourceReadPath(payload.readState, [payload.gate], 'linux'),
  readWindows: preGateSourceReadPath(payload.readState, [payload.gate], 'win32'),
}));
"""
    result = subprocess.run(
        [node, "-e", script],
        cwd=ROOT,
        input=json.dumps(
            {
                "caseState": case_state,
                "route": route,
                "unicodeState": unicode_state,
                "unicodeRoute": unicode_route,
                "idotState": idot_state,
                "idotRoute": idot_route,
                "readState": read_state,
                "gate": SKETCH_GATE,
            }
        ),
        text=True,
        capture_output=True,
        check=True,
    )
    node_results = json.loads(result.stdout)

    assert python_results == node_results == {
        "mutationLinux": "apply_edit_bundle",
        "mutationWindows": "replace_in_file",
        "unicodeLinux": "apply_edit_bundle",
        "idotWindows": "apply_edit_bundle",
        "readLinux": "Source/Demo/Foo.cpp",
        "readWindows": "",
    }


def test_python_node_required_args_subset_and_path_identity_are_in_parity() -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is unavailable")
    cases = [
        {
            "host": "win32",
            "expected": {
                "projectRoot": r"C:\Work\Demo",
                "engineRoot": r"D:\UE_5.6",
                "project": r"C:\Work\Demo\Demo.uproject",
                "buildLogPath": r"C:\Work\Demo\Saved\Build.log",
                "targetFiles": [r"Source\Demo\Foo.cpp"],
            },
            "observed": {
                "projectRoot": "c:/work/demo",
                "engineRoot": "d:/ue_5.6",
                "project": "c:/work/demo/demo.uproject",
                "buildLogPath": "c:/work/demo/saved/build.log",
                "targetFiles": ["source/demo/foo.cpp"],
                "extraDiagnostic": True,
            },
            "matches": True,
        },
        {
            "host": "linux",
            "expected": {"projectRoot": "/work/Demo", "buildLogPath": "/work/Demo/Build.log"},
            "observed": {"projectRoot": "/work/demo", "buildLogPath": "/work/Demo/Build.log"},
            "matches": False,
        },
        {
            "host": "linux",
            "expected": {"project": "Demo", "path": "project://Source/Demo/Foo.cpp"},
            "observed": {"project": "Demo", "path": "Source/Demo/Foo.cpp", "encoding": "utf-8"},
            "matches": True,
        },
        {
            "host": "win32",
            "expected": {"path": "Source/Demo/Foo.cpp", "startLine": 4},
            "observed": {"path": "Source/Demo/Foo.cpp"},
            "matches": False,
        },
        {
            "host": "linux",
            "expected": {"testFilter": ""},
            "observed": {},
            "matches": False,
        },
        {
            "host": "linux",
            "expected": {"path": "Source/D\u00e9mo/Foo.cpp"},
            "observed": {"path": "Source/De\u0301mo/Foo.cpp"},
            "matches": False,
        },
        {
            "host": "win32",
            "expected": {"path": "Source/\u0130/Foo.cpp"},
            "observed": {"path": "Source/i\u0307/Foo.cpp"},
            "matches": False,
        },
    ]
    python_results = [
        _control_args_match(
            case["expected"],
            case["observed"],
            host_platform=case["host"],
        )
        for case in cases
    ]
    script = """
const fs = require('fs');
const { controlArgsMatch } = require('./lmstudio-unreal-agent-mcp/src/task-auth');
const cases = JSON.parse(fs.readFileSync(0, 'utf8'));
process.stdout.write(JSON.stringify(cases.map((item) => (
  controlArgsMatch(item.expected, item.observed, '', item.host)
))));
"""
    result = subprocess.run(
        [node, "-e", script],
        cwd=ROOT,
        input=json.dumps(cases),
        text=True,
        capture_output=True,
        check=True,
    )
    node_results = json.loads(result.stdout)

    assert python_results == node_results == [case["matches"] for case in cases]


def test_retry_policy_only_transition_advances_epoch_once_in_python_and_node() -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is unavailable")
    state = deepcopy(_base_state())
    state["completedGates"] = {}
    state["toolRoute"]["phase"] = "planner"
    state["toolRoute"]["pendingGates"] = [SKETCH_GATE]
    failed_attempt = {
        SKETCH_GATE: {
            "attemptCount": 1,
            "fingerprint": "same-gate-input",
            "gateSetHash": state["requiredGateSetHash"],
            "planRevision": state["planRevision"],
            "activeSliceId": state["activeSliceId"],
            "mutationGeneration": state["mutationGeneration"],
            # Keeping the recovery tool equal to the pending gate means the
            # semantic transition changes retryPolicy and nothing else.
            "nextAction": SKETCH_GATE,
            "nextActionIsTool": True,
            "nextActionArgs": {},
        }
    }

    python_state = deepcopy(state)
    commit_control_transition(python_state)
    python_snapshots = [deepcopy(python_state["controlState"])]
    python_state["failedGateAttempts"] = deepcopy(failed_attempt)
    commit_control_transition(python_state)
    python_snapshots.append(deepcopy(python_state["controlState"]))
    commit_control_transition(python_state)
    python_snapshots.append(deepcopy(python_state["controlState"]))

    script = """
const fs = require('fs');
const { commitControlTransition } = require('./lmstudio-unreal-agent-mcp/src/task-control-transition');
const payload = JSON.parse(fs.readFileSync(0, 'utf8'));
const value = payload.state;
commitControlTransition(value);
const snapshots = [JSON.parse(JSON.stringify(value.controlState))];
value.failedGateAttempts = payload.failedGateAttempts;
commitControlTransition(value);
snapshots.push(JSON.parse(JSON.stringify(value.controlState)));
commitControlTransition(value);
snapshots.push(JSON.parse(JSON.stringify(value.controlState)));
process.stdout.write(JSON.stringify(snapshots));
"""
    result = subprocess.run(
        [node, "-e", script],
        cwd=ROOT,
        input=json.dumps({"state": state, "failedGateAttempts": failed_attempt}),
        text=True,
        capture_output=True,
        check=True,
    )
    node_snapshots = json.loads(result.stdout)

    assert node_snapshots == python_snapshots
    first, changed, stable = python_snapshots
    assert first["requiredTool"] == changed["requiredTool"] == {
        "name": SKETCH_GATE,
        "args": {},
    }
    assert first["retryPolicy"] == {"sameSemanticInput": "allowed"}
    assert changed["retryPolicy"] == {"sameSemanticInput": "once"}
    assert changed["epoch"] == first["epoch"] + 1
    assert changed["fingerprint"] != first["fingerprint"]
    assert stable == changed

    first_material = {
        key: value
        for key, value in first.items()
        if key not in {"epoch", "fingerprint", "retryPolicy"}
    }
    changed_material = {
        key: value
        for key, value in changed.items()
        if key not in {"epoch", "fingerprint", "retryPolicy"}
    }
    for material in (first_material, changed_material):
        readiness = material.get("synthesisReadiness")
        if isinstance(readiness, dict):
            readiness.pop("controlEpoch", None)
        latch = material.get("synthesisLatch")
        if isinstance(latch, dict):
            latch.pop("controlEpoch", None)
    assert changed_material == first_material
