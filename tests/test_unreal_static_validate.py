from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from unreal_static_validate import (  # noqa: E402
    Finding,
    validate_duplicate_source_basenames,
    validate_include_paths_exist,
    validate_unreal_readiness,
    validate_generated_h,
    validate_delegate_broadcast_consistency,
    validate_uht_macros_in_conditional_blocks,
    validate_gengine_world_context,
    validate_static_mutable_container_members,
    build_source_include_index,
    has_static_errors,
    should_block_llm_apply_static_gate,
)
from retry_feedback import static_validation_retry_feedback  # noqa: E402
from bootstrap_local_holdout import write_fixture_case  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "compile_fix_ceiling" / "missing_gameplaytags_dep"


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_duplicate_source_basename_detected(tmp_path: Path) -> None:
    project = tmp_path / "Demo"
    _write(
        project / "Source" / "Demo" / "Public" / "Foo" / "HealthComponent.h",
        '#include "HealthComponent.generated.h"\n',
    )
    _write(
        project / "Source" / "Demo" / "Public" / "Bar" / "HealthComponent.h",
        '#include "HealthComponent.generated.h"\n',
    )

    findings = validate_duplicate_source_basenames(project)
    codes = {item.code for item in findings}

    assert "DUPLICATE_SOURCE_BASENAME" in codes
    assert has_static_errors(findings)


def test_include_path_not_found_detected(tmp_path: Path) -> None:
    project = tmp_path / "Demo"
    cpp = project / "Source" / "Demo" / "Private" / "Enemy.cpp"
    _write(
        project / "Source" / "Demo" / "Public" / "SharedComponent" / "HealthComponent.h",
        '#include "HealthComponent.generated.h"\n',
    )
    _write(cpp, '#include "Character/Player/Component/HealthComponent.h"\n')

    include_index = build_source_include_index(project)
    findings = validate_include_paths_exist(cpp, cpp.read_text(encoding="utf-8"), project, include_index)

    assert any(item.code == "INCLUDE_PATH_NOT_FOUND" for item in findings)


def test_subsystems_include_not_flagged_as_missing(tmp_path: Path) -> None:
    project = tmp_path / "Demo"
    header = project / "Source" / "Demo" / "Public" / "DemoSubsystem.h"
    header.parent.mkdir(parents=True, exist_ok=True)
    header.write_text(
        '#pragma once\n#include "CoreMinimal.h"\n#include "Subsystems/GameInstanceSubsystem.h"\n',
        encoding="utf-8",
    )
    findings = validate_unreal_readiness(project)
    assert not any(item.code == "INCLUDE_PATH_NOT_FOUND" for item in findings)


def test_ueditorengine_include_in_runtime_module_is_editor_boundary_error(tmp_path: Path) -> None:
    project = tmp_path / "Demo"
    cpp = project / "Source" / "Demo" / "Private" / "EditorGuard.cpp"
    _write(
        cpp,
        """#include "UEditorEngine.h"

void RefreshEditorPreview()
{
}
""",
    )

    findings = validate_unreal_readiness(project, skip_include_path_checks=True)

    assert any(item.code == "EDITOR_ONLY_INCLUDE_IN_RUNTIME_MODULE" for item in findings)


def test_module_fix_build_cs_patch_skips_include_path_gate(tmp_path: Path) -> None:
    project = tmp_path / "CompileFixTags"
    source_root = FIXTURE / "Source"
    for path in source_root.rglob("*"):
        if path.is_file():
            rel = path.relative_to(FIXTURE)
            dest = project / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    (project / "CompileFixTags.uproject").write_text(
        (FIXTURE / "CompileFixTags.uproject").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    before = validate_unreal_readiness(project)
    assert any(item.code == "INCLUDE_PATH_NOT_FOUND" for item in before)

    build_cs = project / "Source" / "CompileFixTags" / "CompileFixTags.Build.cs"
    build_cs.write_text(
        (FIXTURE / "golden" / "Source" / "CompileFixTags" / "CompileFixTags.Build.cs").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    after = validate_unreal_readiness(project, skip_include_path_checks=True)
    assert not any(item.code == "INCLUDE_PATH_NOT_FOUND" for item in after)
    assert not has_static_errors(after)


def test_ufunction_declaration_with_cpp_definition_not_flagged_missing(tmp_path: Path) -> None:
    project = tmp_path / "NavFixture"
    _write(
        project / "Source" / "HoldoutFixture" / "Public" / "HoldoutNavigationProbeComponent.h",
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
        project / "Source" / "HoldoutFixture" / "Private" / "HoldoutNavigationProbeComponent.cpp",
        """#include "HoldoutNavigationProbeComponent.h"

bool UHoldoutNavigationProbeComponent::HasNavigationSystem() const
{
	return true;
}
""",
    )

    findings = validate_unreal_readiness(project, skip_include_path_checks=True)

    assert not any(
        finding.code == "CPP_DEFINITION_MISSING" and "::UFUNCTION" in finding.message
        for finding in findings
    )
    assert not any(
        finding.code == "CPP_DEFINITION_MISSING" and "HasNavigationSystem" in finding.message
        for finding in findings
    )


def test_module_fix_c1083_retry_feedback_does_not_block_build_cs_edit() -> None:
    feedback = static_validation_retry_feedback(
        [
            Finding(
                "error",
                "Source/HoldoutFixture/Public/HoldoutNavigationProbeComponent.h",
                11,
                "CPP_DEFINITION_MISSING",
                "UHoldoutNavigationProbeComponent::UFUNCTION is declared in the header but has no matching .cpp definition.",
            )
        ],
        {"errorSubkind": "C1083_MISSING_INCLUDE", "broadMode": "module_fix"},
    )

    assert "Do not edit Build.cs" not in feedback


def test_navigation_build_cs_fix_not_blocked_by_static_gate(tmp_path: Path) -> None:
    fixture = write_fixture_case("local_navigation_system_missing_module", tmp_path)
    build_cs = fixture / "Source" / "HoldoutFixture" / "HoldoutFixture.Build.cs"
    golden_build_cs = fixture / "golden" / "Source" / "HoldoutFixture" / "HoldoutFixture.Build.cs"

    build_cs.write_text(golden_build_cs.read_text(encoding="utf-8"), encoding="utf-8")
    findings = validate_unreal_readiness(fixture, skip_include_path_checks=True)

    assert not any(finding.code == "CPP_DEFINITION_MISSING" for finding in findings)
    assert should_block_llm_apply_static_gate(findings, mode="module_fix") is False


def test_generated_h_after_type_detected(tmp_path: Path) -> None:
    project = tmp_path / "Demo"
    header = project / "Source" / "Demo" / "Public" / "FooComponent.h"
    _write(
        header,
        """#pragma once
#include "CoreMinimal.h"
#include "Components/ActorComponent.h"

UCLASS()
class UFooComponent : public UActorComponent
{
	GENERATED_BODY()
};
#include "FooComponent.generated.h"
""",
    )
    findings = validate_generated_h(header, header.read_text(encoding="utf-8"), project)
    assert any(item.code == "GENERATED_H_AFTER_TYPE" for item in findings)


def test_uht_macro_in_shipping_conditional_flagged(tmp_path: Path) -> None:
    project = tmp_path / "Demo"
    header = project / "Source" / "Demo" / "Public" / "DevConsoleSubsystem.h"
    _write(
        header,
        """#pragma once
#include "CoreMinimal.h"
#include "Subsystems/WorldSubsystem.h"
#include "DevConsoleSubsystem.generated.h"

UCLASS()
class DEMO_API UDevConsoleSubsystem : public UWorldSubsystem
{
	GENERATED_BODY()

public:
#if !UE_BUILD_SHIPPING
	UFUNCTION(Exec, Category="Dev")
	void RunDevCommand(const FString& Command);

	UPROPERTY()
	int32 DevCommandCount = 0;
#endif
};
""",
    )

    direct = validate_uht_macros_in_conditional_blocks(header, header.read_text(encoding="utf-8"), project)
    assert len(direct) == 1, "should report once per offending conditional frame"
    assert direct[0].code == "UHT_MACRO_IN_CONDITIONAL_BLOCK"
    assert direct[0].severity == "error"
    assert "WITH_EDITOR" in direct[0].message
    assert ".cpp" in direct[0].message

    readiness = validate_unreal_readiness(project, skip_include_path_checks=True)
    assert any(item.code == "UHT_MACRO_IN_CONDITIONAL_BLOCK" for item in readiness)
    assert has_static_errors(readiness)


def test_uht_macro_in_with_editor_blocks_not_flagged(tmp_path: Path) -> None:
    project = tmp_path / "Demo"
    header = project / "Source" / "Demo" / "Public" / "EditorTunedComponent.h"
    _write(
        header,
        """#pragma once
#include "CoreMinimal.h"
#include "Components/ActorComponent.h"
#include "EditorTunedComponent.generated.h"

UCLASS()
class DEMO_API UEditorTunedComponent : public UActorComponent
{
	GENERATED_BODY()

public:
#if WITH_EDITOR
	UFUNCTION(CallInEditor, Category="Editor")
	void RefreshPreview();
#else
	UPROPERTY()
	int32 RuntimeOnlyCounter = 0;
#endif

#if WITH_EDITORONLY_DATA
	UPROPERTY(EditAnywhere, Category="Editor")
	FString EditorNote;
#endif

	UPROPERTY(EditAnywhere, Category="Gameplay")
	float Strength = 1.0f;
};
""",
    )

    findings = validate_uht_macros_in_conditional_blocks(header, header.read_text(encoding="utf-8"), project)
    assert findings == []


def test_uht_macro_in_nested_disallowed_conditional_flagged(tmp_path: Path) -> None:
    project = tmp_path / "Demo"
    header = project / "Source" / "Demo" / "Public" / "NestedComponent.h"
    _write(
        header,
        """#pragma once
#include "CoreMinimal.h"
#include "Components/ActorComponent.h"
#include "NestedComponent.generated.h"

UCLASS()
class DEMO_API UNestedComponent : public UActorComponent
{
	GENERATED_BODY()

public:
#if WITH_EDITOR
#if !UE_BUILD_SHIPPING
	UFUNCTION(CallInEditor, Category="Dev")
	void DumpDebugState();
#endif
#endif
};
""",
    )

    findings = validate_uht_macros_in_conditional_blocks(header, header.read_text(encoding="utf-8"), project)
    assert len(findings) == 1
    assert findings[0].code == "UHT_MACRO_IN_CONDITIONAL_BLOCK"
    assert "UE_BUILD_SHIPPING" in findings[0].message


def test_uht_macro_in_include_guard_not_flagged(tmp_path: Path) -> None:
    project = tmp_path / "Demo"
    header = project / "Source" / "Demo" / "Public" / "GuardedComponent.h"
    _write(
        header,
        """#ifndef GUARDEDCOMPONENT_H
#define GUARDEDCOMPONENT_H
#include "CoreMinimal.h"
#include "Components/ActorComponent.h"
#include "GuardedComponent.generated.h"

UCLASS()
class DEMO_API UGuardedComponent : public UActorComponent
{
	GENERATED_BODY()
};
#endif
""",
    )

    findings = validate_uht_macros_in_conditional_blocks(header, header.read_text(encoding="utf-8"), project)
    assert findings == []


def test_gengine_world_access_flagged_as_error(tmp_path: Path) -> None:
    project = tmp_path / "Demo"
    cpp = project / "Source" / "Demo" / "Private" / "DevCommandDispatcher.cpp"
    _write(
        cpp,
        """#include "DevCommandDispatcher.h"

void FDevCommandDispatcher::RunSpawnCommand()
{
	UWorld* World = GEngine->GetWorld();
	UGameInstance* Instance = GEngine->GetGameInstance();
}
""",
    )

    direct = validate_gengine_world_context(cpp, cpp.read_text(encoding="utf-8"), project)
    assert len(direct) == 2
    assert all(item.code == "GENGINE_WORLD_CONTEXT" for item in direct)
    assert all(item.severity == "error" for item in direct)
    assert "PIE" in direct[0].message
    assert "GetWorld()" in direct[0].message

    readiness = validate_unreal_readiness(project, skip_include_path_checks=True)
    assert any(item.code == "GENGINE_WORLD_CONTEXT" for item in readiness)


def test_gengine_legitimate_uses_not_flagged(tmp_path: Path) -> None:
    project = tmp_path / "Demo"
    cpp = project / "Source" / "Demo" / "Private" / "DebugDisplay.cpp"
    _write(
        cpp,
        """#include "DebugDisplay.h"

void ShowMessage(UWorld* World)
{
	if (GEngine)
	{
		GEngine->AddOnScreenDebugMessage(-1, 5.f, FColor::Green, TEXT("hello"));
	}
	UWorld* PlayWorld = GEngine->GetCurrentPlayWorld();
	UGameInstance* Instance = World ? World->GetGameInstance() : nullptr;
}
""",
    )

    findings = validate_gengine_world_context(cpp, cpp.read_text(encoding="utf-8"), project)
    assert findings == []


def test_static_mutable_container_member_warned(tmp_path: Path) -> None:
    project = tmp_path / "Demo"
    header = project / "Source" / "Demo" / "Public" / "DevCommandDispatcher.h"
    _write(
        header,
        """#pragma once
#include "CoreMinimal.h"

class FDevCommandDispatcher
{
public:
	static TMap<FString, TFunction<void()>> Commands;
	static const TArray<FString> ReservedNames;
	static constexpr int32 MaxCommands = 32;
	TMap<FString, int32> InstanceCounters;
};
""",
    )

    findings = validate_static_mutable_container_members(header, header.read_text(encoding="utf-8"), project)
    assert len(findings) == 1
    assert findings[0].code == "STATIC_MUTABLE_CONTAINER_MEMBER"
    assert findings[0].severity == "warning"
    assert "world" in findings[0].message.lower()

    readiness = validate_unreal_readiness(project, skip_include_path_checks=True)
    assert any(item.code == "STATIC_MUTABLE_CONTAINER_MEMBER" for item in readiness)
    # warning only: must not flip the project into a blocked/error state by itself
    assert not any(
        item.code == "STATIC_MUTABLE_CONTAINER_MEMBER" and item.severity == "error" for item in readiness
    )


def test_delegate_broadcast_empty_args_detected(tmp_path: Path) -> None:
    project = tmp_path / "Demo"
    cpp = project / "Source" / "Demo" / "Private" / "Score.cpp"
    _write(
        cpp,
        """#include "Score.h"

void Trigger()
{
	OnScoreChanged.Broadcast();
}
""",
    )
    findings = validate_delegate_broadcast_consistency(cpp, cpp.read_text(encoding="utf-8"), project)
    assert any(item.code == "DELEGATE_BROADCAST_SIGNATURE_MISMATCH" for item in findings)

