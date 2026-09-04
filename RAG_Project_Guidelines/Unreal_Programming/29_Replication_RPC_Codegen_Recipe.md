# 네트워크 값과 RPC 연결

복제할 값은 헤더 표시와 `GetLifetimeReplicatedProps`의 `DOREPLIFETIME` 등록을 함께 확인해야 합니다. 액터의 `bReplicates`와 실제 소유권도 필요합니다.

RPC는 `UFUNCTION(Server, Reliable)`, `Client`, `NetMulticast` 등 의도한 선언과 `_Implementation`을 맞춥니다. 검증 함수를 쓰는 경우 `_Validate`도 확인합니다. 클라이언트 요청과 서버의 최종 상태 판정을 구분해야 합니다.

관련 경고는 `REPLICATED_UPROPERTY_WITHOUT_DOREPLIFETIME`, `RPC_IMPLEMENTATION_MISSING`, `REPLICATION_SETUP_INCOMPLETE`입니다. 선언만으로 실제 네트워크 전달이 확인된 것은 아닙니다.

## 코드 형태 예시

아래 예시는 실제 프로젝트에서 빌드를 끝낸 결과가 아닙니다. 이름·모듈·엔진 선언을 확인해서 적용해야 합니다.

```cpp
UPROPERTY(Replicated)
int32 Health;

virtual void GetLifetimeReplicatedProps(TArray<FLifetimeProperty>& OutLifetimeProps) const override;
```

```cpp
void AMyActor::GetLifetimeReplicatedProps(TArray<FLifetimeProperty>& OutLifetimeProps) const
{
    Super::GetLifetimeReplicatedProps(OutLifetimeProps);
    DOREPLIFETIME(AMyActor, Health);
}
```
