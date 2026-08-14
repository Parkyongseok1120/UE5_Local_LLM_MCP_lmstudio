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

from phase_tool_router import derive_next_obligation  # noqa: E402


def _base_state() -> dict:
    return {
        "taskSessionId": "parity-task",
        "status": "running",
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
                "search_files",
                "unreal_code_sketch_claim_validate",
                "unreal_feature_intent_resolve",
            ],
        },
    }


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
