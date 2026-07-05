from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import architecture_claim_validate as validator  # noqa: E402


def _arch() -> dict:
    return {
        "schemaVersion": 1,
        "project": {"name": "DemoGame"},
        "modules": [{"name": "DemoGame"}],
        "types": [
            {
                "name": "UDemoCombatComponent",
                "kind": "UCLASS",
                "module": "DemoGame",
                "header": "Source/DemoGame/Public/DemoCombatComponent.h",
                "cpp": "Source/DemoGame/Private/DemoCombatComponent.cpp",
                "category": "ActorComponent",
                "reflectedSurface": {
                    "properties": [{"name": "CurrentComboIndex", "specifiers": ["VisibleAnywhere", "BlueprintReadOnly"]}],
                    "functions": [
                        {"name": "StartAttack", "specifiers": ["BlueprintCallable"]},
                        {"name": "CanStartAttack", "specifiers": ["BlueprintNativeEvent"]},
                    ],
                },
                "memberEvidence": {
                    "variables": [{"name": "InternalComboSeed"}],
                    "methods": [{"name": "ResetCombo"}],
                },
                "riskFlags": [
                    "blueprint_facing_surface",
                    "blueprint_event_surface",
                    "blueprint_native_event_surface",
                    "reflected_serialized_surface",
                    "possible_asset_reference",
                ],
            },
            {
                "name": "UEmptyComponent",
                "kind": "UCLASS",
                "module": "DemoGame",
                "header": "Source/DemoGame/Public/EmptyComponent.h",
                "cpp": "",
                "category": "ActorComponent",
                "reflectedSurface": {"properties": [], "functions": []},
                "memberEvidence": {"variables": [], "methods": []},
                "riskFlags": ["missing_cpp_pair"],
            },
        ],
    }


def test_supported_ownership_claim_validates() -> None:
    payload = validator.validate_claims_payload(
        _arch(),
        {
            "claims": [
                {
                    "claim": "UDemoCombatComponent owns combo state",
                    "type": "ownership",
                    "subject": "UDemoCombatComponent",
                    "requiredEvidence": ["reflected property", "function", "cpp pair"],
                }
            ]
        },
    )

    assert payload["ok"] is True
    assert payload["results"][0]["confidence"] == "medium"


def test_unknown_subject_is_rejected() -> None:
    payload = validator.validate_claims_payload(
        _arch(),
        {"claims": [{"claim": "UMissing owns combat", "type": "ownership", "subject": "UMissing"}]},
    )

    assert payload["ok"] is False
    assert "missing subject: UMissing" in payload["results"][0]["issues"]


def test_unprefixed_subject_resolves_when_unique() -> None:
    payload = validator.validate_claims_payload(
        _arch(),
        {
            "claims": [
                {
                    "claim": "CombatComponent owns combo state",
                    "type": "ownership",
                    "subject": "DemoCombatComponent",
                    "requiredEvidence": ["reflected property"],
                }
            ]
        },
    )

    assert payload["ok"] is True
    assert payload["results"][0]["resolvedSubject"] == "UDemoCombatComponent"


def test_reflected_rename_and_blueprint_risk_are_flagged() -> None:
    payload = validator.validate_claims_payload(
        _arch(),
        {
            "claims": [
                {
                    "claim": "Rename StartAttack",
                    "type": "risk",
                    "subject": "UDemoCombatComponent",
                    "changeType": "rename",
                    "requiredEvidence": ["Blueprint-facing risk"],
                    "riskIfChanged": ["rename may break Blueprint references"],
                }
            ]
        },
    )

    warnings = payload["results"][0]["warnings"]
    assert any("reflected rename risk" in item for item in warnings)
    assert any("Blueprint-facing change risk" in item for item in warnings)
    assert any("Blueprint event implementation/override validation required" in item for item in warnings)
    assert any("asset/reference validation required" in item for item in warnings)


def test_missing_required_evidence_is_reported() -> None:
    payload = validator.validate_claims_payload(
        _arch(),
        {
            "claims": [
                {
                    "claim": "UEmptyComponent owns state",
                    "type": "ownership",
                    "subject": "UEmptyComponent",
                    "requiredEvidence": ["reflected property", "function", "cpp pair"],
                }
            ]
        },
    )

    result = payload["results"][0]
    assert result["ok"] is False
    assert "missing evidence: reflected property" in result["issues"]
    assert "missing evidence: function" in result["issues"]
    assert "missing evidence: cpp pair" in result["issues"]
    assert "unsupported ownership claim" in "\n".join(result["issues"])


def test_cpp_pair_alone_does_not_support_ownership_claim() -> None:
    arch = _arch()
    arch["types"].append(
        {
            "name": "UCppOnlyComponent",
            "kind": "UCLASS",
            "module": "DemoGame",
            "header": "Source/DemoGame/Public/CppOnlyComponent.h",
            "cpp": "Source/DemoGame/Private/CppOnlyComponent.cpp",
            "category": "ActorComponent",
            "reflectedSurface": {"properties": [], "functions": []},
            "memberEvidence": {"variables": [], "methods": []},
            "riskFlags": [],
        }
    )

    payload = validator.validate_claims_payload(
        arch,
        {
            "claims": [
                {
                    "claim": "UCppOnlyComponent owns state",
                    "type": "ownership",
                    "subject": "UCppOnlyComponent",
                    "requiredEvidence": ["cpp pair"],
                }
            ]
        },
    )

    assert payload["ok"] is False
    assert payload["results"][0]["evidence"] == ["cpp pair"]
    assert "unsupported ownership claim" in "\n".join(payload["results"][0]["issues"])


def test_member_evidence_supports_ownership_without_reflection() -> None:
    arch = _arch()
    arch["types"].append(
        {
            "name": "UPlainOwnerComponent",
            "kind": "UCLASS",
            "module": "DemoGame",
            "header": "Source/DemoGame/Public/PlainOwnerComponent.h",
            "cpp": "Source/DemoGame/Private/PlainOwnerComponent.cpp",
            "category": "ActorComponent",
            "reflectedSurface": {"properties": [], "functions": []},
            "memberEvidence": {
                "variables": [{"name": "InternalState"}],
                "methods": [{"name": "ResetState"}],
            },
            "riskFlags": [],
        }
    )

    payload = validator.validate_claims_payload(
        arch,
        {
            "claims": [
                {
                    "claim": "UPlainOwnerComponent owns internal state",
                    "type": "ownership",
                    "subject": "UPlainOwnerComponent",
                    "requiredEvidence": ["member", "method", "cpp pair"],
                }
            ]
        },
    )

    assert payload["ok"] is True
    assert payload["results"][0]["evidence"] == ["member", "method", "cpp pair"]


def test_blueprint_event_evidence_is_supported() -> None:
    payload = validator.validate_claims_payload(
        _arch(),
        {
            "claims": [
                {
                    "claim": "UDemoCombatComponent exposes a Blueprint event",
                    "type": "risk",
                    "subject": "UDemoCombatComponent",
                    "requiredEvidence": ["BlueprintNativeEvent", "Blueprint event"],
                }
            ]
        },
    )

    assert payload["ok"] is True
    assert payload["results"][0]["evidence"] == ["BlueprintNativeEvent", "Blueprint event"]


def test_architecture_claim_validate_cli_writes_output(tmp_path: Path) -> None:
    arch_path = tmp_path / "architecture_map.json"
    claims_path = tmp_path / "claims.json"
    out_path = tmp_path / "claim_validation.json"
    arch_path.write_text(json.dumps(_arch()), encoding="utf-8")
    claims_path.write_text(
        json.dumps(
            {
                "claims": [
                    {
                        "claim": "UDemoCombatComponent owns combo state",
                        "type": "ownership",
                        "subject": "UDemoCombatComponent",
                        "requiredEvidence": ["reflected property", "function", "cpp pair"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    proc = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "architecture_claim_validate.py"),
            "--architecture",
            str(arch_path),
            "--claims",
            str(claims_path),
            "--out",
            str(out_path),
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr
    assert json.loads(out_path.read_text(encoding="utf-8"))["ok"] is True
