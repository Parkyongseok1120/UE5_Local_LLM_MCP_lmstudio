from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT / "scripts"))

from synthesis_readiness import derive_synthesis_readiness, synthesis_latch_matches  # noqa: E402
DECL = {
    "path": "Source/Cinematic/Public/CinematicSystem.h",
    "sourceKind": "declaration",
    "evidenceId": "decl-1",
}
IMPL = {
    "path": "Source/Cinematic/Private/CinematicSystem.cpp",
    "sourceKind": "implementation",
    "evidenceId": "impl-1",
}


def _state(files=None, **overrides):
    value = {
        "mode": "read_only",
        "writesAllowed": False,
        "writeGate": {"writesAllowed": False},
        "taskKind": "cpp_analysis",
        "planRevision": "plan-7",
        "controlEpoch": 4,
        "inspectionContract": {
            "intent": "cpp_analysis",
            "coverageMode": "representative",
            "evidenceBudget": {"representativePairs": 1},
        },
        "sourceEvidence": {"planRevision": "plan-7", "files": files or {}},
    }
    value.update(overrides)
    return value


def test_zero_search_only_header_only_and_pair_readiness():
    assert derive_synthesis_readiness(_state())["reason"] == "direct_source_evidence_missing"
    search_only = derive_synthesis_readiness(_state(
        inspectionProgress={"remainingFrontier": [DECL["path"], IMPL["path"]]}
    ))
    assert search_only["ready"] is False
    assert search_only["acceptedDirectEvidenceCount"] == 0
    assert derive_synthesis_readiness(_state({DECL["path"]: DECL}))["ready"] is False
    pair = derive_synthesis_readiness(_state({DECL["path"]: DECL, IMPL["path"]: IMPL}))
    assert pair["ready"] is True
    assert pair["representativePairCount"] == 1


def test_legacy_read_only_state_without_task_kind_fails_closed():
    result = derive_synthesis_readiness(_state(taskKind="", inspectionContract={}))
    assert result["ready"] is False
    assert result["reason"] == "direct_source_evidence_missing"


def test_latch_rejects_stale_epoch_and_plan():
    value = _state({DECL["path"]: DECL, IMPL["path"]: IMPL})
    readiness = derive_synthesis_readiness(value)
    value["postBudgetAction"] = {
        "name": "synthesize_current_evidence",
        "controlEpoch": value["controlEpoch"],
        "planRevision": value["planRevision"],
        "acceptedEvidenceHash": readiness["acceptedEvidenceHash"],
        "remainingFrontierHash": readiness["remainingFrontierHash"],
    }
    assert synthesis_latch_matches(value, readiness) is True
    value["planRevision"] = "plan-8"
    assert synthesis_latch_matches(value) is False


def test_latch_accepts_zero_epoch_and_rejects_invalid_epochs():
    value = _state({DECL["path"]: DECL, IMPL["path"]: IMPL}, controlEpoch=0)
    readiness = derive_synthesis_readiness(value)
    value["postBudgetAction"] = {
        "name": "synthesize_current_evidence",
        "controlEpoch": 0,
        "planRevision": value["planRevision"],
        "acceptedEvidenceHash": readiness["acceptedEvidenceHash"],
        "remainingFrontierHash": readiness["remainingFrontierHash"],
    }
    assert synthesis_latch_matches(value, readiness) is True
    value["postBudgetAction"]["controlEpoch"] = -1
    assert synthesis_latch_matches(value, readiness) is False
    value["postBudgetAction"]["controlEpoch"] = "invalid"
    assert synthesis_latch_matches(value, readiness) is False


def test_same_basename_in_different_unreal_module_is_not_a_pair():
    foreign = {
        "path": "Plugins/Other/Source/OtherRuntime/Private/CinematicSystem.cpp",
        "sourceKind": "implementation",
        "evidenceId": "foreign-impl",
    }
    result = derive_synthesis_readiness(_state({DECL["path"]: DECL, foreign["path"]: foreign}))
    assert result["representativePairCount"] == 0
    assert result["ready"] is False


def test_node_and_python_readiness_are_identical():
    value = _state(
        {DECL["path"]: DECL, IMPL["path"]: IMPL},
        inspectionProgress={"remainingFrontier": ["Source/한글/Next.cpp"]},
    )
    script = """
const fs = require('fs');
const {deriveSynthesisReadiness} = require('./lmstudio-unreal-agent-mcp/src/synthesis-readiness');
process.stdout.write(JSON.stringify(deriveSynthesisReadiness(JSON.parse(fs.readFileSync(0, 'utf8')))));
"""
    completed = subprocess.run(
        ["node", "-e", script], cwd=ROOT, input=json.dumps(value, ensure_ascii=False),
        text=True, encoding="utf-8", capture_output=True, check=True,
    )
    assert json.loads(completed.stdout) == derive_synthesis_readiness(value)
