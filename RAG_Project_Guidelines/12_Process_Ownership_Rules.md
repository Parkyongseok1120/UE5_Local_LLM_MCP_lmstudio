# Process Ownership Rules

## 검색 키워드

process ownership, state SSOT, process SSOT, performer-owned process, target-owned process, request apply resolve, process event vs state event, cancel contract, duplicate request guard, failure path, concrete type dependency, request object, timer over tick, TOCTOU

## 목적

이 문서는 특정 게임이나 클래스 이름에 묶이지 않고, Unreal C++ 프로토타입 설계에서 상태 SSOT와 프로세스 SSOT를 분리하기 위한 범용 규칙이다.

상태 SSOT는 최종 상태 값을 소유하고 검증하는 객체다. 프로세스 SSOT는 진행률, 타이머, 현재 대상, 취소 가능성, 시도 결과를 소유하는 객체다. 둘은 같을 수도 있지만, 기본적으로 분리해서 판단한다.

예시:

- 해킹: 수행자는 해킹 진행률을 소유하고, 대상은 방어/취약 상태를 소유할 수 있다.
- 점령: 점령 장치나 상호작용 컴포넌트가 진행률을 소유하고, 거점 Actor가 점령 상태를 소유할 수 있다.
- 부활: 부활을 시도하는 객체가 채널링 진행률을 소유하고, 쓰러진 캐릭터가 생존 상태를 소유할 수 있다.
- 스캔: 스캐너가 스캔 진행률을 소유하고, 대상이 노출/식별 상태를 소유할 수 있다.

## Default: Performer-Owned Process

사용자가 어떤 객체가 행동을 "실행한다", "수행한다", "채널링한다", "유지한다", "진행한다"고 말하면 기본값은 performer-owned process다.

| 항목 | 기본 담당 |
|---|---|
| 외부 명령 수신 | 행동 수행자 또는 수행자의 전용 Component |
| Progress / Timer | 행동 수행자 또는 수행자의 전용 Component |
| CurrentTarget | 행동 수행자 또는 수행자의 전용 Component |
| Cancel / range break / target lost | 행동 수행자 또는 수행자의 전용 Component |
| 미니게임/거리/타이머 완료 판정 | 행동 수행자 또는 수행자의 전용 Component |
| CanReceive / resistance / immunity | 대상 또는 대상 소유 Component |
| 최종 상태 변경 | 대상 또는 상태 소유 Component |
| OnProcessStarted / OnProcessCompleted / OnProcessCancelled | 프로세스 소유자 |
| OnStateChanged / OnBroken / OnActivated / OnRecovered | 상태 소유자 |

수행자가 `Progress`와 `Timer`를 소유하는 것은 SSOT 위반이 아니다. SSOT 위반은 수행자가 대상의 `Health`, `Shield`, `Vulnerability`, `Captured`, `Revived`, `Unlocked` 같은 내부 상태를 직접 수정하는 경우다.

## Target-Owned Process Is a Different Architecture

대상이 Progress를 소유하는 구조도 가능하다. 단, 이는 target-owned process이며 별도 근거가 필요하다.

Target-owned process가 자연스러운 경우:

- 행동이 수행자의 능동 채널링이 아니라 대상에게 걸리는 디버프/상태이상이다.
- 여러 수행자가 같은 대상에 누적 진행도를 쌓는다.
- 대상의 방어/잠금/저항 시스템이 타이머, 저항, 실패 판정을 전부 처리한다.
- 수행자는 요청만 보내고 대상 내부 시스템이 프로세스를 실행한다.
- 진행도가 대상에 저장/복제/표시되어야 한다.

이 근거가 없다면 "대상이 최종 상태를 소유한다"는 이유만으로 Progress/Timer까지 대상에게 넘기지 않는다.

## Process Event vs State Event

프로세스 이벤트와 상태 이벤트를 반드시 구분한다.

프로세스 이벤트:

- 프로세스 소유자가 발생시킨다.
- 예: `OnProcessStarted`, `OnProgressChanged`, `OnProcessCompleted`, `OnProcessCancelled`
- 수행자가 Progress/Timer를 소유한다면 수행자 또는 수행자의 전용 Component가 Broadcast한다.

상태 이벤트:

- 상태 소유자가 발생시킨다.
- 예: `OnHealthChanged`, `OnShieldBroken`, `OnVulnerabilityStarted`, `OnCaptured`, `OnUnlocked`, `OnRevived`
- 대상이 해당 상태를 소유한다면 대상 또는 대상 소유 Component가 Broadcast한다.

`OnCompleted` 같은 이름은 모호하다. 프로세스 완료인지 상태 변경 결과인지 구분해서 `OnProcessCompleted`, `OnResultApplied`, `OnStateChanged`처럼 이름을 분리한다.

## Recommended Flow

```text
Pseudocode only:
Controller/Input sends command to Performer.RequestAction(Target)
Performer validates distance, ownership, cooldown, and target lifetime
Performer asks Target.CanReceiveAction(ActionContext)
Performer starts Progress/Timer
Performer cancels if range breaks, target changes, performer dies, or player cancels
Performer completes the process and builds ActionRequest
Target.ApplyActionAttempt(ActionRequest) or Target.ResolveActionAttempt(ActionRequest)
Target revalidates resistance, immunity, and current state
Target mutates target-owned state if accepted
Target broadcasts state events
Performer broadcasts process completion/cancel result
```

`CanReceiveAction`은 사전 확인이다. `ApplyActionAttempt` 또는 `ResolveActionAttempt`는 완료 시점의 최종 재검증이다. 두 단계가 나뉘면 TOCTOU 문제가 생길 수 있으므로 Target은 완료 시점에도 반드시 다시 판단한다.

## Interface Boundary

대상 인터페이스는 대상이 어떤 시도를 받을 수 있는지와 그 시도를 최종 적용할 수 있는지만 표현한다.

권장:

- `CanReceiveAction(ActionContext)`
- `ApplyActionAttempt(ActionRequest) -> ActionResult`
- `ResolveActionAttempt(ActionRequest) -> ActionResult`
- `GetActionRequirement` 또는 `GetReceivableState`

주의:

- `ApplyActionSuccess(Instigator)`는 성공을 전제로 하므로 Target 재검증을 빠뜨리기 쉽다. 사용할 경우 Target 내부에서 다시 상태를 확인한다고 명시한다.

금지 또는 기본 제외:

- `RequestAction`
- `CancelActionRequest`
- `GetActionProgress`
- 대상 내부 상태의 상세 getter 모음
- `SetTargetState`
- `Break/Unlock/Open/Revive` 같은 내부 mutation 이름
- `OnActionCompleted`

`RequestAction`은 프로세스 수행 주체의 API다. `CancelActionRequest`와 `GetActionProgress`도 performer-owned process에서는 수행자 또는 수행자의 전용 Component에 둔다.

## Avoid Concrete Class Dependency In Interfaces

Target-facing interface가 프로젝트별 수행자 Actor/Component 타입 같은 구체 클래스를 직접 받으면 대상이 수행자의 구현 클래스를 알아야 한다. 이는 인터페이스 경계를 흐린다.

권장:

- `AActor* Initiator`
- `UObject* InstigatorObject`
- 역할 중심의 별도 최소 인터페이스
- `FActionRequest` 같은 request struct에 source actor, power, tags, request id를 담기

설계 리뷰에서는 request object를 우선 제안한다. 컴파일 가능한 구조체 코드는 구현 요청이 있을 때만 작성한다.

## Cancel, Duplicate, Failure Contract

진행형 프로세스 API에는 성공 경로만 있으면 안 된다.

반드시 정의할 계약:

- 중복 요청: 이미 진행 중이면 거절, 갱신, 재시작, 큐잉 중 하나를 선택한다.
- 취소 조건: 범위 이탈, Target 변경, 수행자 사망/비활성, player cancel, Target destroyed.
- 실패 결과: 거리 실패, target invalid, resistance fail, immunity, interrupted, already applied 같은 이유를 구분한다.
- 완료 후 이벤트: 프로세스 완료 이벤트와 Target 상태 이벤트를 따로 발행한다.
- Target lifetime: CurrentTarget은 파괴/무효화를 고려한다.

Performer-owned process라면 cancel/duplicate/failure 상태도 수행자 또는 수행자의 전용 Component가 우선 소유한다. Target reservation이 필요할 때만 Target에 `TryReserveAction` 같은 별도 계약을 둔다.

## Timer Over Tick

진행형 프로세스는 기본적으로 Timer, latent task, ability task, 또는 명시적 active process update를 우선한다. 모든 대상 Actor가 Tick으로 `ProgressTime += DeltaTime`을 수행하는 구조를 기본값으로 제안하지 않는다.

Tick이 필요한 경우:

- 진행 중인 객체만 Tick을 켠다.
- 시작/종료/취소 시 Tick enable 상태를 명확히 정리한다.
- Tick이 필요한 이유를 설명한다.

설계 리뷰에서는 `FTimerHandle` 등 구체 API를 컴파일 코드처럼 쓰지 말고, timer-based active process라고 표현한다.
