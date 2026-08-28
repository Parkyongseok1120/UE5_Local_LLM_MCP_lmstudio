from __future__ import annotations

import importlib.util
import sys
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
    sys.path.insert(0, str(VALIDATOR.parent))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(VALIDATOR.parent))
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


def test_portable_validator_rejects_schema_invalid_scalar_types() -> None:
    validator = _load_validator()
    packet = _neutral_architecture_packet()
    packet["claims"][0]["claim"] = 123
    packet["claims"][0]["evidence"][0]["location"] = 42
    packet["claims"][0]["evidence"][0]["observation"] = True

    result = validator.validate_packet(packet)

    assert result["ok"] is False
    assert any("claim must be a non-empty string" in error for error in result["errors"])
    assert any("location must be a non-empty string" in error for error in result["errors"])
    assert any("observation must be a non-empty string" in error for error in result["errors"])


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


def _neutral_architecture_packet(claim_type: str = "architecture") -> dict:
    claim = _valid_claim()
    claim.update(
        {
            "claim": "The existing state owner is source verified without inferring design intent.",
            "claimType": claim_type,
            "verdict": "Confirmed",
            "severity": "Info",
            "counterEvidence": [],
        }
    )
    return {
        "mode": "architecture",
        "claims": [claim],
        "existing": ["The existing state owner remains authoritative."],
        "proposed": [],
        "doNotDuplicate": [],
    }


def test_portable_validator_accepts_verified_neutral_as_built_architecture_facts() -> None:
    validator = _load_validator()

    architecture_result = validator.validate_packet(_neutral_architecture_packet())
    behavior_result = validator.validate_packet(_neutral_architecture_packet("behavior"))

    assert architecture_result["ok"] is True
    assert behavior_result["ok"] is True


def test_portable_validator_keeps_confirmed_info_narrow_and_paired() -> None:
    validator = _load_validator()

    mismatched = _neutral_architecture_packet()
    mismatched["claims"][0]["severity"] = "P3"
    mismatched_result = validator.validate_packet(mismatched)
    assert mismatched_result["ok"] is False
    assert any("must be used together" in error for error in mismatched_result["errors"])

    info_without_confirmed = _neutral_architecture_packet()
    info_without_confirmed["claims"][0]["verdict"] = "ByDesign"
    info_result = validator.validate_packet(info_without_confirmed)
    assert info_result["ok"] is False
    assert any("must be used together" in error for error in info_result["errors"])

    outside_architecture = _neutral_architecture_packet()
    outside_architecture["mode"] = "audit"
    outside_result = validator.validate_packet(outside_architecture)
    assert outside_result["ok"] is False
    assert any("architecture mode" in error for error in outside_result["errors"])

    proposed = _neutral_architecture_packet()
    proposed["claims"][0]["proofLevel"] = "Proposed"
    proposed_result = validator.validate_packet(proposed)
    assert proposed_result["ok"] is False
    assert any("verified proofLevel" in error for error in proposed_result["errors"])

    codegen_claim = _neutral_architecture_packet("codegen")
    codegen_claim["claims"][0]["behaviorPath"] = []
    codegen_result = validator.validate_packet(codegen_claim)
    assert codegen_result["ok"] is False
    assert any("non-codegen" in error for error in codegen_result["errors"])


def test_portable_validator_requires_architecture_arrays_but_allows_no_change_report() -> None:
    validator = _load_validator()
    packet = _neutral_architecture_packet()
    assert validator.validate_packet(packet)["ok"] is True

    missing_proposed = _neutral_architecture_packet()
    missing_proposed.pop("proposed")
    missing_result = validator.validate_packet(missing_proposed)
    assert missing_result["ok"] is False
    assert any("proposed array" in error for error in missing_result["errors"])

    empty_existing = _neutral_architecture_packet()
    empty_existing["existing"] = []
    existing_result = validator.validate_packet(empty_existing)
    assert existing_result["ok"] is False
    assert any("non-empty existing" in error for error in existing_result["errors"])
