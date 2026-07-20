from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from review_claim_validate import validate_claim, validate_claims  # noqa: E402


def _write_authored_world_fixture(project_root: Path) -> None:
    public = project_root / "Source" / "DemoGame" / "Public" / "Cinematic"
    private = project_root / "Source" / "DemoGame" / "Private" / "Cinematic"
    public.mkdir(parents=True)
    private.mkdir(parents=True)
    (public / "CinematicTypes.h").write_text(
        "\n".join(
            [
                "#pragma once",
                "UENUM(BlueprintType)",
                "enum class ECinematicAnchorMode : uint8",
                "{",
                "\t// Level Sequence 에셋에 저장된 위치/회전을 그대로 사용.",
                "\tAuthoredWorld UMETA(DisplayName = \"Authored World\"),",
                "\tInstigatorActor,",
                "};",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (private / "CinematicDirectorSubsystem.cpp").write_text(
        "\n".join(
            [
                '#include "Cinematic/CinematicDirectorSubsystem.h"',
                "void UCinematicDirectorSubsystem::ApplyDynamicTransform() const",
                "{",
                "\tif (Request.AnchorMode == ECinematicAnchorMode::AuthoredWorld)",
                "\t{",
                "\t\treturn;",
                "\t}",
                "\tActiveSequenceActor->SetActorTransform(OutAnchorWorldTransform);",
                "}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (public / "CinematicDirectorSubsystem.h").write_text(
        "\n".join(
            [
                "#pragma once",
                "class UCinematicDirectorSubsystem",
                "{",
                "\tvoid ApplyDynamicTransform() const;",
                "};",
                "",
            ]
        ),
        encoding="utf-8",
    )


def test_authored_world_missing_logic_claim_fails_by_design(tmp_path: Path) -> None:
    project = tmp_path / "DemoGame"
    project.mkdir()
    _write_authored_world_fixture(project)

    result = validate_claim(
        "AuthoredWorld 로직 누락 — SetActorTransform을 호출하지 않아 버그",
        project,
        pab={},
    )
    assert result["ok"] is False
    assert "by_design_contract" in result["reasons"]
    assert any("by-design" in issue.lower() or "intentional" in issue.lower() for issue in result["issues"])


def test_exists_claim_without_source_needs_source_read(tmp_path: Path) -> None:
    project = tmp_path / "AlphaGame"
    (project / "Source").mkdir(parents=True)
    result = validate_claim(
        "UStaminaComponent exists in the project and is already implemented",
        project,
        pab={},
    )
    assert result["ok"] is False
    assert "needs_source_read" in result["reasons"]

    project = tmp_path / "DemoGame"
    project.mkdir()
    _write_authored_world_fixture(project)

    result = validate_claim(
        "Bug in Source/DemoGame/Private/Cinematic/CinematicDirectorSubsystem.cpp: "
        "AuthoredWorld early return is missing logic",
        project,
        pab={},
    )
    assert result["ok"] is False
    assert "header_contract_unread" in result["reasons"] or "by_design_contract" in result["reasons"]


def test_eval_case_bad_answer_fails_claim_validate(tmp_path: Path) -> None:
    import json

    cases = json.loads(
        (ROOT / "config" / "rag_eval_project_review_cases.json").read_text(encoding="utf-8-sig")
    )
    case = next(c for c in cases["cases"] if c["id"] == "project_example_authored_world_by_design")
    project = tmp_path / "DemoGame"
    project.mkdir()
    for snippet in case["snippets"]:
        path = project / snippet["path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(snippet["content"], encoding="utf-8")

    result = validate_claim(case["badAnswerFixture"], project, pab={})
    assert result["ok"] is False
    assert "by_design_contract" in result["reasons"]


def test_core_review_cases_include_authored_world_false_positive() -> None:
    import json

    cases = json.loads(
        (ROOT / "config" / "rag_eval_project_review_cases.json").read_text(encoding="utf-8-sig")
    )
    ids = {c["id"] for c in cases["cases"]}
    assert "project_example_authored_world_by_design" in ids
    assert "project_example_damage_semantics_and_wiring" in ids


def test_damage_semantics_eval_bad_answer_fails_framework_guard(tmp_path: Path) -> None:
    import json

    cases = json.loads(
        (ROOT / "config" / "rag_eval_project_review_cases.json").read_text(encoding="utf-8-sig")
    )
    case = next(
        c for c in cases["cases"] if c["id"] == "project_example_damage_semantics_and_wiring"
    )
    project = tmp_path / "DemoGame"
    project.mkdir()
    for snippet in case["snippets"]:
        path = project / snippet["path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(snippet["content"], encoding="utf-8")

    result = validate_claim(case["badAnswerFixture"], project, pab={})
    assert result["ok"] is False
    assert "framework_semantics_unverified" in result["reasons"]


def _write_damage_fixture(project_root: Path) -> None:
    source = project_root / "Source" / "DemoGame"
    public = source / "Public" / "Combat"
    private = source / "Private" / "Combat"
    public.mkdir(parents=True)
    private.mkdir(parents=True)
    (public / "HealthComponent.h").write_text(
        "class UHealthComponent { public: void ReceiveDamage(float Amount); };\n",
        encoding="utf-8",
    )
    (private / "PlayerCharacter.cpp").write_text(
        "float APlayerCharacter::TakeDamage(float Amount) { return Super::TakeDamage(Amount); }\n",
        encoding="utf-8",
    )
    (private / "EnemyCharacter.cpp").write_text(
        "float AEnemyCharacter::TakeDamage(float Amount) { HealthComponent->ReceiveDamage(Amount); return Amount; }\n",
        encoding="utf-8",
    )


def test_framework_causal_claim_requires_direct_framework_evidence(tmp_path: Path) -> None:
    project = tmp_path / "DemoGame"
    project.mkdir()
    _write_damage_fixture(project)

    result = validate_claim(
        "Super::TakeDamage 때문에 기본 HP와 HealthComponent HP가 함께 감소한다.",
        project,
        pab={},
    )
    assert result["ok"] is False
    assert "framework_semantics_unverified" in result["reasons"]

    sourced = validate_claim(
        "Unreal Engine source Actor.cpp:3417 확인 결과 AActor::TakeDamage는 이벤트를 호출하고 값을 반환한다.",
        project,
        pab={},
    )
    assert sourced["ok"] is True


def test_presence_is_not_treated_as_runtime_wiring(tmp_path: Path) -> None:
    project = tmp_path / "DemoGame"
    project.mkdir()
    _write_damage_fixture(project)

    result = validate_claim(
        "UHealthComponent exists and is fully implemented and integrated.",
        project,
        pab={},
    )
    assert result["ok"] is False
    assert "presence_not_wiring" in result["reasons"]


def test_structured_wiring_claim_requires_complete_behavior_path(tmp_path: Path) -> None:
    project = tmp_path / "DemoGame"
    project.mkdir()
    _write_damage_fixture(project)
    packet = {
        "claim": "Incoming damage is connected to player health.",
        "verdict": "Bug",
        "severity": "P0",
        "proofLevel": "SourceVerified",
        "claimType": "wiring",
        "evidence": [
            {
                "kind": "project_source",
                "location": "Source/DemoGame/Private/Combat/PlayerCharacter.cpp:1",
                "observation": "The player override returns after the base call.",
            }
        ],
        "behaviorPath": [
            {
                "stage": "entry",
                "stageStatus": "present",
                "location": "EnemyCharacter.cpp:1",
                "symbol": "TakeDamage",
            },
            {
                "stage": "decision",
                "stageStatus": "present",
                "location": "PlayerCharacter.cpp:1",
                "symbol": "TakeDamage",
            },
        ],
        "counterEvidence": [
            {
                "kind": "project_source",
                "location": "EnemyCharacter.cpp:1",
                "observation": "Enemy path calls ReceiveDamage.",
            }
        ],
        "unknowns": [],
    }

    result = validate_claim(packet["claim"], project, pab={}, claim_packet=packet)
    assert result["ok"] is False
    assert "behavior_path_incomplete" in result["reasons"]
    assert "presence_not_wiring" in result["reasons"]


def test_structured_claim_accepts_source_verified_entry_to_mutation_path(tmp_path: Path) -> None:
    project = tmp_path / "DemoGame"
    project.mkdir()
    _write_damage_fixture(project)
    packet = {
        "claim": "The enemy damage path reaches the health mutation API.",
        "verdict": "ByDesign",
        "severity": "P1",
        "proofLevel": "SourceVerified",
        "claimType": "wiring",
        "evidence": [
            {
                "kind": "project_source",
                "location": "Source/DemoGame/Private/Combat/EnemyCharacter.cpp:1",
                "observation": "TakeDamage calls ReceiveDamage.",
            }
        ],
        "behaviorPath": [
            {"stage": "entry", "stageStatus": "present", "location": "EnemyCharacter.cpp:1", "symbol": "TakeDamage"},
            {"stage": "decision", "stageStatus": "present", "location": "EnemyCharacter.cpp:1", "symbol": "Amount"},
            {"stage": "mutation", "stageStatus": "present", "location": "EnemyCharacter.cpp:1", "symbol": "ReceiveDamage"},
        ],
        "counterEvidence": [
            {
                "kind": "project_source",
                "location": "PlayerCharacter.cpp:1",
                "observation": "Symmetric player path does not call ReceiveDamage.",
            }
        ],
        "unknowns": [],
    }

    payload = validate_claims([packet], project)
    assert payload["ok"] is True
    assert payload["results"][0]["structured"] is True


def test_structured_claim_rejects_malformed_evidence_entries(tmp_path: Path) -> None:
    project = tmp_path / "DemoGame"
    project.mkdir()
    _write_damage_fixture(project)
    packet = {
        "claim": "The health component owns the mutation.",
        "verdict": "ByDesign",
        "severity": "P2",
        "proofLevel": "SourceVerified",
        "claimType": "architecture",
        "evidence": [{"kind": "guess", "location": "", "observation": ""}],
        "behaviorPath": [],
        "counterEvidence": [],
        "unknowns": [],
    }

    result = validate_claim(packet["claim"], project, pab={}, claim_packet=packet)
    assert result["ok"] is False
    assert "evidence_packet_invalid" in result["reasons"]


def test_structured_claim_rejects_out_of_order_behavior_path(tmp_path: Path) -> None:
    project = tmp_path / "DemoGame"
    project.mkdir()
    _write_damage_fixture(project)
    packet = {
        "claim": "The enemy damage path reaches the health mutation API.",
        "verdict": "ByDesign",
        "severity": "P1",
        "proofLevel": "SourceVerified",
        "claimType": "wiring",
        "evidence": [
            {
                "kind": "project_source",
                "location": "Source/DemoGame/Private/Combat/EnemyCharacter.cpp:1",
                "observation": "TakeDamage calls ReceiveDamage.",
            }
        ],
        "behaviorPath": [
            {"stage": "mutation", "stageStatus": "present", "location": "EnemyCharacter.cpp:1", "symbol": "ReceiveDamage"},
            {"stage": "entry", "stageStatus": "present", "location": "EnemyCharacter.cpp:1", "symbol": "TakeDamage"},
            {"stage": "decision", "stageStatus": "present", "location": "EnemyCharacter.cpp:1", "symbol": "Amount"},
        ],
        "counterEvidence": [
            {
                "kind": "project_source",
                "location": "PlayerCharacter.cpp:1",
                "observation": "The symmetric player path was inspected.",
            }
        ],
        "unknowns": [],
    }
    result = validate_claim(packet["claim"], project, pab={}, claim_packet=packet)
    assert result["ok"] is False
    assert "behavior_path_incomplete" in result["reasons"]
    assert any("must order entry" in issue for issue in result["issues"])
