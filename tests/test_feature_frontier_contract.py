from __future__ import annotations

import hashlib
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from feature_frontier_contract import (  # noqa: E402
    is_completion_audit_request,
    validate_feature_frontier,
)


def _source_ledger(project: Path, target: Path) -> tuple[dict, str]:
    evidence_id = "source-evidence-1"
    relative = target.relative_to(project).as_posix()
    return (
        {
            "version": 2,
            "planRevision": "1",
            "files": {
                relative.casefold(): {
                    "evidenceId": evidence_id,
                    "path": relative,
                    "contentHash": hashlib.sha256(target.read_bytes()).hexdigest(),
                    "coveredRanges": [[1, 40]],
                }
            },
            "absentEvidence": {"version": 1, "planRevision": "1", "files": {}},
        },
        evidence_id,
    )


def test_completion_audit_detection_is_narrow() -> None:
    assert is_completion_audit_request("Find all missing implementations and finish every branch")
    assert is_completion_audit_request("빠진 기능을 모두 구현하고 끝까지 검증해줘")
    assert not is_completion_audit_request("Add one null guard to this function")
    assert not is_completion_audit_request("모든 패치를 적용하고 각 OS에서 전부 검증해줘")
    assert not is_completion_audit_request("Finish all requested fixes and run every test")


def test_typed_call_edge_claim_requires_current_server_evidence(tmp_path: Path) -> None:
    project = tmp_path / "DemoProject"
    target = project / "Source" / "Demo" / "RuleEngine.cpp"
    target.parent.mkdir(parents=True)
    target.write_text("void FRuleEngine::Initialize() {}\n", encoding="utf-8")
    ledger, evidence_id = _source_ledger(project, target)
    claim = {
        "claimType": "missing_call_edge",
        "subjectSymbol": "FRuleEngine::BeginPlay",
        "objectSymbol": "FRuleEngine::Initialize",
        "evidenceRefs": [evidence_id],
        "statement": "may not fully initialize",
    }

    validated = validate_feature_frontier(
        [claim],
        project_root=project,
        evidence_ledger=ledger,
    )
    assert validated["ok"] is True, validated
    assert validated["claims"][0]["claimType"] == "missing_call_edge"
    assert "statement" not in validated["claims"][0]

    target.write_text("void FRuleEngine::Initialize() { /* changed */ }\n", encoding="utf-8")
    stale = validate_feature_frontier(
        [claim],
        project_root=project,
        evidence_ledger=ledger,
    )
    assert stale["ok"] is False
    assert "stale or uncovered" in stale["issues"][0]


def test_missing_file_claim_requires_complete_matching_absence(tmp_path: Path) -> None:
    project = tmp_path / "PortableProject"
    project.mkdir()
    path = "Source/Portable/MissingRule.cpp"
    claim = {
        "claimType": "missing_file",
        "path": path,
        "evidenceRefs": ["absent-evidence-1"],
    }
    ledger = {
        "version": 2,
        "planRevision": "2",
        "files": {},
        "absentEvidence": {
            "version": 1,
            "planRevision": "2",
            "files": {
                path.casefold(): {
                    "evidenceId": "absent-evidence-1",
                    "path": path,
                    "searchComplete": False,
                }
            },
        },
    }
    incomplete = validate_feature_frontier(
        [claim], project_root=project, evidence_ledger=ledger
    )
    assert incomplete["ok"] is False
    assert "searchComplete=true" in incomplete["issues"][0]

    ledger["absentEvidence"]["files"][path.casefold()]["searchComplete"] = True
    complete = validate_feature_frontier(
        [claim], project_root=project, evidence_ledger=ledger
    )
    assert complete["ok"] is True, complete


def test_rc2_replay_f_free_text_or_missing_claims_never_open_completion_frontier(
    tmp_path: Path,
) -> None:
    result = validate_feature_frontier(
        [],
        project_root=tmp_path,
        evidence_ledger={},
    )
    assert result["ok"] is False
    assert result["errorCode"] == "FEATURE_FRONTIER_TYPED_CLAIMS_REQUIRED"
