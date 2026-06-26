# Unreal Codegen Recipes: Core Types

## Keywords

Unreal C++ codegen, code generation, new class, create component, UActorComponent, AActor, UObject, UDataAsset, UGameInstanceSubsystem, UWorldSubsystem, UInterface, UCLASS, USTRUCT, UENUM, UFUNCTION, UPROPERTY, GENERATED_BODY, constructor, BeginPlay, Tick, Build.cs, include, module dependency

Korean query aliases: 언리얼 코드 생성, 새 컴포넌트 만들기, 새 Actor 만들기, UObject 만들기, DataAsset 만들기, Subsystem 만들기, Interface 만들기, 헤더 include, 모듈 dependency, Build.cs

## Purpose

Use this document when the model must generate Unreal C++ code before an exact project exists. The answer should prefer a small compiling skeleton, name the files to create, list required includes, list likely Build.cs modules, and call out UHT/reflection pitfalls.

The default rule for this workspace is to avoid adding a C++ namespace unless the project already uses one and the symbol is not a reflected Unreal type. Reflected Unreal types with UCLASS, USTRUCT, UENUM, UINTERFACE, UFUNCTION, or UPROPERTY should not be wrapped in a new namespace by default.

## Universal Unreal Header Rules

1. Put `#pragma once` first.
2. Include the direct base class or direct type dependencies before the generated header.
3. Put `"MyType.generated.h"` as the last include in that header.
4. Put `GENERATED_BODY()` as the first statement inside the reflected type body unless there is a local convention saying otherwise.
5. Use forward declarations for pointer/reference UCLASS types when possible, but include full definitions for USTRUCT value members, UENUM properties, templates, inline methods, and base classes.
6. If a header exposes a type from another module through a public property, function signature, or inherited type, check `PublicDependencyModuleNames`. If the type is only used in `.cpp`, check `PrivateDependencyModuleNames`.

## Recipe: UActorComponent 새 컴포넌트 만들기

Intent: `UActorComponent` new component create UCLASS GENERATED_BODY.

Files:

- `Source/<Module>/Public/<Feature>/<MyComponent>.h`
- `Source/<Module>/Private/<Feature>/<MyComponent>.cpp`

Header essentials:

```cpp
#pragma once

#include "CoreMinimal.h"
#include "Components/ActorComponent.h"
#include "MyComponent.generated.h"

UCLASS(ClassGroup=(Custom), meta=(BlueprintSpawnableComponent))
class <MODULE_API> UMyComponent : public UActorComponent
{
    GENERATED_BODY()

public:
    UMyComponent();

protected:
    virtual void BeginPlay() override;

public:
    virtual void TickComponent(float DeltaTime, ELevelTick TickType, FActorComponentTickFunction* ThisTickFunction) override;
};
```

CPP essentials:

```cpp
#include "Feature/MyComponent.h"

UMyComponent::UMyComponent()
{
    PrimaryComponentTick.bCanEverTick = true;
}

void UMyComponent::BeginPlay()
{
    Super::BeginPlay();
}

void UMyComponent::TickComponent(float DeltaTime, ELevelTick TickType, FActorComponentTickFunction* ThisTickFunction)
{
    Super::TickComponent(DeltaTime, TickType, ThisTickFunction);
}
```

Build.cs modules:

- Usually `Core`, `CoreUObject`, `Engine`.
- Add extra modules only when the component exposes or uses their types.

Common failures:

- `generated.h` error: generated header is not the last include.
- UHT cannot find base/type: missing include or missing module dependency.
- `LNK2019`: method declared but not defined, or definition signature does not match declaration.

## Recipe: AActor 새 Actor 만들기

Intent: `AActor` create actor UCLASS constructor BeginPlay Tick.

Header essentials:

```cpp
#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Actor.h"
#include "MyActor.generated.h"

UCLASS()
class <MODULE_API> AMyActor : public AActor
{
    GENERATED_BODY()

public:
    AMyActor();

protected:
    virtual void BeginPlay() override;

public:
    virtual void Tick(float DeltaSeconds) override;
};
```

CPP essentials:

```cpp
#include "Actors/MyActor.h"

AMyActor::AMyActor()
{
    PrimaryActorTick.bCanEverTick = true;
}

void AMyActor::BeginPlay()
{
    Super::BeginPlay();
}

void AMyActor::Tick(float DeltaSeconds)
{
    Super::Tick(DeltaSeconds);
}
```

Advice:

- Use `CreateDefaultSubobject` in the constructor for default components.
- Do not spawn actors in constructors. Use `BeginPlay`, gameplay systems, or factory methods.
- Use `UPROPERTY` for UObject references that must survive garbage collection.

## Recipe: UObject Service Or Helper

Intent: `UObject` helper object create UCLASS NewObject garbage collection.

Header essentials:

```cpp
#pragma once

#include "CoreMinimal.h"
#include "UObject/Object.h"
#include "MyService.generated.h"

UCLASS()
class <MODULE_API> UMyService : public UObject
{
    GENERATED_BODY()

public:
    void Initialize();
};
```

Advice:

- Create with `NewObject<UMyService>(Outer)`.
- Store the object in a `UPROPERTY` owner field if it must not be garbage collected.
- Prefer a subsystem if lifetime should match GameInstance, World, Engine, or Editor.

## Recipe: UDataAsset

Intent: `UDataAsset` create data asset UCLASS asset type.

Header essentials:

```cpp
#pragma once

#include "CoreMinimal.h"
#include "Engine/DataAsset.h"
#include "MyDataAsset.generated.h"

UCLASS(BlueprintType)
class <MODULE_API> UMyDataAsset : public UDataAsset
{
    GENERATED_BODY()

public:
    UPROPERTY(EditDefaultsOnly, BlueprintReadOnly)
    FName Id;
};
```

Build.cs modules:

- Usually `Engine` is enough beyond `Core` and `CoreUObject`.

Advice:

- Use `EditDefaultsOnly` for authored config data.
- Avoid mutable runtime state inside shared DataAsset instances.

## Recipe: GameInstance Or World Subsystem

Intent: `UGameInstanceSubsystem` `UWorldSubsystem` initialize deinitialize subsystem.

Header essentials:

```cpp
#pragma once

#include "CoreMinimal.h"
#include "Subsystems/GameInstanceSubsystem.h"
#include "MyGameSubsystem.generated.h"

UCLASS()
class <MODULE_API> UMyGameSubsystem : public UGameInstanceSubsystem
{
    GENERATED_BODY()

public:
    virtual void Initialize(FSubsystemCollectionBase& Collection) override;
    virtual void Deinitialize() override;
};
```

Advice:

- Use `UGameInstanceSubsystem` for game-session wide services.
- Use `UWorldSubsystem` for world-specific state.
- Use `UEngineSubsystem` or `UEditorSubsystem` only when that lifetime is intentional.
- Subsystems are usually created by the engine, not by `NewObject` in gameplay code.

## Recipe: UInterface

Intent: Unreal interface create UINTERFACE IInterface BlueprintNativeEvent.

Header essentials:

```cpp
#pragma once

#include "CoreMinimal.h"
#include "UObject/Interface.h"
#include "MyInteractable.generated.h"

UINTERFACE(BlueprintType)
class <MODULE_API> UMyInteractable : public UInterface
{
    GENERATED_BODY()
};

class <MODULE_API> IMyInteractable
{
    GENERATED_BODY()

public:
    UFUNCTION(BlueprintNativeEvent, BlueprintCallable)
    void Interact(AActor* InstigatorActor);
};
```

Advice:

- Call BlueprintNativeEvent methods through `IMyInteractable::Execute_Interact(Object, InstigatorActor)`.
- Do not assume the C++ interface pointer exists for Blueprint-only implementers.

