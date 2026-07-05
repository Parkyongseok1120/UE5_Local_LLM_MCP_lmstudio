from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from refactor_plan import (  # noqa: E402
    build_refactor_manager_plan,
    classify_refactor_scope,
    scan_symbol_impact,
    validate_refactor_plan,
)


def test_classify_small_multifile_refactor_allows_default_writes():
    scope = classify_refactor_scope(
        "Rename ApplyDamage across declaration, definition, and callsite in HealthComponent.h and HealthComponent.cpp"
    )

    assert scope["scope"] == "small_multifile_refactor"
    assert scope["writesAllowedByDefault"] is True
    assert scope["requiresHumanApproval"] is False


def test_classify_medium_refactor_requires_approval():
    scope = classify_refactor_scope("Refactor combat system API across inventory, ability, and subsystem owners")

    assert scope["scope"] == "medium_system_local_refactor"
    assert scope["writesAllowedByDefault"] is False
    assert scope["requiresHumanApproval"] is True
    assert "architecture_rule_check" in scope["requiredGates"]


def test_validate_r0_medium_refactor_requires_staged_approval_gate():
    result = validate_refactor_plan(
        "R0",
        "Owner: CombatComponent. Impact files: Combat.h Combat.cpp. Risk: API migration for combat system.",
    )

    assert result["ok"] is False
    assert result["refactorScope"]["scope"] == "medium_system_local_refactor"
    assert any("approval gate" in issue for issue in result["issues"])


def test_validate_r0_medium_refactor_accepts_staged_approval_plan():
    result = validate_refactor_plan(
        "R0",
        (
            "Owner: CombatComponent SSOT. Impact files: Combat.h Combat.cpp Ability.cpp. "
            "Risk: combat system API migration. Stage R1 boundary only, R2 implementation, R3 callsites. "
            "Human approval gate before code edits. UBT and log scan validation."
        ),
    )

    assert result["ok"] is True
    assert result["refactorScope"]["requiresHumanApproval"] is True


def test_scan_symbol_impact_reports_roles_and_risks(tmp_path):
    src = tmp_path / "Source" / "Demo"
    public = src / "Public"
    private = src / "Private"
    public.mkdir(parents=True)
    private.mkdir(parents=True)
    (public / "HealthComponent.h").write_text(
        """#pragma once

#include "CoreMinimal.h"
#include "HealthComponent.generated.h"

UCLASS()
class UHealthComponent : public UObject
{
	GENERATED_BODY()
public:
	UFUNCTION(BlueprintCallable)
	void ApplyDamage(float Amount);
};
""",
        encoding="utf-8",
    )
    (private / "HealthComponent.cpp").write_text(
        """#include "HealthComponent.h"

void UHealthComponent::ApplyDamage(float Amount)
{
	(void)Amount;
}

void BindDamage(UHealthComponent* Component)
{
	OnDamage.AddUObject(Component, &UHealthComponent::ApplyDamage);
	Component->ApplyDamage(1.0f);
}
""",
        encoding="utf-8",
    )

    result = scan_symbol_impact(str(tmp_path), "ApplyDamage")

    assert result["ok"] is True
    assert result["matchCount"] == 2
    assert result["roleCounts"]["declaration"] == 1
    assert result["roleCounts"]["definition"] == 1
    assert result["roleCounts"]["delegate_binding"] == 1
    assert result["roleCounts"]["callsite"] == 1
    assert result["riskCounts"]["blueprint_surface"] == 1


def test_refactor_manager_blocks_medium_refactor_until_approval():
    plan = build_refactor_manager_plan(
        "Refactor combat system API across inventory and ability subsystem",
        symbols=["UCombatSubsystem"],
    )

    assert plan["managerMode"] == "refactor_manager"
    assert plan["scope"]["scope"] == "medium_system_local_refactor"
    assert plan["approval"]["required"] is True
    assert plan["writePolicy"]["writesAllowedNow"] is False
    assert plan["nextAction"] == "collect_impact_scan_inputs"
    assert "human_approval_gate" in plan["requiredEvidence"]["gates"]


def test_refactor_manager_aggregates_symbol_impact_roles(tmp_path):
    src = tmp_path / "Source" / "Demo"
    public = src / "Public"
    private = src / "Private"
    public.mkdir(parents=True)
    private.mkdir(parents=True)
    (public / "ScoreComponent.h").write_text(
        """#pragma once

#include "CoreMinimal.h"
#include "ScoreComponent.generated.h"

UCLASS()
class UScoreComponent : public UObject
{
	GENERATED_BODY()
public:
	UFUNCTION(BlueprintCallable)
	int32 GetScore() const;
};
""",
        encoding="utf-8",
    )
    (private / "ScoreComponent.cpp").write_text(
        """#include "ScoreComponent.h"

int32 UScoreComponent::GetScore() const
{
	return 1;
}

void ReadScore(UScoreComponent* Component)
{
	Component->GetScore();
}
""",
        encoding="utf-8",
    )

    plan = build_refactor_manager_plan(
        "Move GetScore declaration, definition, and callsite as a small multifile refactor",
        project_root=str(tmp_path),
        symbols=["GetScore"],
    )

    assert plan["impact"]["scanStatus"] == "completed"
    assert plan["impact"]["roleCounts"]["declaration"] == 1
    assert plan["impact"]["roleCounts"]["definition"] == 1
    assert plan["impact"]["roleCounts"]["callsite"] == 1
    assert plan["writePolicy"]["requiresStagedPatch"] is True
    assert "blueprint_compile_or_asset_validation" in plan["requiredEvidence"]["validation"]
    assert [stage["stage"] for stage in plan["stages"]] == ["R0", "R1", "R2", "R3", "R4"]
