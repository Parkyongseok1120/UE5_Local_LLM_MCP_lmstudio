# 델리게이트 연결과 해제

변경 알림은 선언·연결·발행·해제를 한 흐름으로 확인해야 합니다. `AddDynamic`, `AddUObject`, `BindUObject`로 연결했다면 종료나 비활성화 시점의 `RemoveDynamic`, `RemoveAll` 등 대응 정리를 확인합니다.

알림 인자와 콜백 선언을 정확히 맞춥니다. 같은 몽타주에 다시 연결하기 전에 이전 종료 콜백을 정리해 중복 호출을 막아야 합니다. `bInterrupted`를 무시하지 않습니다.

관련 보조 경고는 `DELEGATE_BIND_WITHOUT_UNBIND`, `DELEGATE_BROADCAST_SIGNATURE_MISMATCH`입니다.

## 코드 형태 예시

아래 예시는 실제 프로젝트에서 빌드를 끝낸 결과가 아닙니다. 이름·모듈·엔진 선언을 확인해서 적용해야 합니다.

```cpp
void UMyComponent::EndPlay(const EEndPlayReason::Type EndPlayReason)
{
    if (UAnimInstance* Anim = GetAnimInstance())
    {
        Anim->Montage_SetEndDelegate(FOnMontageEnded(), ActiveMontage.Get());
    }
    Super::EndPlay(EndPlayReason);
}
```
