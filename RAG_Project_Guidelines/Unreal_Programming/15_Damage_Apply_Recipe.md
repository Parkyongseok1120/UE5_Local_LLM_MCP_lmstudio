# 데미지 적용 코드 구현

설계 검토에서는 책임과 흐름부터 설명하고, 구현 요청에서만 실제 엔진 함수를 사용합니다. 현재 엔진의 `UGameplayStatics::ApplyDamage`와 `AActor::TakeDamage` 선언을 확인해야 합니다.

공격자는 공격 시도를 전달하고 대상이 최종 상태를 판정합니다. 무기 코드에서 대상의 체력·방어막 내부 값을 직접 바꾸지 않습니다. 멀티플레이에서는 `DamageCauser`와 `Instigator`가 누구인지, 어느 쪽이 최종 판정하는지 분명히 합니다.

프로토타입도 데미지 전달 경로를 하나로 모으되 현재 프로젝트의 기존 경로를 먼저 재사용해야 합니다. 아래 예시는 현재 프로젝트에 맞춰 확인한 뒤 사용합니다.

## 코드 형태 예시

아래 예시는 실제 프로젝트에서 빌드를 끝낸 결과가 아닙니다. 이름·모듈·엔진 선언을 확인해서 적용해야 합니다.

```cpp
#include "Kismet/GameplayStatics.h"
#include "GameFramework/Actor.h"

void UCombatComponent::ApplyDamageToActor(AActor* Target, float Amount, AActor* InstigatorActor)
{
    if (!Target || Amount <= 0.f) return;
    UGameplayStatics::ApplyDamage(Target, Amount, InstigatorActor ? InstigatorActor->GetInstigatorController() : nullptr, InstigatorActor);
}
```

```cpp
float AMyCharacter::TakeDamage(float DamageAmount, FDamageEvent const& DamageEvent,
    AController* EventInstigator, AActor* DamageCauser)
{
    const float Applied = Super::TakeDamage(DamageAmount, DamageEvent, EventInstigator, DamageCauser);
    // 이 프로젝트의 피해 감소 규칙을 여기에 적용합니다
    return Applied;
}
```
