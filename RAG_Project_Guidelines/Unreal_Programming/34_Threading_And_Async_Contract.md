# 스레드와 비동기 작업의 계약

비동기 코드를 쓰거나 검토할 때 세 가지를 따로 확인합니다.

| 질문 | 확인할 것 |
|---|---|
| 어디서 실행되는가 | 게임 스레드, 워커, 렌더 스레드 |
| 대상이 아직 살아 있는가 | GC 생존, `TWeakObjectPtr` 유효성 |
| 대상이 아직 현재 대상인가 | EndPlay·맵 전환·취소 이후인지, 같은 World인지 |

객체가 살아 있어도 이미 EndPlay가 끝났을 수 있고, 살아 있는 객체라도 워커에서 접근하면 안 됩니다.

## 실행 문맥

| 호출 | 본문이 실행되는 곳 |
|---|---|
| `AsyncTask(ENamedThreads::GameThread, ...)` | 게임 스레드(다음 처리 시점) |
| `AsyncTask(ENamedThreads::AnyThread, ...)`, `Async(EAsyncExecution::ThreadPool, ...)`, `UE::Tasks::Launch` | 워커 |
| `Async(EAsyncExecution::TaskGraphMainThread, ...)` | 게임 스레드 |
| `ParallelFor` | 워커(호출 스레드는 완료까지 대기) |
| `ENQUEUE_RENDER_COMMAND` | 렌더 스레드(나중에 실행) |
| `FRunnable::Run` | 별도 스레드 |

## 워커에서 하지 않는 것

UObject·Actor·Component·World를 생성·파괴·스폰하거나 상태를 바꾸지 않습니다. `FTimerManager`는 `SetTimer`와 조회에서 `IsInGameThread`를 검사하므로 워커에서 타이머를 설정·해제하지 않습니다.

## 권장 형태

```cpp
void UMyComponent::StartBuild()
{
    TArray<FVector> Input = CollectPoints();              // 게임 스레드에서 복사
    TWeakObjectPtr<UMyComponent> WeakThis(this);
    UE::Tasks::Launch(UE_SOURCE_LOCATION, [WeakThis, Input = MoveTemp(Input)]()
    {
        FBuildResult Result = BuildOffThread(Input);       // 일반 데이터만 사용
        AsyncTask(ENamedThreads::GameThread, [WeakThis, Result = MoveTemp(Result)]()
        {
            if (UMyComponent* Self = WeakThis.Get())       // 생존 확인
            {
                Self->ApplyBuild(Result);                  // 현재 대상인지 ApplyBuild에서 확인
            }
        });
    });
}
```

- 워커 람다에는 `this`나 UObject 포인터 대신 복사한 값과 `TWeakObjectPtr`을 캡처합니다.
- 결과는 게임 스레드로 돌아온 뒤 적용합니다. 적용 전에 취소 여부와 대상 World를 확인합니다.
- 렌더 명령에는 값 복사나 렌더 프록시를 넘기고, 게임플레이 UObject를 캡처해 렌더 스레드에서 읽지 않습니다.

## 서브시스템과의 관계

- `Initialize`에서 같은 컬렉션의 다른 서브시스템을 바로 사용하면 `Collection.InitializeDependency<T>()`로 순서를 보장합니다. 다른 컬렉션(예: World 서브시스템에서 GameInstance 서브시스템)은 대상이 아닙니다.
- `Initialize`·`OnWorldBeginPlay`에서 등록한 delegate·ticker·타이머는 `Deinitialize`·`OnWorldEndPlay`에서 해제합니다.
- 비동기 작업을 시작한 서브시스템은 `Deinitialize`에서 작업을 취소하거나, 완료 콜백이 약한 참조로 종료 여부를 확인하게 합니다.

## 관련 경고

`GAME_THREAD_API_OFF_THREAD`, `ASYNC_UOBJECT_LIFETIME_RISK`, `RENDER_THREAD_UOBJECT_ACCESS_REVIEW`, `SUBSYSTEM_INITIALIZATION_DEPENDENCY_REVIEW`, `SUBSYSTEM_CLEANUP_NOT_OBSERVED`는 모두 경고입니다. 문법만 보고 판단하므로 helper 함수 안의 정리나 다른 파일의 보장은 보지 못합니다. 경고가 없다는 것이 안전하다는 증명은 아닙니다.

계약 원천: `config/unreal_contracts.json`의 `ue.timer.game-thread`, `ue.thread.uobject-game-thread`, `policy.render.game-objects`, `ue.subsystem.initialize-dependency`, `ue.subsystem.lifecycle`.
