from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import architecture_map  # noqa: E402


def _write_demo_project(tmp_path: Path) -> Path:
    project = tmp_path / "DemoGame"
    module = project / "Source" / "DemoGame"
    public = module / "Public"
    private = module / "Private"
    public.mkdir(parents=True)
    private.mkdir(parents=True)
    (project / "DemoGame.uproject").write_text(
        json.dumps(
            {
                "FileVersion": 3,
                "EngineAssociation": "5.8",
                "Modules": [{"Name": "DemoGame", "Type": "Runtime", "LoadingPhase": "Default"}],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (module / "DemoGame.Build.cs").write_text(
        """
using UnrealBuildTool;

public class DemoGame : ModuleRules
{
    public DemoGame(ReadOnlyTargetRules Target) : base(Target)
    {
        PublicDependencyModuleNames.AddRange(new string[] { "Core", "CoreUObject", "Engine" });
        PrivateDependencyModuleNames.AddRange(new string[] { "UMG", "GameplayTags" });
    }
}
""".strip()
        + "\n",
        encoding="utf-8",
    )
    (project / "Source" / "DemoGame.Target.cs").write_text("public class DemoGameTarget {}\n", encoding="utf-8")
    (public / "DemoCombatComponent.h").write_text(
        """
#pragma once
#include "CoreMinimal.h"
#include "Components/ActorComponent.h"
#include "Animation/AnimMontage.h"
#include "DemoCombatComponent.generated.h"

UCLASS(ClassGroup=(Demo), meta=(BlueprintSpawnableComponent))
class DEMOGAME_API UDemoCombatComponent : public UActorComponent
{
    GENERATED_BODY()
public:
    UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category="Combat")
    int32 CurrentComboIndex = 0;

    UPROPERTY(EditAnywhere, BlueprintReadOnly, Category="Combat")
    TObjectPtr<UAnimMontage> AttackMontage;

    UFUNCTION(BlueprintCallable, Category="Combat")
    void StartAttack();

    UFUNCTION(BlueprintNativeEvent, Category="Combat")
    bool CanStartAttack() const;

    UFUNCTION(BlueprintImplementableEvent, Category="Combat")
    void OnComboWindowOpened();

    void ResetCombo();

private:
    int32 InternalComboSeed = 0;
};
""".strip()
        + "\n",
        encoding="utf-8",
    )
    (private / "DemoCombatComponent.cpp").write_text(
        """
#include "DemoCombatComponent.h"

void UDemoCombatComponent::StartAttack()
{
}
""".strip()
        + "\n",
        encoding="utf-8",
    )
    (public / "DemoPayload.h").write_text(
        """
#pragma once
#include "DemoPayload.generated.h"

USTRUCT(BlueprintType)
struct FDemoPayload
{
    GENERATED_BODY()

    UPROPERTY(EditAnywhere)
    int32 Damage = 0;
};
""".strip()
        + "\n",
        encoding="utf-8",
    )
    (public / "DemoInteractable.h").write_text(
        """
#pragma once
#include "UObject/Interface.h"
#include "DemoInteractable.generated.h"

UINTERFACE(BlueprintType)
class DEMOGAME_API UDemoInteractable : public UInterface
{
    GENERATED_BODY()
};
""".strip()
        + "\n",
        encoding="utf-8",
    )
    return project / "DemoGame.uproject"


def _type_by_name(arch: dict, name: str) -> dict:
    return next(row for row in arch["types"] if row["name"] == name)


def test_architecture_map_detects_modules_types_members_pairs_and_risks(tmp_path: Path) -> None:
    uproject = _write_demo_project(tmp_path)

    arch = architecture_map.generate_architecture_map(uproject)

    assert arch["schemaVersion"] == 1
    assert arch["project"]["name"] == "DemoGame"
    module = arch["modules"][0]
    assert module["name"] == "DemoGame"
    assert module["publicDependencies"] == ["Core", "CoreUObject", "Engine"]
    assert module["privateDependencies"] == ["UMG", "GameplayTags"]
    assert module["classification"] == "runtime"

    combat = _type_by_name(arch, "UDemoCombatComponent")
    assert combat["kind"] == "UCLASS"
    assert combat["baseClass"] == "UActorComponent"
    assert combat["category"] == "ActorComponent"
    assert combat["cpp"].endswith("Source/DemoGame/Private/DemoCombatComponent.cpp")
    assert "hint: component-level gameplay behavior" in combat["responsibilityHints"]
    assert "hint: combat state / action execution candidate" in combat["responsibilityHints"]
    assert "blueprint_facing_surface" in combat["riskFlags"]
    assert "blueprint_event_surface" in combat["riskFlags"]
    assert "blueprint_native_event_surface" in combat["riskFlags"]
    assert "blueprint_implementable_event_surface" in combat["riskFlags"]
    assert "reflected_serialized_surface" in combat["riskFlags"]
    assert "possible_asset_reference" in combat["riskFlags"]

    properties = {row["name"]: row["specifiers"] for row in combat["reflectedSurface"]["properties"]}
    functions = {row["name"]: row["specifiers"] for row in combat["reflectedSurface"]["functions"]}
    assert properties["CurrentComboIndex"] == ["VisibleAnywhere", "BlueprintReadOnly", "Category"]
    assert "BlueprintCallable" in functions["StartAttack"]
    assert "BlueprintNativeEvent" in functions["CanStartAttack"]
    assert "BlueprintImplementableEvent" in functions["OnComboWindowOpened"]
    member_variables = {row["name"] for row in combat["memberEvidence"]["variables"]}
    member_methods = {row["name"] for row in combat["memberEvidence"]["methods"]}
    assert member_variables == {"InternalComboSeed"}
    assert "ResetCombo" in member_methods
    assert "StartAttack" not in member_methods
    assert "CurrentComboIndex" not in member_variables

    payload = _type_by_name(arch, "FDemoPayload")
    assert payload["kind"] == "USTRUCT"
    iface = _type_by_name(arch, "UDemoInteractable")
    assert iface["kind"] == "UINTERFACE"
    assert iface["category"] == "Interface"


def test_architecture_map_missing_source_is_nonfatal(tmp_path: Path) -> None:
    project = tmp_path / "EmptyGame"
    project.mkdir()
    uproject = project / "EmptyGame.uproject"
    uproject.write_text('{"FileVersion":3,"EngineAssociation":"5.8"}\n', encoding="utf-8")

    arch = architecture_map.generate_architecture_map(uproject)

    assert arch["project"]["name"] == "EmptyGame"
    assert arch["modules"] == []
    assert arch["types"] == []


def test_architecture_map_scopes_reflected_members_per_type(tmp_path: Path) -> None:
    uproject = _write_demo_project(tmp_path)
    public = uproject.parent / "Source" / "DemoGame" / "Public"
    private = uproject.parent / "Source" / "DemoGame" / "Private"
    (public / "MultiTypeHeader.h").write_text(
        """
#pragma once
#include "CoreMinimal.h"
#include "Components/ActorComponent.h"
#include "MultiTypeHeader.generated.h"

UCLASS()
class DEMOGAME_API UAlphaComponent : public UActorComponent
{
    GENERATED_BODY()
public:
    UPROPERTY(BlueprintReadOnly)
    int32 AlphaValue = 0;

    UFUNCTION(BlueprintCallable)
    void StartAlpha();
};

UCLASS()
class DEMOGAME_API UBetaComponent : public UActorComponent
{
    GENERATED_BODY()
public:
    UPROPERTY(EditAnywhere)
    int32 BetaValue = 0;
};
""".strip()
        + "\n",
        encoding="utf-8",
    )
    (private / "MultiTypeHeader.cpp").write_text('#include "MultiTypeHeader.h"\n', encoding="utf-8")

    arch = architecture_map.generate_architecture_map(uproject)
    alpha = _type_by_name(arch, "UAlphaComponent")
    beta = _type_by_name(arch, "UBetaComponent")
    alpha_props = {row["name"] for row in alpha["reflectedSurface"]["properties"]}
    beta_props = {row["name"] for row in beta["reflectedSurface"]["properties"]}
    alpha_funcs = {row["name"] for row in alpha["reflectedSurface"]["functions"]}
    beta_funcs = {row["name"] for row in beta["reflectedSurface"]["functions"]}

    assert alpha_props == {"AlphaValue"}
    assert beta_props == {"BetaValue"}
    assert alpha_funcs == {"StartAlpha"}
    assert beta_funcs == set()
    assert "blueprint_facing_surface" in alpha["riskFlags"]
    assert "blueprint_facing_surface" not in beta["riskFlags"]


def test_cpp_member_evidence_avoids_reflection_and_macro_false_positives() -> None:
    evidence = architecture_map.parse_cpp_member_evidence(
        """
        GENERATED_BODY()
    public:
        UPROPERTY(BlueprintReadOnly)
        int32 ReflectedValue = 0;

        UFUNCTION(BlueprintCallable)
        void ReflectedCall();

        UE_DECLARE_GAMEPLAY_TAG_EXTERN(DemoAttackTag);
        DECLARE_DYNAMIC_MULTICAST_DELEGATE(FOnDemoAttack);

        virtual const TArray<FVector>& GetTracePoints() const override;
        static bool IsComboOpen(int32 ComboIndex, float ElapsedSeconds);

    private:
        TMap<FName, int32> ComboCounts;
        mutable TWeakObjectPtr<UObject> CachedOwner;
        """,
        "UDemoCombatComponent",
    )

    variables = {row["name"] for row in evidence["variables"]}
    methods = {row["name"] for row in evidence["methods"]}
    assert variables == {"ComboCounts", "CachedOwner"}
    assert methods == {"GetTracePoints", "IsComboOpen"}
    assert "ReflectedValue" not in variables
    assert "ReflectedCall" not in methods
    assert "UE_DECLARE_GAMEPLAY_TAG_EXTERN" not in methods


def test_architecture_map_detects_plugin_and_editor_runtime_boundary(tmp_path: Path) -> None:
    uproject = _write_demo_project(tmp_path)
    project = uproject.parent
    plugin = project / "Plugins" / "DemoPlugin"
    runtime_module = plugin / "Source" / "DemoPlugin"
    editor_module = plugin / "Source" / "DemoPluginEditor"
    (runtime_module / "Public").mkdir(parents=True)
    (runtime_module / "Private").mkdir(parents=True)
    editor_module.mkdir(parents=True)
    (plugin / "DemoPlugin.uplugin").write_text(
        json.dumps(
            {
                "FileVersion": 3,
                "VersionName": "1.0",
                "Modules": [
                    {"Name": "DemoPlugin", "Type": "Runtime", "LoadingPhase": "Default"},
                    {"Name": "DemoPluginEditor", "Type": "Editor", "LoadingPhase": "Default"},
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (runtime_module / "DemoPlugin.Build.cs").write_text(
        """
using UnrealBuildTool;

public class DemoPlugin : ModuleRules
{
    public DemoPlugin(ReadOnlyTargetRules Target) : base(Target)
    {
        PublicDependencyModuleNames.AddRange(new string[] { "Core", "CoreUObject", "Engine" });
    }
}
""".strip()
        + "\n",
        encoding="utf-8",
    )
    (editor_module / "DemoPluginEditor.Build.cs").write_text(
        """
using UnrealBuildTool;

public class DemoPluginEditor : ModuleRules
{
    public DemoPluginEditor(ReadOnlyTargetRules Target) : base(Target)
    {
        PrivateDependencyModuleNames.AddRange(new string[] { "Core", "UnrealEd" });
    }
}
""".strip()
        + "\n",
        encoding="utf-8",
    )
    (runtime_module / "Public" / "RuntimeEditorLeakComponent.h").write_text(
        """
#pragma once
#include "CoreMinimal.h"
#include "Components/ActorComponent.h"
#include "EditorUtilityWidget.h"
#include "RuntimeEditorLeakComponent.generated.h"

UCLASS()
class DEMOPLUGIN_API URuntimeEditorLeakComponent : public UActorComponent
{
    GENERATED_BODY()
public:
    UPROPERTY(EditAnywhere)
    TSubclassOf<UEditorUtilityWidget> WidgetClass;
};
""".strip()
        + "\n",
        encoding="utf-8",
    )

    arch = architecture_map.generate_architecture_map(uproject)

    assert {"name": "DemoPlugin", "path": "Plugins/DemoPlugin/DemoPlugin.uplugin"} in arch["project"]["pluginsDetected"]
    modules = {row["name"]: row for row in arch["modules"]}
    assert modules["DemoPlugin"]["classification"] == "runtime"
    assert modules["DemoPluginEditor"]["classification"] == "editor"
    leaked = _type_by_name(arch, "URuntimeEditorLeakComponent")
    assert "editor_only_name_hint" in leaked["riskFlags"]
    assert "runtime_editor_boundary_risk" in leaked["riskFlags"]


def test_architecture_map_cli_writes_json_and_markdown(tmp_path: Path) -> None:
    uproject = _write_demo_project(tmp_path)
    out = tmp_path / "architecture_map.json"
    md = tmp_path / "PROJECT_MAP.generated.md"

    proc = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "architecture_map.py"),
            "--project",
            str(uproject),
            "--out",
            str(out),
            "--markdown",
            str(md),
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr
    assert json.loads(out.read_text(encoding="utf-8"))["project"]["name"] == "DemoGame"
    assert "Generated architecture hints" in md.read_text(encoding="utf-8")
