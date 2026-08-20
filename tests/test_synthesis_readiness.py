from __future__ import annotations

import json
import hashlib
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT / "scripts"))

from synthesis_readiness import derive_synthesis_readiness, synthesis_latch_matches  # noqa: E402
def _complete(path: str, kind: str, evidence_id: str, content_hash: str, text: str):
    return {
        "path": path,
        "sourceKind": kind,
        "evidenceId": evidence_id,
        "contentHash": content_hash,
        "evidenceSnapshotGeneration": 0,
        "coveredRanges": [[1, 3]],
        "wholeFileComplete": True,
        "truncated": False,
        "lineCount": 3,
        "coverageLevel": "FILE_COMPLETE",
        "supportingExcerpts": [{
            "startLine": 1,
            "endLine": 3,
            "text": text,
            "excerptDigest": hashlib.sha256(text.encode()).hexdigest(),
        }],
    }


DECL = _complete("Source/Cinematic/Public/CinematicSystem.h", "declaration", "decl-1", "a" * 64, "class FCinematicSystem {};")
IMPL = _complete("Source/Cinematic/Private/CinematicSystem.cpp", "implementation", "impl-1", "b" * 64, "void FCinematicSystem::Tick() {}")


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
        "synthesisEvidenceBundleHash": readiness["synthesisEvidenceBundleHash"],
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
        "synthesisEvidenceBundleHash": readiness["synthesisEvidenceBundleHash"],
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


def test_coverage_extension_changes_evidence_state_hash():
    partial = dict(IMPL)
    partial.update({
        "coveredRanges": [[1, 2]],
        "wholeFileComplete": False,
        "coverageLevel": "RANGE_PARTIAL",
    })
    before = derive_synthesis_readiness(_state({DECL["path"]: DECL, IMPL["path"]: partial}))
    after = derive_synthesis_readiness(_state({DECL["path"]: DECL, IMPL["path"]: IMPL}))
    assert before["evidenceStateHash"] != after["evidenceStateHash"]
    assert before["acceptedEvidenceHash"] != after["acceptedEvidenceHash"]


def test_truncated_file_does_not_satisfy_file_complete_contract():
    truncated = dict(IMPL)
    truncated["truncated"] = True
    result = derive_synthesis_readiness(_state({DECL["path"]: DECL, IMPL["path"]: truncated}))
    assert result["ready"] is False
    assert result["implementationCount"] == 0


def test_materialized_bundle_is_bound_to_ready_control():
    result = derive_synthesis_readiness(_state({DECL["path"]: DECL, IMPL["path"]: IMPL}))
    assert result["ready"] is True
    assert len(result["synthesisEvidenceBundle"]["records"]) == 2
    assert result["synthesisEvidenceBundle"]["bundleHash"] == result["synthesisEvidenceBundleHash"]


def test_four_required_pairs_are_all_materialized_within_prompt_capacity() -> None:
    files = {}
    for index in range(4):
        declaration_path = f"Source/Boundary/Public/Item{index}.h"
        implementation_path = f"Source/Boundary/Private/Item{index}.cpp"
        files[declaration_path] = _complete(
            declaration_path,
            "declaration",
            f"decl-{index}",
            hashlib.sha256(f"decl-{index}".encode()).hexdigest(),
            f"DECL_{index}_" + "D" * 3900,
        )
        files[implementation_path] = _complete(
            implementation_path,
            "implementation",
            f"impl-{index}",
            hashlib.sha256(f"impl-{index}".encode()).hexdigest(),
            f"IMPL_{index}_" + "I" * 3900,
        )
    result = derive_synthesis_readiness(_state(
        files,
        taskSessionId="four-pair-task",
        objectiveHash="e" * 64,
        inspectionContract={
            "intent": "cpp_analysis",
            "coverageMode": "representative",
            "evidenceBudget": {"representativePairs": 4},
        },
        inspectionProgress={
            "discoveryStarted": True,
            "discoveredRelevantPairs": 4,
            "remainingFrontier": [],
        },
    ))
    assert result["ready"] is True
    assert result["coverageIncomplete"] is False
    assert result["requiredRepresentativePairs"] == 4
    assert result["selectedSynthesisRepresentativePairCount"] == 4
    assert len(result["synthesisEvidenceBundle"]["records"]) == 8
    assert result["synthesisEvidenceBundle"]["serializedCharacterCount"] <= 12_000


@pytest.mark.parametrize("accepted_count", [0, 1, 2, 15, 16, 17, 31, 32, 33])
def test_selected_claim_materialization_does_not_require_every_accepted_file(
    accepted_count: int,
) -> None:
    files = {}
    for index in range(accepted_count):
        pair = index // 2
        declaration = index % 2 == 0
        suffix = "h" if declaration else "cpp"
        folder = "Public" if declaration else "Private"
        kind = "declaration" if declaration else "implementation"
        path = f"Source/Boundary/{folder}/Item{pair}.{suffix}"
        files[path] = _complete(
            path,
            kind,
            f"evidence-{index}",
            hashlib.sha256(str(index).encode()).hexdigest(),
            f"PROMPT_BOUNDARY_SENTINEL_{index}",
        )

    result = derive_synthesis_readiness(_state(files, taskSessionId="boundary-task", objectiveHash="f" * 64))
    assert result["acceptedDirectEvidenceCount"] == accepted_count
    assert result["synthesisEvidenceBundle"]["serializedCharacterCount"] <= 12_000
    assert hashlib.sha256(
        result["synthesisEvidenceBundle"]["serializedEvidence"].encode("utf-8")
    ).hexdigest() == result["synthesisEvidenceBundleHash"]
    if accepted_count < 2:
        assert result["ready"] is False
    else:
        assert result["ready"] is True
        assert result["synthesisEvidenceMaterialized"] is True
        assert 2 <= result["selectedSynthesisEvidenceCount"] <= 16


def test_oversized_claim_excerpt_is_not_silently_truncated() -> None:
    oversized = dict(IMPL)
    oversized["supportingExcerpts"] = [{
        "startLine": 1,
        "endLine": 3,
        "text": "x" * 4001,
    }]
    result = derive_synthesis_readiness(_state({DECL["path"]: DECL, IMPL["path"]: oversized}))
    assert result["ready"] is False
    assert result["reason"] == "synthesis_evidence_not_materialized"
