from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = (
    ROOT
    / "skills"
    / "evidence-first-code-audit"
    / "scripts"
    / "validate_evidence_packet.py"
)


def _load_validator():
    spec = importlib.util.spec_from_file_location("evidence_packet_validator", VALIDATOR)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _valid_claim() -> dict:
    return {
        "claim": "The request reaches the final state mutation.",
        "verdict": "Bug",
        "severity": "P1",
        "proofLevel": "SourceVerified",
        "claimType": "wiring",
        "evidence": [
            {
                "kind": "project_source",
                "location": "src/handler.py:10",
                "observation": "The handler dispatches to the state owner.",
            }
        ],
        "behaviorPath": [
            {"stage": "entry", "stageStatus": "present", "location": "src/api.py:5", "symbol": "request"},
            {"stage": "decision", "stageStatus": "present", "location": "src/handler.py:10", "symbol": "validate"},
            {"stage": "mutation", "stageStatus": "present", "location": "src/state.py:20", "symbol": "apply"},
        ],
        "counterEvidence": [
            {
                "kind": "test",
                "location": "tests/test_state.py:30",
                "observation": "The failure path was checked separately.",
            }
        ],
        "unknowns": [],
    }


def test_portable_validator_accepts_complete_audit_packet() -> None:
    validator = _load_validator()
    result = validator.validate_packet({"mode": "audit", "claims": [_valid_claim()]})
    assert result["ok"] is True
    assert result["errors"] == []


def test_portable_validator_rejects_unverified_framework_and_incomplete_wiring() -> None:
    validator = _load_validator()
    framework_claim = _valid_claim()
    framework_claim["claimType"] = "framework_semantics"
    framework_result = validator.validate_packet(
        {"mode": "audit", "claims": [framework_claim]}
    )
    assert framework_result["ok"] is False
    assert any("framework_source" in error for error in framework_result["errors"])

    wiring_claim = _valid_claim()
    wiring_claim["behaviorPath"] = wiring_claim["behaviorPath"][:2]
    wiring_result = validator.validate_packet({"mode": "audit", "claims": [wiring_claim]})
    assert wiring_result["ok"] is False
    assert any("three behaviorPath" in error for error in wiring_result["errors"])


def test_portable_validator_enforces_codegen_obligations() -> None:
    validator = _load_validator()
    result = validator.validate_packet({"mode": "codegen", "claims": [_valid_claim()]})
    assert result["ok"] is False
    assert any("invariants" in error for error in result["errors"])
    assert any("impactedSurfaces" in error for error in result["errors"])
    assert any("validationPlan" in error for error in result["errors"])


def test_portable_validator_requires_explicit_claim_type_and_unknowns() -> None:
    validator = _load_validator()
    claim = _valid_claim()
    claim.pop("claimType")
    claim.pop("unknowns")
    result = validator.validate_packet({"mode": "audit", "claims": [claim]})
    assert result["ok"] is False
    assert any("claimType" in error for error in result["errors"])
    assert any("unknowns" in error for error in result["errors"])


def test_portable_validator_rejects_proof_evidence_mismatch() -> None:
    validator = _load_validator()
    claim = _valid_claim()
    claim["proofLevel"] = "BuildVerified"
    result = validator.validate_packet({"mode": "audit", "claims": [claim]})
    assert result["ok"] is False
    assert any("BuildVerified" in error and "build" in error for error in result["errors"])


def test_portable_validator_accepts_proposed_codegen_with_requirement_evidence() -> None:
    validator = _load_validator()
    claim = _valid_claim()
    claim.update(
        {
            "claim": "The proposed adapter preserves the existing storage owner.",
            "claimType": "codegen",
            "severity": "P2",
            "proofLevel": "Proposed",
            "evidence": [
                {
                    "kind": "requirement",
                    "location": "request:1",
                    "observation": "The requested change must reuse the existing owner.",
                }
            ],
            "behaviorPath": [],
            "counterEvidence": [],
        }
    )
    packet = {
        "mode": "codegen",
        "claims": [claim],
        "invariants": ["The existing storage owner remains authoritative."],
        "impactedSurfaces": ["adapter", "tests"],
        "validationPlan": ["Run the adapter contract tests."],
    }
    result = validator.validate_packet(packet)
    assert result["ok"] is True


def test_portable_validator_rejects_out_of_order_behavior_path() -> None:
    validator = _load_validator()
    claim = _valid_claim()
    claim["behaviorPath"] = [
        claim["behaviorPath"][2],
        claim["behaviorPath"][0],
        claim["behaviorPath"][1],
    ]
    result = validator.validate_packet({"mode": "audit", "claims": [claim]})
    assert result["ok"] is False
    assert any("must order entry" in error for error in result["errors"])


def test_portable_validator_rejects_wrong_container_shapes() -> None:
    validator = _load_validator()
    claim = _valid_claim()
    claim["counterEvidence"] = {}
    claim["unknowns"] = [""]
    result = validator.validate_packet({"mode": "audit", "claims": [claim]})
    assert result["ok"] is False
    assert any("counterEvidence must be an array" in error for error in result["errors"])
    assert any("unknowns[0]" in error for error in result["errors"])


def test_portable_validator_distinguishes_missing_and_unknown_path_stages() -> None:
    validator = _load_validator()
    missing_claim = _valid_claim()
    missing_claim["behaviorPath"][2]["stageStatus"] = "expected_missing"
    missing_result = validator.validate_packet({"mode": "audit", "claims": [missing_claim]})
    assert missing_result["ok"] is True

    unknown_claim = _valid_claim()
    unknown_claim["behaviorPath"][2]["stageStatus"] = "unknown"
    unknown_result = validator.validate_packet({"mode": "audit", "claims": [unknown_claim]})
    assert unknown_result["ok"] is False
    assert any("Unknown" in error or "unknown" in error for error in unknown_result["errors"])
