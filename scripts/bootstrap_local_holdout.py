#!/usr/bin/env python
"""Create an ignored local live holdout config from the public-safe template."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EXAMPLE = ROOT / "config" / "rag_eval_real_project_holdout_cases.local.example.json"
DEFAULT_OUTPUT = ROOT / "config" / "rag_eval_real_project_holdout_cases.local.json"
DEFAULT_SUITE_NAME = "real-project-holdout-local-v0"
DEFAULT_MODEL = "qwen3.6-27b-heretic-uncensored-finetune-neo-code-di-imatrix-max"
DEFAULT_FIXTURE_ROOT = ROOT / "data" / "local_holdout_fixtures"
BASE_FIXTURE = DEFAULT_FIXTURE_ROOT / "_base_ue58_cpp_project"


EXPANSION_CASES_12: list[dict[str, Any]] = [
    {
        "id": "local_umg_missing_module",
        "category": "UMG dependency issue",
        "mode": "module_fix",
        "errorLog": "fatal error C1083: Cannot open include file: 'Blueprint/UserWidget.h': No such file or directory",
        "expectedFilesToRead": ["Source/<Module>/<Module>.Build.cs", "failing UI source/header"],
        "expectedPatchTargets": ["owner Build.cs"],
        "forbiddenPatchTargets": ["unrelated widget rewrite"],
        "expectedModules": ["UMG"],
        "expectedErrorSubkind": "C1083_MISSING_INCLUDE",
        "notes": "UE 5.8 local fixture; expected fix is adding UMG to the owner Build.cs.",
    },
    {
        "id": "local_niagara_missing_module",
        "category": "Niagara dependency issue",
        "mode": "module_fix",
        "errorLog": "fatal error C1083: Cannot open include file: 'NiagaraComponent.h': No such file or directory",
        "expectedFilesToRead": ["Source/<Module>/<Module>.Build.cs", "failing Niagara source/header"],
        "expectedPatchTargets": ["owner Build.cs"],
        "forbiddenPatchTargets": ["unrelated effects rewrite"],
        "expectedModules": ["Niagara"],
        "expectedErrorSubkind": "C1083_MISSING_INCLUDE",
        "notes": "UE 5.8 local fixture; expected fix is adding Niagara to the owner Build.cs.",
    },
    {
        "id": "local_aimodule_missing_module",
        "category": "AI module dependency issue",
        "mode": "module_fix",
        "errorLog": "fatal error C1083: Cannot open include file: 'AIController.h': No such file or directory",
        "expectedFilesToRead": ["Source/<Module>/<Module>.Build.cs", "failing AI source/header"],
        "expectedPatchTargets": ["owner Build.cs"],
        "forbiddenPatchTargets": ["unrelated behavior rewrite"],
        "expectedModules": ["AIModule"],
        "expectedErrorSubkind": "C1083_MISSING_INCLUDE",
        "notes": "UE 5.8 local fixture; expected fix is adding AIModule to the owner Build.cs.",
    },
    {
        "id": "local_navigation_system_missing_module",
        "category": "NavigationSystem dependency issue",
        "mode": "module_fix",
        "errorLog": "fatal error C1083: Cannot open include file: 'NavigationSystem.h': No such file or directory",
        "expectedFilesToRead": ["Source/<Module>/<Module>.Build.cs", "failing navigation source/header"],
        "expectedPatchTargets": ["owner Build.cs"],
        "forbiddenPatchTargets": ["unrelated movement rewrite"],
        "expectedModules": ["NavigationSystem"],
        "expectedErrorSubkind": "C1083_MISSING_INCLUDE",
        "notes": "UE 5.8 local fixture; expected fix is adding NavigationSystem to the owner Build.cs.",
    },
    {
        "id": "local_levelsequence_missing_module",
        "category": "LevelSequence dependency issue",
        "mode": "module_fix",
        "errorLog": "fatal error C1083: Cannot open include file: 'LevelSequence.h': No such file or directory",
        "expectedFilesToRead": ["Source/<Module>/<Module>.Build.cs", "failing sequence source/header"],
        "expectedPatchTargets": ["owner Build.cs"],
        "forbiddenPatchTargets": ["unrelated cinematic rewrite"],
        "expectedModules": ["LevelSequence"],
        "expectedErrorSubkind": "C1083_MISSING_INCLUDE",
        "notes": "UE 5.8 local fixture; expected fix is adding LevelSequence. Replace if live UBT proves this case needs multiple modules.",
    },
    {
        "id": "local_blueprint_native_event_missing_implementation",
        "category": "BlueprintNativeEvent signature issue",
        "mode": "reflection_fix",
        "errorLog": "BlueprintNativeEvent function OnHoldoutEvent_Implementation not found for declaration",
        "expectedFilesToRead": ["header declaration", "matching cpp file"],
        "expectedPatchTargets": ["matching cpp/header"],
        "forbiddenPatchTargets": ["unrelated interface rewrite"],
        "expectedErrorSubkind": "BLUEPRINT_NATIVE_EVENT_IMPL_MISSING",
        "notes": "UE 5.8 local fixture; expected fix is adding or correcting the _Implementation definition.",
    },
    {
        "id": "local_editor_only_runtime_boundary",
        "category": "editor-only include in runtime module",
        "mode": "module_fix",
        "errorLog": "Editor-only UnrealEd include is referenced from a runtime module source file",
        "expectedFilesToRead": ["failing runtime source/header", "module Build.cs"],
        "expectedPatchTargets": ["failing file", "module boundary files"],
        "forbiddenPatchTargets": ["adding UnrealEd to runtime module as default fix"],
        "expectedErrorSubkind": "EDITOR_ONLY_INCLUDE_IN_RUNTIME_MODULE",
        "notes": "UE 5.8 local fixture; expected fix should remove/guard editor-only runtime usage, not blindly add UnrealEd.",
    },
]

EXPANSION_CASES_24: list[dict[str, Any]] = [
    {
        "id": "local_include_path_wrong_owner",
        "category": "wrong include owner / missing include path",
        "mode": "compile_fix",
        "errorLog": "fatal error C1083: Cannot open include file: 'BoxComponent.h': No such file or directory",
        "expectedFilesToRead": ["failing header", "include owner evidence"],
        "expectedPatchTargets": ["failing header"],
        "forbiddenPatchTargets": ["Build.cs-first fix without module evidence"],
        "expectedErrorSubkind": "C1083_MISSING_INCLUDE",
        "notes": "Fix the include path to Components/BoxComponent.h; do not add a module for an Engine-owned include already covered by base deps.",
    },
    {
        "id": "local_blueprint_implementable_event_native_impl",
        "category": "BlueprintImplementableEvent signature issue",
        "mode": "reflection_fix",
        "errorLog": "error C2039: 'OnHoldoutBlueprintEvent_Implementation': is not a member of UHoldoutImplementableEventComponent",
        "expectedFilesToRead": ["BlueprintImplementableEvent declaration", "matching cpp file"],
        "expectedPatchTargets": ["matching cpp/header"],
        "forbiddenPatchTargets": ["Build.cs-first fix without module evidence"],
        "notes": "BlueprintImplementableEvent should not have a native _Implementation body unless converted to BlueprintNativeEvent intentionally.",
    },
    {
        "id": "local_delegate_broadcast_signature_mismatch",
        "category": "delegate binding signature issue",
        "mode": "compile_fix",
        "errorLog": "error C2660: FOnHoldoutScoreChanged::Broadcast: function does not take 0 arguments",
        "expectedFilesToRead": ["delegate declaration", "broadcast call site"],
        "expectedPatchTargets": ["matching cpp/header"],
        "forbiddenPatchTargets": ["Build.cs-first fix without module evidence"],
        "notes": "Fix the Broadcast call to match the declared delegate payload.",
    },
    {
        "id": "local_component_registration_missing_include",
        "category": "component registration issue",
        "mode": "compile_fix",
        "errorLog": "error C2027: use of undefined type 'UBoxComponent' while creating a default subobject",
        "expectedFilesToRead": ["component constructor cpp", "header forward declarations"],
        "expectedPatchTargets": ["matching cpp/header"],
        "forbiddenPatchTargets": ["Build.cs-first fix without module evidence"],
        "notes": "Add the concrete component include at the use site rather than changing module dependencies.",
    },
    {
        "id": "local_game_instance_subsystem_missing_include",
        "category": "subsystem registration issue",
        "mode": "compile_fix",
        "errorLog": "error C2504: 'UGameInstanceSubsystem': base class undefined",
        "expectedFilesToRead": ["subsystem header"],
        "expectedPatchTargets": ["failing header"],
        "forbiddenPatchTargets": ["Build.cs-first fix without module evidence"],
        "notes": "Add the required subsystem header include for UGameInstanceSubsystem.",
    },
    {
        "id": "local_plugin_projects_missing_module",
        "category": "plugin/module dependency issue",
        "mode": "module_fix",
        "errorLog": "fatal error C1083: Cannot open include file: 'Interfaces/IPluginManager.h': No such file or directory",
        "expectedFilesToRead": ["owner Build.cs", "failing plugin manager source"],
        "expectedPatchTargets": ["owner Build.cs"],
        "forbiddenPatchTargets": ["unrelated plugin descriptor rewrite"],
        "expectedModules": ["Projects"],
        "expectedErrorSubkind": "C1083_MISSING_INCLUDE",
        "notes": "IPluginManager is owned by Projects; patch the owner Build.cs only.",
    },
    {
        "id": "local_uobject_lifecycle_missing_include",
        "category": "UObject lifecycle issue",
        "mode": "compile_fix",
        "errorLog": "error C3861: 'NewObject': identifier not found",
        "expectedFilesToRead": ["UObject allocation source"],
        "expectedPatchTargets": ["matching cpp/header"],
        "forbiddenPatchTargets": ["Build.cs-first fix without module evidence"],
        "notes": "Add the missing UObject globals include at the source using NewObject.",
    },
    {
        "id": "local_multifile_method_rename_header_cpp_callsite",
        "category": "simple multi-file compile refactor",
        "mode": "compile_fix",
        "errorLog": "error C2039: 'StartCharge': is not a member of UHoldoutRefactorComponent",
        "expectedFilesToRead": ["header declaration", "cpp definition", "call site cpp"],
        "expectedPatchTargets": ["matching cpp/header"],
        "forbiddenPatchTargets": ["Build.cs-first fix without module evidence"],
        "notes": "Align a renamed method across header, implementation, and call site.",
    },
    {
        "id": "local_multifile_delegate_signature_update",
        "category": "simple multi-file compile refactor",
        "mode": "compile_fix",
        "errorLog": "error C2660: UHoldoutDelegateOwner::HandleScoreChanged: function does not take 1 arguments",
        "expectedFilesToRead": ["delegate owner header", "delegate owner cpp"],
        "expectedPatchTargets": ["matching cpp/header"],
        "forbiddenPatchTargets": ["Build.cs-first fix without module evidence"],
        "notes": "Update a delegate handler declaration and definition together.",
    },
    {
        "id": "local_multifile_interface_signature_update",
        "category": "simple multi-file compile refactor",
        "mode": "compile_fix",
        "errorLog": "error C3668: method with override specifier did not override any base class methods",
        "expectedFilesToRead": ["interface header", "implementer header", "implementer cpp"],
        "expectedPatchTargets": ["matching cpp/header"],
        "forbiddenPatchTargets": ["Build.cs-first fix without module evidence"],
        "notes": "Align an interface method signature with its implementer across files.",
    },
    {
        "id": "local_multifile_component_api_move",
        "category": "simple multi-file compile refactor",
        "mode": "compile_fix",
        "errorLog": "error C2039: 'ApplyMovedValue': is not a member of UHoldoutApiComponent",
        "expectedFilesToRead": ["component header", "component cpp", "consumer cpp"],
        "expectedPatchTargets": ["matching cpp/header"],
        "forbiddenPatchTargets": ["Build.cs-first fix without module evidence"],
        "notes": "Repair a small component API move across declaration, definition, and caller.",
    },
    {
        "id": "local_common_const_signature_mismatch",
        "category": "common compile regression",
        "mode": "compile_fix",
        "errorLog": "error C2511: overloaded member function not found in UHoldoutConstComponent",
        "expectedFilesToRead": ["header declaration", "matching cpp file"],
        "expectedPatchTargets": ["matching cpp/header"],
        "forbiddenPatchTargets": ["Build.cs-first fix without module evidence"],
        "notes": "Fix a const qualifier mismatch without touching module dependencies.",
    },
]


def detect_ubt_path() -> Path | None:
    """Return the UE 5.8 UBT path if present; never fail if absent."""
    base = Path("C:/") / "Program Files" / "Epic Games"
    candidate = base / "UE_5.8" / "Engine" / "Binaries" / "DotNET" / "UnrealBuildTool" / "UnrealBuildTool.exe"
    if candidate.is_file():
        return candidate
    return None


def load_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return data


def build_suite_config(data: dict[str, Any], suite: str = "5") -> dict[str, Any]:
    out = json.loads(json.dumps(data))
    if str(suite) == "5":
        return out
    if str(suite) not in {"12", "24"}:
        raise ValueError("--suite must be 5, 12, or 24")
    existing = {str(case.get("id")) for case in out.get("cases") or []}
    additions = [case for case in EXPANSION_CASES_12 if case["id"] not in existing]
    if str(suite) == "24":
        additions.extend(case for case in EXPANSION_CASES_24 if case["id"] not in existing)
    out.setdefault("cases", []).extend(json.loads(json.dumps(additions)))
    out["suite"] = f"real-project-holdout-local-v0-{suite}"
    out["description"] = (
        f"UE 5.8 local {suite}-case holdout config. Fixture directories are local-only and ignored; "
        "do not commit this .local.json file."
    )
    out["engineVersion"] = "5.8"
    return out


def update_config(
    data: dict[str, Any],
    *,
    project_file: str = "",
    fixture_root: str = "",
    suite_name: str = DEFAULT_SUITE_NAME,
    suite: str = "5",
) -> dict[str, Any]:
    out = build_suite_config(data, suite=suite)
    if suite_name:
        out["suite"] = suite_name if str(suite) == "5" else f"{suite_name}-{suite}"
    out["engineVersion"] = "5.8"
    fixture_root = fixture_root.rstrip("/\\")
    for case in out.get("cases") or []:
        case["engineVersion"] = "5.8"
        if project_file:
            case["projectFile"] = project_file
        if fixture_root:
            case["fixtureDir"] = f"{fixture_root}/{case.get('id')}"
        case.setdefault("projectFile", "HoldoutFixture.uproject")
        case.setdefault("target", "HoldoutFixtureEditor Win64 Development")
        case.setdefault("requestFile", "request.txt")
        if str(suite) in {"12", "24"} and str(case.get("target") or "").startswith("<TARGET_NAME>"):
            case["target"] = "HoldoutFixtureEditor Win64 Development"
        if str(suite) in {"12", "24"} and str(case.get("projectFile") or "").startswith("<PATH_TO_PROJECT>"):
            case["projectFile"] = "HoldoutFixture.uproject"
    return out


def write_local_config(
    *,
    example_path: Path = DEFAULT_EXAMPLE,
    output_path: Path = DEFAULT_OUTPUT,
    project_file: str = "",
    fixture_root: str = "",
    suite_name: str = DEFAULT_SUITE_NAME,
    suite: str = "5",
    force: bool = False,
) -> dict[str, Any]:
    if output_path.exists() and not force:
        raise FileExistsError(f"{output_path} already exists; use --force to overwrite")
    data = update_config(
        load_json(example_path),
        project_file=project_file,
        fixture_root=fixture_root,
        suite_name=suite_name,
        suite=suite,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return data


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_minimal_base_fixture(fixture_dir: Path) -> None:
    """Write a tiny UE 5.8 C++ fixture skeleton when local base fixtures are absent."""
    module = fixture_dir / "Source" / "HoldoutFixture"
    _write(
        fixture_dir / "HoldoutFixture.uproject",
        """{
  "FileVersion": 3,
  "EngineAssociation": "5.8",
  "Category": "",
  "Description": "",
  "Modules": [
    {
      "Name": "HoldoutFixture",
      "Type": "Runtime",
      "LoadingPhase": "Default"
    }
  ]
}
""",
    )
    _write(
        fixture_dir / "Source" / "HoldoutFixture.Target.cs",
        """using UnrealBuildTool;
using System.Collections.Generic;

public class HoldoutFixtureTarget : TargetRules
{
	public HoldoutFixtureTarget(TargetInfo Target) : base(Target)
	{
		Type = TargetType.Game;
		DefaultBuildSettings = BuildSettingsVersion.V5;
		ExtraModuleNames.Add("HoldoutFixture");
	}
}
""",
    )
    _write(
        fixture_dir / "Source" / "HoldoutFixtureEditor.Target.cs",
        """using UnrealBuildTool;
using System.Collections.Generic;

public class HoldoutFixtureEditorTarget : TargetRules
{
	public HoldoutFixtureEditorTarget(TargetInfo Target) : base(Target)
	{
		Type = TargetType.Editor;
		DefaultBuildSettings = BuildSettingsVersion.V5;
		ExtraModuleNames.Add("HoldoutFixture");
	}
}
""",
    )
    _write(
        module / "HoldoutFixture.Build.cs",
        """using UnrealBuildTool;

public class HoldoutFixture : ModuleRules
{
	public HoldoutFixture(ReadOnlyTargetRules Target) : base(Target)
	{
		PCHUsage = PCHUsageMode.UseExplicitOrSharedPCHs;
		PublicDependencyModuleNames.AddRange(new string[] { "Core", "CoreUObject", "Engine" });
	}
}
""",
    )
    _write(
        module / "Public" / "HoldoutFixture.h",
        """#pragma once

#include "CoreMinimal.h"
""",
    )
    _write(
        module / "Private" / "HoldoutFixture.cpp",
        """#include "HoldoutFixture.h"

IMPLEMENT_PRIMARY_GAME_MODULE(FDefaultGameModuleImpl, HoldoutFixture, "HoldoutFixture");
""",
    )


def _copy_base_fixture(fixture_dir: Path) -> None:
    if not BASE_FIXTURE.is_dir():
        _write_minimal_base_fixture(fixture_dir)
        return
    fixture_dir.mkdir(parents=True, exist_ok=True)
    for item in BASE_FIXTURE.iterdir():
        dest = fixture_dir / item.name
        if item.is_dir():
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(item, dest)
        else:
            shutil.copy2(item, dest)


def _source_paths(fixture_dir: Path, stem: str) -> tuple[Path, Path]:
    module = fixture_dir / "Source" / "HoldoutFixture"
    return module / "Public" / f"{stem}.h", module / "Private" / f"{stem}.cpp"


def write_fixture_case(case_id: str, fixture_root: Path = DEFAULT_FIXTURE_ROOT) -> Path:
    fixture_dir = fixture_root / case_id
    _copy_base_fixture(fixture_dir)
    writers = {
        "local_gameplaytags_missing_module": _write_gameplaytags_fixture,
        "local_enhanced_input_missing_module": _write_enhanced_input_fixture,
        "local_generated_h_not_last": _write_generated_h_order_fixture,
        "local_header_cpp_signature_mismatch": _write_header_cpp_signature_fixture,
        "local_lnk2019_missing_cpp_definition": _write_lnk_missing_definition_fixture,
        "local_umg_missing_module": _write_umg_fixture,
        "local_niagara_missing_module": _write_niagara_fixture,
        "local_aimodule_missing_module": _write_ai_fixture,
        "local_navigation_system_missing_module": _write_navigation_fixture,
        "local_levelsequence_missing_module": _write_levelsequence_fixture,
        "local_blueprint_native_event_missing_implementation": _write_blueprint_native_event_fixture,
        "local_editor_only_runtime_boundary": _write_editor_only_fixture,
        "local_include_path_wrong_owner": _write_include_path_fixture,
        "local_blueprint_implementable_event_native_impl": _write_blueprint_implementable_event_fixture,
        "local_delegate_broadcast_signature_mismatch": _write_delegate_broadcast_fixture,
        "local_component_registration_missing_include": _write_component_registration_fixture,
        "local_game_instance_subsystem_missing_include": _write_subsystem_include_fixture,
        "local_plugin_projects_missing_module": _write_projects_module_fixture,
        "local_uobject_lifecycle_missing_include": _write_uobject_lifecycle_fixture,
        "local_multifile_method_rename_header_cpp_callsite": _write_multifile_method_rename_fixture,
        "local_multifile_delegate_signature_update": _write_multifile_delegate_fixture,
        "local_multifile_interface_signature_update": _write_multifile_interface_fixture,
        "local_multifile_component_api_move": _write_multifile_component_api_fixture,
        "local_common_const_signature_mismatch": _write_const_signature_fixture,
    }
    writer = writers.get(case_id)
    if not writer:
        return fixture_dir
    writer(fixture_dir)
    return fixture_dir


def write_fixture_cases(case_ids: list[str], fixture_root: Path = DEFAULT_FIXTURE_ROOT) -> list[Path]:
    return [write_fixture_case(case_id, fixture_root) for case_id in case_ids]


def _write_gameplaytags_fixture(fixture_dir: Path) -> None:
    header, cpp = _source_paths(fixture_dir, "HoldoutGameplayTagComponent")
    _write(
        header,
        """#pragma once

#include "CoreMinimal.h"
#include "Components/ActorComponent.h"
#include "GameplayTagContainer.h"
#include "HoldoutGameplayTagComponent.generated.h"

UCLASS(ClassGroup=(Holdout), meta=(BlueprintSpawnableComponent))
class HOLDOUTFIXTURE_API UHoldoutGameplayTagComponent : public UActorComponent
{
	GENERATED_BODY()

public:
	UPROPERTY(EditAnywhere, Category="Holdout")
	FGameplayTag RequiredTag;
};
""",
    )
    _write(cpp, '#include "HoldoutGameplayTagComponent.h"\n')
    _write(
        fixture_dir / "request.txt",
        "Fix the Unreal C++ compile error. GameplayTagContainer.h cannot be found; read the owner Build.cs and add GameplayTags only if missing.\n",
    )


def _write_enhanced_input_fixture(fixture_dir: Path) -> None:
    header, cpp = _source_paths(fixture_dir, "HoldoutEnhancedInputComponent")
    _write(
        header,
        """#pragma once

#include "CoreMinimal.h"
#include "Components/ActorComponent.h"
#include "EnhancedInputComponent.h"
#include "HoldoutEnhancedInputComponent.generated.h"

UCLASS(ClassGroup=(Holdout), meta=(BlueprintSpawnableComponent))
class HOLDOUTFIXTURE_API UHoldoutEnhancedInputComponent : public UActorComponent
{
	GENERATED_BODY()

public:
	void ConfigureInput(UEnhancedInputComponent* InputComponent);
};
""",
    )
    _write(
        cpp,
        """#include "HoldoutEnhancedInputComponent.h"

void UHoldoutEnhancedInputComponent::ConfigureInput(UEnhancedInputComponent* InputComponent)
{
	(void)InputComponent;
}
""",
    )
    _write(
        fixture_dir / "request.txt",
        "Fix the missing EnhancedInput module dependency. Patch HoldoutFixture.Build.cs, not PlayerController behavior.\n",
    )


def _write_generated_h_order_fixture(fixture_dir: Path) -> None:
    header, cpp = _source_paths(fixture_dir, "HoldoutGeneratedOrderComponent")
    _write(
        header,
        """#pragma once

#include "CoreMinimal.h"
#include "HoldoutGeneratedOrderComponent.generated.h"
#include "Components/ActorComponent.h"

UCLASS(ClassGroup=(Holdout), meta=(BlueprintSpawnableComponent))
class HOLDOUTFIXTURE_API UHoldoutGeneratedOrderComponent : public UActorComponent
{
	GENERATED_BODY()
};
""",
    )
    _write(cpp, '#include "HoldoutGeneratedOrderComponent.h"\n')
    _write(
        fixture_dir / "request.txt",
        "Fix the UHT include ordering error. The .generated.h include must be the last include in the reflected header.\n",
    )


def _write_header_cpp_signature_fixture(fixture_dir: Path) -> None:
    header, cpp = _source_paths(fixture_dir, "HoldoutDashComponent")
    _write(
        header,
        """#pragma once

#include "CoreMinimal.h"
#include "Components/ActorComponent.h"
#include "HoldoutDashComponent.generated.h"

UCLASS(ClassGroup=(Holdout), meta=(BlueprintSpawnableComponent))
class HOLDOUTFIXTURE_API UHoldoutDashComponent : public UActorComponent
{
	GENERATED_BODY()

public:
	void ApplyDash(float Strength);
};
""",
    )
    _write(
        cpp,
        """#include "HoldoutDashComponent.h"

void UHoldoutDashComponent::ApplyDash(int32 Strength)
{
	(void)Strength;
}
""",
    )
    _write(
        fixture_dir / "request.txt",
        "Fix the header/cpp signature mismatch. Read both files and align the cpp definition with the header declaration. Do not edit Build.cs.\n",
    )


def _write_lnk_missing_definition_fixture(fixture_dir: Path) -> None:
    header, cpp = _source_paths(fixture_dir, "HoldoutMissingDefinitionComponent")
    _write(
        header,
        """#pragma once

#include "CoreMinimal.h"
#include "Components/ActorComponent.h"
#include "HoldoutMissingDefinitionComponent.generated.h"

UCLASS(ClassGroup=(Holdout), meta=(BlueprintSpawnableComponent))
class HOLDOUTFIXTURE_API UHoldoutMissingDefinitionComponent : public UActorComponent
{
	GENERATED_BODY()

public:
	void StartDash();
	void TriggerDash();
};
""",
    )
    _write(
        cpp,
        """#include "HoldoutMissingDefinitionComponent.h"

void UHoldoutMissingDefinitionComponent::TriggerDash()
{
	StartDash();
}
""",
    )
    _write(
        fixture_dir / "request.txt",
        "Fix the LNK2019 missing cpp definition by adding the missing StartDash implementation in the matching cpp file. Do not edit Build.cs.\n",
    )


def _write_umg_fixture(fixture_dir: Path) -> None:
    header, cpp = _source_paths(fixture_dir, "HoldoutWidgetHostComponent")
    _write(
        header,
        """#pragma once

#include "CoreMinimal.h"
#include "Components/ActorComponent.h"
#include "Blueprint/UserWidget.h"
#include "HoldoutWidgetHostComponent.generated.h"

UCLASS(ClassGroup=(Holdout), meta=(BlueprintSpawnableComponent))
class HOLDOUTFIXTURE_API UHoldoutWidgetHostComponent : public UActorComponent
{
	GENERATED_BODY()

public:
	UPROPERTY(EditAnywhere, BlueprintReadOnly, Category="Holdout")
	TSubclassOf<UUserWidget> WidgetClass;
};
""",
    )
    _write(cpp, '#include "HoldoutWidgetHostComponent.h"\n')
    _write(
        fixture_dir / "request.txt",
        "Public header uses UUserWidget. Add the missing UMG module dependency to HoldoutFixture.Build.cs.\n",
    )


def _write_niagara_fixture(fixture_dir: Path) -> None:
    header, cpp = _source_paths(fixture_dir, "HoldoutNiagaraHostComponent")
    _write(
        header,
        """#pragma once

#include "CoreMinimal.h"
#include "Components/ActorComponent.h"
#include "NiagaraComponent.h"
#include "HoldoutNiagaraHostComponent.generated.h"

UCLASS(ClassGroup=(Holdout), meta=(BlueprintSpawnableComponent))
class HOLDOUTFIXTURE_API UHoldoutNiagaraHostComponent : public UActorComponent
{
	GENERATED_BODY()

public:
	UPROPERTY(EditAnywhere, BlueprintReadOnly, Category="Holdout")
	TObjectPtr<UNiagaraComponent> NiagaraComponent;
};
""",
    )
    _write(cpp, '#include "HoldoutNiagaraHostComponent.h"\n')
    _write(
        fixture_dir / "request.txt",
        "Public header uses UNiagaraComponent. Add the missing Niagara module dependency to HoldoutFixture.Build.cs.\n",
    )


def _write_ai_fixture(fixture_dir: Path) -> None:
    header, cpp = _source_paths(fixture_dir, "HoldoutAIProbeComponent")
    _write(
        header,
        """#pragma once

#include "CoreMinimal.h"
#include "Components/ActorComponent.h"
#include "AIController.h"
#include "HoldoutAIProbeComponent.generated.h"

UCLASS(ClassGroup=(Holdout), meta=(BlueprintSpawnableComponent))
class HOLDOUTFIXTURE_API UHoldoutAIProbeComponent : public UActorComponent
{
	GENERATED_BODY()

public:
	UPROPERTY(EditAnywhere, BlueprintReadOnly, Category="Holdout")
	TObjectPtr<AAIController> CachedController;
};
""",
    )
    _write(cpp, '#include "HoldoutAIProbeComponent.h"\n')
    _write(
        fixture_dir / "request.txt",
        "Public header uses AAIController. Add the missing AIModule dependency to HoldoutFixture.Build.cs.\n",
    )


def _write_navigation_fixture(fixture_dir: Path) -> None:
    header, cpp = _source_paths(fixture_dir, "HoldoutNavigationProbeComponent")
    _write(
        header,
        """#pragma once

#include "CoreMinimal.h"
#include "Components/ActorComponent.h"
#include "HoldoutNavigationProbeComponent.generated.h"

UCLASS(ClassGroup=(Holdout), meta=(BlueprintSpawnableComponent))
class HOLDOUTFIXTURE_API UHoldoutNavigationProbeComponent : public UActorComponent
{
	GENERATED_BODY()

public:
	UFUNCTION(BlueprintCallable, Category="Holdout")
	bool HasNavigationSystem() const;
};
""",
    )
    _write(
        cpp,
        """#include "HoldoutNavigationProbeComponent.h"
#include "NavigationSystem.h"

bool UHoldoutNavigationProbeComponent::HasNavigationSystem() const
{
	return UNavigationSystemV1::GetCurrent(GetWorld()) != nullptr;
}
""",
    )
    _write(
        fixture_dir / "request.txt",
        "Source uses UNavigationSystemV1 from NavigationSystem.h. Add the missing NavigationSystem module dependency to HoldoutFixture.Build.cs.\n",
    )


def _write_levelsequence_fixture(fixture_dir: Path) -> None:
    header, cpp = _source_paths(fixture_dir, "HoldoutLevelSequenceProbeComponent")
    _write(
        header,
        """#pragma once

#include "CoreMinimal.h"
#include "Components/ActorComponent.h"
#include "LevelSequence.h"
#include "HoldoutLevelSequenceProbeComponent.generated.h"

UCLASS(ClassGroup=(Holdout), meta=(BlueprintSpawnableComponent))
class HOLDOUTFIXTURE_API UHoldoutLevelSequenceProbeComponent : public UActorComponent
{
	GENERATED_BODY()

public:
	UPROPERTY(EditAnywhere, BlueprintReadOnly, Category="Holdout")
	TObjectPtr<ULevelSequence> Sequence;
};
""",
    )
    _write(cpp, '#include "HoldoutLevelSequenceProbeComponent.h"\n')
    _write(
        fixture_dir / "request.txt",
        "Public header uses ULevelSequence. Add the missing LevelSequence module dependency to HoldoutFixture.Build.cs. If live UBT proves this needs extra modules, report the blocker instead of inventing unrelated code.\n",
    )


def _write_blueprint_native_event_fixture(fixture_dir: Path) -> None:
    header, cpp = _source_paths(fixture_dir, "HoldoutNativeEventComponent")
    _write(
        header,
        """#pragma once

#include "CoreMinimal.h"
#include "Components/ActorComponent.h"
#include "HoldoutNativeEventComponent.generated.h"

UCLASS(ClassGroup=(Holdout), meta=(BlueprintSpawnableComponent))
class HOLDOUTFIXTURE_API UHoldoutNativeEventComponent : public UActorComponent
{
	GENERATED_BODY()

public:
	UFUNCTION(BlueprintNativeEvent, Category="Holdout")
	void OnHoldoutEvent();
};
""",
    )
    _write(cpp, '#include "HoldoutNativeEventComponent.h"\n')
    _write(
        fixture_dir / "request.txt",
        "Fix the BlueprintNativeEvent implementation gap. Add the missing OnHoldoutEvent_Implementation definition in the matching cpp file.\n",
    )


def _write_editor_only_fixture(fixture_dir: Path) -> None:
    header, cpp = _source_paths(fixture_dir, "HoldoutEditorBoundaryComponent")
    _write(
        header,
        """#pragma once

#include "CoreMinimal.h"
#include "Components/ActorComponent.h"
#include "UnrealEd.h"
#include "HoldoutEditorBoundaryComponent.generated.h"

UCLASS(ClassGroup=(Holdout), meta=(BlueprintSpawnableComponent))
class HOLDOUTFIXTURE_API UHoldoutEditorBoundaryComponent : public UActorComponent
{
	GENERATED_BODY()
};
""",
    )
    _write(cpp, '#include "HoldoutEditorBoundaryComponent.h"\n')
    _write(
        fixture_dir / "request.txt",
        "Fix the runtime module boundary error caused by editor-only UnrealEd usage. Do not blindly add UnrealEd to the runtime module; remove or guard the editor-only dependency.\n",
    )


def _write_include_path_fixture(fixture_dir: Path) -> None:
    header, cpp = _source_paths(fixture_dir, "HoldoutIncludeOwnerComponent")
    _write(
        header,
        """#pragma once

#include "CoreMinimal.h"
#include "Components/ActorComponent.h"
#include "BoxComponent.h"
#include "HoldoutIncludeOwnerComponent.generated.h"

UCLASS(ClassGroup=(Holdout), meta=(BlueprintSpawnableComponent))
class HOLDOUTFIXTURE_API UHoldoutIncludeOwnerComponent : public UActorComponent
{
	GENERATED_BODY()

public:
	UPROPERTY(VisibleAnywhere, Category="Holdout")
	TObjectPtr<UBoxComponent> Box;
};
""",
    )
    _write(cpp, '#include "HoldoutIncludeOwnerComponent.h"\n')
    _write(
        fixture_dir / "request.txt",
        "Fix the missing include owner path. Replace BoxComponent.h with Components/BoxComponent.h; do not edit Build.cs.\n",
    )


def _write_blueprint_implementable_event_fixture(fixture_dir: Path) -> None:
    header, cpp = _source_paths(fixture_dir, "HoldoutImplementableEventComponent")
    _write(
        header,
        """#pragma once

#include "CoreMinimal.h"
#include "Components/ActorComponent.h"
#include "HoldoutImplementableEventComponent.generated.h"

UCLASS(ClassGroup=(Holdout), meta=(BlueprintSpawnableComponent))
class HOLDOUTFIXTURE_API UHoldoutImplementableEventComponent : public UActorComponent
{
	GENERATED_BODY()

public:
	UFUNCTION(BlueprintImplementableEvent, Category="Holdout")
	void OnHoldoutBlueprintEvent();
};
""",
    )
    _write(
        cpp,
        """#include "HoldoutImplementableEventComponent.h"

void UHoldoutImplementableEventComponent::OnHoldoutBlueprintEvent_Implementation()
{
}
""",
    )
    _write(
        fixture_dir / "request.txt",
        "Fix the BlueprintImplementableEvent native implementation compile error. Prefer removing the invalid _Implementation body unless the declaration is intentionally changed to BlueprintNativeEvent.\n",
    )


def _write_delegate_broadcast_fixture(fixture_dir: Path) -> None:
    header, cpp = _source_paths(fixture_dir, "HoldoutScoreDelegateComponent")
    _write(
        header,
        """#pragma once

#include "CoreMinimal.h"
#include "Components/ActorComponent.h"
#include "HoldoutScoreDelegateComponent.generated.h"

DECLARE_DYNAMIC_MULTICAST_DELEGATE_OneParam(FOnHoldoutScoreChanged, int32, Score);

UCLASS(ClassGroup=(Holdout), meta=(BlueprintSpawnableComponent))
class HOLDOUTFIXTURE_API UHoldoutScoreDelegateComponent : public UActorComponent
{
	GENERATED_BODY()

public:
	UPROPERTY(BlueprintAssignable, Category="Holdout")
	FOnHoldoutScoreChanged OnScoreChanged;

	void TriggerScore();
};
""",
    )
    _write(
        cpp,
        """#include "HoldoutScoreDelegateComponent.h"

void UHoldoutScoreDelegateComponent::TriggerScore()
{
	OnScoreChanged.Broadcast();
}
""",
    )
    _write(
        fixture_dir / "request.txt",
        "Fix the delegate Broadcast call to match FOnHoldoutScoreChanged, which requires the Score payload.\n",
    )


def _write_component_registration_fixture(fixture_dir: Path) -> None:
    header, cpp = _source_paths(fixture_dir, "HoldoutBoxActor")
    _write(
        header,
        """#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Actor.h"
#include "HoldoutBoxActor.generated.h"

class UBoxComponent;

UCLASS()
class HOLDOUTFIXTURE_API AHoldoutBoxActor : public AActor
{
	GENERATED_BODY()

public:
	AHoldoutBoxActor();

private:
	UPROPERTY(VisibleAnywhere, Category="Holdout")
	TObjectPtr<UBoxComponent> Box;
};
""",
    )
    _write(
        cpp,
        """#include "HoldoutBoxActor.h"

AHoldoutBoxActor::AHoldoutBoxActor()
{
	Box = CreateDefaultSubobject<UBoxComponent>(TEXT("Box"));
	RootComponent = Box;
}
""",
    )
    _write(
        fixture_dir / "request.txt",
        "Fix the component registration compile error by including the concrete UBoxComponent header in the cpp. Do not edit Build.cs.\n",
    )


def _write_subsystem_include_fixture(fixture_dir: Path) -> None:
    header, cpp = _source_paths(fixture_dir, "HoldoutGlobalSubsystem")
    _write(
        header,
        """#pragma once

#include "CoreMinimal.h"
#include "HoldoutGlobalSubsystem.generated.h"

UCLASS()
class HOLDOUTFIXTURE_API UHoldoutGlobalSubsystem : public UGameInstanceSubsystem
{
	GENERATED_BODY()
};
""",
    )
    _write(cpp, '#include "HoldoutGlobalSubsystem.h"\n')
    _write(
        fixture_dir / "request.txt",
        "Fix the subsystem base class compile error by adding the correct GameInstanceSubsystem include to the header.\n",
    )


def _write_projects_module_fixture(fixture_dir: Path) -> None:
    header, cpp = _source_paths(fixture_dir, "HoldoutPluginProbeComponent")
    _write(
        header,
        """#pragma once

#include "CoreMinimal.h"
#include "Components/ActorComponent.h"
#include "HoldoutPluginProbeComponent.generated.h"

UCLASS(ClassGroup=(Holdout), meta=(BlueprintSpawnableComponent))
class HOLDOUTFIXTURE_API UHoldoutPluginProbeComponent : public UActorComponent
{
	GENERATED_BODY()

public:
	bool HasAnyPlugin() const;
};
""",
    )
    _write(
        cpp,
        """#include "HoldoutPluginProbeComponent.h"
#include "Interfaces/IPluginManager.h"

bool UHoldoutPluginProbeComponent::HasAnyPlugin() const
{
	return IPluginManager::Get().GetDiscoveredPlugins().Num() > 0;
}
""",
    )
    _write(
        fixture_dir / "request.txt",
        "Fix the missing Projects module dependency for IPluginManager. Patch HoldoutFixture.Build.cs only.\n",
    )


def _write_uobject_lifecycle_fixture(fixture_dir: Path) -> None:
    header, cpp = _source_paths(fixture_dir, "HoldoutObjectFactoryComponent")
    _write(
        header,
        """#pragma once

#include "CoreMinimal.h"
#include "Components/ActorComponent.h"
#include "HoldoutObjectFactoryComponent.generated.h"

UCLASS(ClassGroup=(Holdout), meta=(BlueprintSpawnableComponent))
class HOLDOUTFIXTURE_API UHoldoutObjectFactoryComponent : public UActorComponent
{
	GENERATED_BODY()

public:
	UObject* CreateRuntimeObject();
};
""",
    )
    _write(
        cpp,
        """#include "HoldoutObjectFactoryComponent.h"

#define NewObject MissingNewObjectInclude

UObject* UHoldoutObjectFactoryComponent::CreateRuntimeObject()
{
	return NewObject<UObject>(this);
}
""",
    )
    _write(
        fixture_dir / "request.txt",
        "Fix the UObject lifecycle compile error by removing the bad local NewObject macro and using the proper UObject creation API include/usage.\n",
    )


def _write_multifile_method_rename_fixture(fixture_dir: Path) -> None:
    header, cpp = _source_paths(fixture_dir, "HoldoutRefactorComponent")
    _, consumer = _source_paths(fixture_dir, "HoldoutRefactorConsumer")
    _write(
        header,
        """#pragma once

#include "CoreMinimal.h"
#include "Components/ActorComponent.h"
#include "HoldoutRefactorComponent.generated.h"

UCLASS(ClassGroup=(Holdout), meta=(BlueprintSpawnableComponent))
class HOLDOUTFIXTURE_API UHoldoutRefactorComponent : public UActorComponent
{
	GENERATED_BODY()

public:
	void StartCharge();
};
""",
    )
    _write(
        cpp,
        """#include "HoldoutRefactorComponent.h"

void UHoldoutRefactorComponent::BeginCharge()
{
}
""",
    )
    _write(
        consumer,
        """#include "HoldoutRefactorComponent.h"

void UseHoldoutRefactor(UHoldoutRefactorComponent* Component)
{
	if (Component)
	{
		Component->BeginCharge();
	}
}
""",
    )
    _write(
        fixture_dir / "request.txt",
        "Fix the small multi-file refactor drift by aligning the method name across header, cpp definition, and call site.\n",
    )


def _write_multifile_delegate_fixture(fixture_dir: Path) -> None:
    header, cpp = _source_paths(fixture_dir, "HoldoutDelegateOwner")
    _write(
        header,
        """#pragma once

#include "CoreMinimal.h"

class FHoldoutDelegateOwner
{
public:
	void HandleScoreChanged();
};
""",
    )
    _write(
        cpp,
        """#include "HoldoutDelegateOwner.h"

void FHoldoutDelegateOwner::HandleScoreChanged(int32 Score)
{
	(void)Score;
}
""",
    )
    _write(
        fixture_dir / "request.txt",
        "Fix the multi-file delegate handler signature drift by updating declaration and definition consistently.\n",
    )


def _write_multifile_interface_fixture(fixture_dir: Path) -> None:
    interface_h, _ = _source_paths(fixture_dir, "HoldoutActionInterface")
    impl_h, impl_cpp = _source_paths(fixture_dir, "HoldoutActionImplementer")
    _write(
        interface_h,
        """#pragma once

#include "CoreMinimal.h"

class IHoldoutActionInterface
{
public:
	virtual ~IHoldoutActionInterface() = default;
	virtual void ApplyInteraction(float Strength) = 0;
};
""",
    )
    _write(
        impl_h,
        """#pragma once

#include "CoreMinimal.h"
#include "HoldoutActionInterface.h"

class FHoldoutActionImplementer : public IHoldoutActionInterface
{
public:
	void ApplyInteraction(int32 Strength) override;
};
""",
    )
    _write(
        impl_cpp,
        """#include "HoldoutActionImplementer.h"

void FHoldoutActionImplementer::ApplyInteraction(int32 Strength)
{
	(void)Strength;
}
""",
    )
    _write(
        fixture_dir / "request.txt",
        "Fix the multi-file interface signature mismatch by aligning the implementer header and cpp with the interface declaration.\n",
    )


def _write_multifile_component_api_fixture(fixture_dir: Path) -> None:
    header, cpp = _source_paths(fixture_dir, "HoldoutApiComponent")
    _, consumer = _source_paths(fixture_dir, "HoldoutApiConsumer")
    _write(
        header,
        """#pragma once

#include "CoreMinimal.h"
#include "Components/ActorComponent.h"
#include "HoldoutApiComponent.generated.h"

UCLASS(ClassGroup=(Holdout), meta=(BlueprintSpawnableComponent))
class HOLDOUTFIXTURE_API UHoldoutApiComponent : public UActorComponent
{
	GENERATED_BODY()

public:
	void ApplyValue(float Value);
};
""",
    )
    _write(
        cpp,
        """#include "HoldoutApiComponent.h"

void UHoldoutApiComponent::ApplyMovedValue(float Value)
{
	(void)Value;
}
""",
    )
    _write(
        consumer,
        """#include "HoldoutApiComponent.h"

void UseHoldoutApi(UHoldoutApiComponent* Component)
{
	if (Component)
	{
		Component->ApplyMovedValue(1.0f);
	}
}
""",
    )
    _write(
        fixture_dir / "request.txt",
        "Fix the component API move across declaration, definition, and consumer call site. Keep the change minimal.\n",
    )


def _write_const_signature_fixture(fixture_dir: Path) -> None:
    header, cpp = _source_paths(fixture_dir, "HoldoutConstComponent")
    _write(
        header,
        """#pragma once

#include "CoreMinimal.h"
#include "Components/ActorComponent.h"
#include "HoldoutConstComponent.generated.h"

UCLASS(ClassGroup=(Holdout), meta=(BlueprintSpawnableComponent))
class HOLDOUTFIXTURE_API UHoldoutConstComponent : public UActorComponent
{
	GENERATED_BODY()

public:
	int32 GetCount() const;
};
""",
    )
    _write(
        cpp,
        """#include "HoldoutConstComponent.h"

int32 UHoldoutConstComponent::GetCount()
{
	return 0;
}
""",
    )
    _write(
        fixture_dir / "request.txt",
        "Fix the const qualifier mismatch between the header declaration and cpp definition. Do not edit Build.cs.\n",
    )


def next_step_text(output_path: Path, model: str = DEFAULT_MODEL, ubt_path: Path | None = None) -> str:
    config = output_path.as_posix()
    ubt_arg = str(ubt_path) if ubt_path else "<UnrealBuildTool.exe>"
    return "\n".join(
        [
            "Next steps:",
            f"python scripts/validate_holdout_cases.py --config {config} --allow-local-paths",
            "python scripts/build_symbol_graph.py",
            f"python scripts/eval_pass_at_k.py --metrics-only --config {config}",
            (
                "python scripts/eval_pass_at_k.py --live --require-live "
                f"--config {config} --model {model} --ubt-path \"{ubt_arg}\" --wrapper-timeout 1800"
            ),
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Bootstrap ignored local live holdout config.")
    parser.add_argument("--suite", choices=["5", "12", "24"], default="5", help="Local holdout suite size to prepare.")
    parser.add_argument("--project-file", default="", help="Path to the local .uproject for all generated cases.")
    parser.add_argument("--fixture-root", default="", help="Root containing one fixture directory per case id.")
    parser.add_argument("--suite-name", default=DEFAULT_SUITE_NAME)
    parser.add_argument("--force", action="store_true", help="Overwrite existing local config.")
    parser.add_argument("--example-config", type=Path, default=DEFAULT_EXAMPLE)
    parser.add_argument("--output-config", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    fixture_root = args.fixture_root
    project_file = args.project_file
    if args.suite in {"12", "24"}:
        fixture_root = fixture_root or DEFAULT_FIXTURE_ROOT.as_posix()
        project_file = project_file or "HoldoutFixture.uproject"

    try:
        write_local_config(
            example_path=args.example_config,
            output_path=args.output_config,
            project_file=project_file,
            fixture_root=fixture_root,
            suite_name=args.suite_name,
            suite=args.suite,
            force=args.force,
        )
        created = []
        if args.suite in {"12", "24"}:
            fixture_cases = load_json(args.output_config).get("cases") or []
            created = write_fixture_cases(
                [str(case["id"]) for case in fixture_cases],
                Path(fixture_root),
            )
    except FileExistsError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"failed to bootstrap local holdout config: {exc}", file=sys.stderr)
        return 1

    print(f"Wrote {args.output_config}")
    if args.suite in {"12", "24"}:
        print(f"Prepared {len(created)} local fixture directories under {fixture_root}")
    detected_ubt = detect_ubt_path()
    if detected_ubt:
        print(f"Detected UBT candidate: {detected_ubt}")
    else:
        print("UE 5.8 UBT candidate not detected; pass --ubt-path explicitly for live eval.")
    print(next_step_text(args.output_config, ubt_path=detected_ubt))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
