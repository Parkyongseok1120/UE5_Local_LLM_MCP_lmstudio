# 입력·네트워크·태그·저장 기능 연결

아래 예시는 선언만 붙이는 것으로 끝내지 말고 누가 시작하고 끝내는지까지 확인해야 합니다.

- 변경 알림은 상태 소유 객체가 발행합니다. 블루프린트 노출이 필요하면 동적 델리게이트, C++ 전용이면 해당 용도에 맞는 델리게이트를 선택합니다. 연결한 쪽은 종료 때 해제합니다.
- Enhanced Input은 `EnhancedInput` 모듈과 관련 헤더를 확인합니다. `UEnhancedInputComponent`로 동작을 연결하고 `UEnhancedInputLocalPlayerSubsystem`의 입력 매핑도 확인합니다.
- 네트워크 값은 서버의 원본과 클라이언트 표시를 구분합니다. 복제 대상 등록과 RPC 소유권을 확인하고 `OnRep`에서 보상을 중복 지급하지 않습니다.
- Gameplay Tags는 `GameplayTagContainer.h`와 `GameplayTags` 모듈을 확인합니다. 태그 정의를 여러 곳에서 중복 관리하지 않습니다.
- 타이머는 `FTimerHandle`과 콜백 선언을 맞추고 객체 종료 때 정리합니다. 객체가 먼저 사라질 수 있으면 약한 참조와 유효성 검사를 사용합니다.
- 저장 파일에는 복원 가능한 값과 식별자를 넣습니다. 실행 중 객체 포인터 자체를 저장하지 않습니다.
- 실행용 모듈과 에디터용 모듈은 분리합니다. 다른 모듈 자료형이 공개 헤더에 드러나는지에 따라 의존성 위치를 고릅니다.

행동 요청에서는 현재 상태, 비용, 필요한 에셋과 대상, 실행 가능성을 확인합니다. 실행 성공 여부와 비용 차감·상태 변경·알림 순서를 분명히 하고 실패했는데 비용만 소비되는지 확인해야 합니다.

## 코드 형태 예시

아래 예시는 실제 프로젝트에서 빌드를 끝낸 결과가 아닙니다. 이름·모듈·엔진 선언을 확인해서 적용해야 합니다.

```cpp
DECLARE_DYNAMIC_MULTICAST_DELEGATE_OneParam(FOnValueChanged, int32, NewValue);

UPROPERTY(BlueprintAssignable)
FOnValueChanged OnValueChanged;
```

```cpp
OnValueChanged.Broadcast(Value);
```

```cpp
#include "EnhancedInputComponent.h"
#include "EnhancedInputSubsystems.h"
#include "InputAction.h"
#include "InputMappingContext.h"
```

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

```cpp
UPROPERTY(ReplicatedUsing=OnRep_Health)
float Health = 100.0f;

UFUNCTION()
void OnRep_Health();

UFUNCTION(Server, Reliable)
void ServerUseAbility();
```

```cpp
#include "Net/UnrealNetwork.h"

void AMyActor::GetLifetimeReplicatedProps(TArray<FLifetimeProperty>& OutLifetimeProps) const
{
    Super::GetLifetimeReplicatedProps(OutLifetimeProps);
    DOREPLIFETIME(AMyActor, Health);
}
```

```cpp
#include "GameplayTagContainer.h"

UPROPERTY(EditDefaultsOnly, BlueprintReadOnly)
FGameplayTag AbilityTag;
```

```cpp
FTimerHandle CooldownTimerHandle;

void FinishCooldown();
```

```cpp
GetWorldTimerManager().SetTimer(CooldownTimerHandle, this, &AMyActor::FinishCooldown, CooldownSeconds, false);
GetWorldTimerManager().ClearTimer(CooldownTimerHandle);
```

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
