#!/usr/bin/env python
"""Regression test for Unreal compile-readiness static validation."""

from __future__ import annotations

import tempfile
from pathlib import Path

from lmstudio_unreal_wrapper import validate_unreal_readiness


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def main() -> int:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        write(
            root / "Source" / "Sample" / "Sample.Build.cs",
            """
using UnrealBuildTool;

public class Sample : ModuleRules
{
    public Sample(ReadOnlyTargetRules Target) : base(Target)
    {
        PublicDependencyModuleNames.AddRange(new string[] { "Core", "CoreUObject", "Engine" });
    }
}
""".strip()
            + "\n",
        )
        write(
            root / "Source" / "Sample" / "Public" / "BadReflectedComponent.h",
            """
#pragma once

#include "CoreMinimal.h"
#include "BadReflectedComponent.generated.h"
#include "Components/ActorComponent.h"

namespace BadSample
{
UCLASS()
class SAMPLE_API UBadReflectedComponent : public UActorComponent
{
    GENERATED_BODY()
};
}
""".strip()
            + "\n",
        )
        write(
            root / "Source" / "Sample" / "Public" / "MissingGeneratedStruct.h",
            """
#pragma once

#include "CoreMinimal.h"

USTRUCT(BlueprintType)
struct FMissingGeneratedStruct
{
    GENERATED_BODY()
};
""".strip()
            + "\n",
        )
        write(
            root / "Source" / "Sample" / "Public" / "BadInteractable.h",
            """
#pragma once

#include "CoreMinimal.h"
#include "UObject/Interface.h"
#include "BadInteractable.generated.h"

UINTERFACE(BlueprintType)
class SAMPLE_API UBadInteractable : public UInterface
{
    GENERATED_BODY()
};

class SAMPLE_API IBadInteractable
{
    GENERATED_BODY()

public:
    UFUNCTION(BlueprintNativeEvent)
    bool CanUse(AActor* Instigator) const;

    virtual bool CanUse(AActor* Instigator) const = 0;
};
""".strip()
            + "\n",
        )
        write(
            root / "Source" / "Sample" / "Public" / "BadEditorDependency.h",
            """
#pragma once

#include "CoreMinimal.h"
#include "EditorUtilityWidget.h"
#include "BadEditorDependency.generated.h"

UCLASS()
class SAMPLE_API UBadEditorDependency : public UObject
{
    GENERATED_BODY()
};
""".strip()
            + "\n",
        )
        write(
            root / "Source" / "Sample" / "Public" / "BadLifetimeHolder.h",
            """
#pragma once

#include "CoreMinimal.h"
#include "BadLifetimeHolder.generated.h"

UCLASS()
class SAMPLE_API UBadLifetimeHolder : public UObject
{
    GENERATED_BODY()

private:
    UObject* ActiveEffect;
};
""".strip()
            + "\n",
        )
        write(
            root / "Source" / "Sample" / "Public" / "BadTimerComponent.h",
            """
#pragma once

#include "CoreMinimal.h"
#include "Components/ActorComponent.h"
#include "BadTimerComponent.generated.h"

UCLASS()
class SAMPLE_API UBadTimerComponent : public UActorComponent
{
    GENERATED_BODY()

private:
    FTimerHandle CooldownTimerHandle;
};
""".strip()
            + "\n",
        )
        write(
            root / "Source" / "Sample" / "Private" / "BadTimerComponent.cpp",
            """
#include "BadTimerComponent.h"

void UBadTimerComponent::BeginPlay()
{
    GetWorldTimerManager().ClearTimer(CooldownTimerHandle);
}
""".strip()
            + "\n",
        )
        write(
            root / "Source" / "Sample" / "Public" / "BadLifecycleActor.h",
            """
#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Actor.h"
#include "BadLifecycleActor.generated.h"

UCLASS()
class SAMPLE_API ABadLifecycleActor : public AActor
{
    GENERATED_BODY()

public:
    ABadLifecycleActor();
    virtual void BeginPlay() override;
};
""".strip()
            + "\n",
        )
        write(
            root / "Source" / "Sample" / "Private" / "BadLifecycleActor.cpp",
            """
#include "BadLifecycleActor.h"

ABadLifecycleActor::ABadLifecycleActor()
{
    GetWorld()->SpawnActor<AActor>();
}

void ABadLifecycleActor::BeginPlay()
{
    CreateDefaultSubobject<USceneComponent>(TEXT("LateRoot"));
    UObject* RuntimeObject = NewObject<UObject>();
}
""".strip()
            + "\n",
        )
        write(
            root / "Source" / "Sample" / "Public" / "BadRpcActor.h",
            """
#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Actor.h"
#include "BadRpcActor.generated.h"

UCLASS()
class SAMPLE_API ABadRpcActor : public AActor
{
    GENERATED_BODY()

public:
    UFUNCTION(Server, Reliable)
    void ServerUse();
};
""".strip()
            + "\n",
        )
        write(
            root / "Source" / "Sample" / "Private" / "BadIncludes.cpp",
            """
#include "BadLifecycleActor.h"

void ABadLifecycleActor::MissingIncludes()
{
    UGameplayStatics::OpenLevel(this, TEXT("Map"));
}
""".strip()
            + "\n",
        )
        write(
            root / "Source" / "Sample" / "Public" / "BadWorldSubsystem.h",
            """
#pragma once

#include "CoreMinimal.h"
#include "Subsystems/WorldSubsystem.h"
#include "BadWorldSubsystem.generated.h"

UCLASS()
class SAMPLE_API UBadWorldSubsystem : public UWorldSubsystem
{
    GENERATED_BODY()

protected:
    virtual void OnWorldDestroyed(UWorld* World) override;
};
""".strip()
            + "\n",
        )
        write(
            root / "Source" / "Sample" / "Private" / "BadWorldSubsystem.cpp",
            """
#include "BadWorldSubsystem.h"

UBadWorldSubsystem::UBadWorldSubsystem()
{
    CreateDefaultSubobject<USceneComponent>(TEXT("Illegal"));
}
""".strip()
            + "\n",
        )

        findings = validate_unreal_readiness(root)
        found = {finding.code for finding in findings}
        expected = {
            "GENERATED_H_NOT_LAST",
            "GENERATED_H_MISSING",
            "REFLECTED_TYPE_IN_NAMESPACE",
            "BLUEPRINT_NATIVE_EVENT_DUPLICATE_VIRTUAL",
            "EDITOR_ONLY_INCLUDE_IN_RUNTIME_MODULE",
            "RAW_UOBJECT_MEMBER_WITHOUT_UPROPERTY",
            "MISSING_TIMER_MANAGER_INCLUDE",
            "COMPONENT_GET_WORLD_TIMER_MANAGER",
            "SPAWN_ACTOR_IN_CONSTRUCTOR",
            "CREATE_DEFAULT_SUBOBJECT_OUTSIDE_CONSTRUCTOR",
            "NEWOBJECT_WITHOUT_OUTER",
            "MISSING_CPP_SYMBOL_INCLUDE",
            "RPC_IMPLEMENTATION_MISSING",
            "SUBSYSTEM_CREATE_SUBOBJECT",
            "INVALID_UNREAL_LIFECYCLE_OVERRIDE",
        }
        missing = expected - found
        if missing:
            print(f"[FAIL] missing expected static validation codes: {sorted(missing)}")
            print(f"found: {sorted(found)}")
            return 1
        print(f"[PASS] static validation fixture found {len(expected)} expected codes")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
