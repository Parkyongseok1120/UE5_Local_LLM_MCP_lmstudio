# Unreal Codegen Recipes: Gameplay Systems

## Keywords

Unreal C++ gameplay codegen, delegate, dynamic multicast delegate, Enhanced Input, replication, replicated property, Server RPC, Client RPC, NetMulticast, GameplayTags, SaveGame, TimerManager, async task, module dependency, Build.cs, API lookup

Korean query aliases: 델리게이트, Enhanced Input 연결, 입력 액션 바인딩, 리플리케이션, 서버 RPC, 클라이언트 RPC, GameplayTag 사용, SaveGame 저장, 타이머 사용, API 사용법, Build.cs 모듈 추가

## Purpose

Use this document for common gameplay system snippets. The answer should include the minimum code shape, the include/module requirements, and the most likely compile or runtime failure.

## Recipe: Delegate 선언과 바인딩

Intent: delegate declare bind broadcast dynamic multicast.

Header essentials:

```cpp
DECLARE_DYNAMIC_MULTICAST_DELEGATE_OneParam(FOnValueChanged, int32, NewValue);

UPROPERTY(BlueprintAssignable)
FOnValueChanged OnValueChanged;
```

Usage:

```cpp
OnValueChanged.Broadcast(Value);
```

Advice:

- Use dynamic delegates when Blueprint exposure or serialization is needed.
- Use native delegates for lower overhead C++ only paths.
- UHT errors often mean the delegate declaration is in the wrong place, uses an unsupported reflected parameter type, or lacks a required include for the parameter type.

## Recipe: Enhanced Input 연결

Intent: Enhanced Input setup input action bind action UInputAction UEnhancedInputComponent.

Build.cs modules:

- Add `EnhancedInput` to the module dependencies.

Common includes:

```cpp
#include "EnhancedInputComponent.h"
#include "EnhancedInputSubsystems.h"
#include "InputAction.h"
#include "InputMappingContext.h"
```

Typical binding in a pawn or character:

```cpp
void AMyCharacter::SetupPlayerInputComponent(UInputComponent* PlayerInputComponent)
{
    Super::SetupPlayerInputComponent(PlayerInputComponent);

    if (UEnhancedInputComponent* EnhancedInput = Cast<UEnhancedInputComponent>(PlayerInputComponent))
    {
        EnhancedInput->BindAction(MoveAction, ETriggerEvent::Triggered, this, &AMyCharacter::HandleMove);
    }
}
```

Advice:

- Mapping contexts are usually added through `UEnhancedInputLocalPlayerSubsystem`.
- Missing `EnhancedInput` dependency often appears as include failure, unresolved symbol, or unknown type.

## Recipe: Replication 변수와 RPC

Intent: replicated property GetLifetimeReplicatedProps Server RPC Client RPC NetMulticast.

Build.cs modules:

- Usually `Engine` and `NetCore` depending on APIs used.

Header essentials:

```cpp
UPROPERTY(ReplicatedUsing=OnRep_Health)
float Health = 100.0f;

UFUNCTION()
void OnRep_Health();

UFUNCTION(Server, Reliable)
void ServerUseAbility();
```

CPP essentials:

```cpp
#include "Net/UnrealNetwork.h"

void AMyActor::GetLifetimeReplicatedProps(TArray<FLifetimeProperty>& OutLifetimeProps) const
{
    Super::GetLifetimeReplicatedProps(OutLifetimeProps);
    DOREPLIFETIME(AMyActor, Health);
}
```

Advice:

- Set `bReplicates = true` for actors that must replicate.
- Server RPCs must be called on an actor/component owned by the calling client.
- `OnRep` runs on clients when the replicated value changes, not as a general setter replacement.

## Recipe: Gameplay Tags

Intent: GameplayTag FGameplayTag UPROPERTY Build.cs GameplayTags.

Build.cs modules:

- Add `GameplayTags`.

Header essentials:

```cpp
#include "GameplayTagContainer.h"

UPROPERTY(EditDefaultsOnly, BlueprintReadOnly)
FGameplayTag AbilityTag;
```

Advice:

- Missing module dependency for GameplayTags often appears as `C1083`, UHT unknown type, or unresolved external symbol.
- Prefer centralized tag definitions or config-driven tag lists once the project starts.

## Recipe: TimerManager

Intent: timer set timer clear timer FTimerHandle GetWorldTimerManager.

Header essentials:

```cpp
FTimerHandle CooldownTimerHandle;

void FinishCooldown();
```

CPP essentials:

```cpp
GetWorldTimerManager().SetTimer(CooldownTimerHandle, this, &AMyActor::FinishCooldown, CooldownSeconds, false);
GetWorldTimerManager().ClearTimer(CooldownTimerHandle);
```

Advice:

- Timer callbacks must match the expected signature.
- Use weak object checks when timers interact with objects that may be destroyed.

## Recipe: SaveGame

Intent: USaveGame save slot load slot gameplay save data.

Header essentials:

```cpp
#include "GameFramework/SaveGame.h"
#include "MySaveGame.generated.h"

UCLASS()
class <MODULE_API> UMySaveGame : public USaveGame
{
    GENERATED_BODY()

public:
    UPROPERTY()
    int32 Progress = 0;
};
```

Usage APIs:

- `UGameplayStatics::CreateSaveGameObject`
- `UGameplayStatics::SaveGameToSlot`
- `UGameplayStatics::LoadGameFromSlot`

Advice:

- Store simple serializable state, not raw runtime pointers.
- If a save class is referenced in a public header, ensure the module dependency is visible to that header.

## Recipe: Runtime Module Or Plugin Module

Intent: new module plugin Build.cs module dependency startup module shutdown module.

Files:

- `<Module>.Build.cs`
- `Public/`
- `Private/`
- Optional module class implementing `IModuleInterface`

Build.cs guidance:

- Public dependency: types appear in public headers or exported API.
- Private dependency: types used only inside private `.cpp` implementation.
- Plugin modules should keep Runtime and Editor code in separate modules.

Advice:

- If Editor-only classes appear in a Runtime module, packaging or non-editor builds will fail.
- If a public header includes a type from a Private dependency, downstream modules may compile fail.

