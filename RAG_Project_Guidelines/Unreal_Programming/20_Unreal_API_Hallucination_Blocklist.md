# 언리얼 함수·속성의 존재 여부 확인과 오용 방지

현재 엔진 헤더·공식 자료·프로젝트 소스·에디터 내보내기 중 하나로 정확한 이름과 사용 형태를 확인해야 합니다. 함수가 있어도 다른 객체나 인자로 호출하면 맞는 코드가 아닙니다.

| 의심할 표현 | 확인할 방향 |
|---|---|
| `DisableGravity()` | 해당 이동 컴포넌트의 `GravityScale`이나 의도한 이동 모드 |
| `UWorld::GetURL()` | 실제 레벨 이름 조회와 로컬·서버 이동 방식 |
| `GEngine->GetWorld()` | 소유 객체의 월드 또는 명시적인 `UWorld*` |
| `SpawnActor(..., &SpawnTransform, ...)` | 실제 선택한 오버로드와 인자형 |
| `ReplicateVariable`, `SetReplicated` | `GetLifetimeReplicatedProps`, `DOREPLIFETIME` |
| 인자 없는 `GiveAbility()` | 실제 AbilitySystemComponent의 멤버 호출 |
| 소유자 없는 `CreateWidget()` | 실제 함수 선언과 UI 소유자 |
| 실행 코드의 `GEditor`, `FEditorDelegates` | 에디터 전용 모듈·조건과 경계 |
| `SetRestoreState`, `SetBindingTag`, `AddBindingOverride` | 실제 시퀀서 설정·바인딩 API |
| 표면 머티리얼의 최종 `SceneColor` 변경 | 머티리얼 종류와 후처리 단계 |
| `WorldPosition.Z`를 카메라 거리로 사용 | 실제 월드 위치와 카메라 위치의 관계 |

`ResolvedView.PreExposure`, 자동 주 광원 방향, GBuffer·스텐실·주변 깊이 입력도 현재 머티리얼에서 가능한지 확인합니다. 추측한 화면 연결이나 일반 파일 도구로 `.uasset` 노드를 수정했다고 말하지 않습니다.

프로젝트 자료형을 사용하기 전에 해당 헤더와 선언을 찾고 필요한 모듈만 추가합니다. 빌드·셰이더 컴파일·에디터 실행은 실제 결과가 있어야 성공이라고 적습니다.

근거가 없으면 확인이 필요한 파일을 적고, 근사라면 원래 동작과의 차이를 설명해야 합니다. 아래 코드 형태도 현재 엔진과 프로젝트에서 다시 확인해야 합니다.

## 코드 형태 예시

아래 예시는 실제 프로젝트에서 빌드를 끝낸 결과가 아닙니다. 이름·모듈·엔진 선언을 확인해서 적용해야 합니다.

```cpp
// 실제 이동 컴포넌트의 값으로 캐릭터 중력을 끕니다.
if (UCharacterMovementComponent* MoveComp = Character->GetCharacterMovement())
{
    MoveComp->GravityScale = 0.0f;
}

// 확인된 월드에서 현재 로컬·PIE 레벨을 다시 시작합니다.
const FString LevelName = UGameplayStatics::GetCurrentLevelName(World, true);
UGameplayStatics::OpenLevel(World, FName(*LevelName));

// 대상 자료형과 Transform을 받는 생성 함수를 사용합니다.
FActorSpawnParameters Params;
AEnemyCharacter* Spawned = World->SpawnActor<AEnemyCharacter>(
    EnemyClass, SpawnTransform, Params);
```
