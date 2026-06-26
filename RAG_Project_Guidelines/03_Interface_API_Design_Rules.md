# Interface and API Design Rules

## Critical Rule: Do Not Mix Interface and Event

인터페이스에는 Event/Delegate 역할의 함수를 넣지 않는다. `OnXChanged`, `OnXStarted`, `OnXCompleted`, `OnDamageApplied`, `OnInteractionFinished` 같은 이름은 인터페이스 함수가 아니라 상태 소유 객체의 Delegate/Event로 분리한다.

검색 키워드: interface event separation, UINTERFACE no event, OnXChanged delegate, BlueprintAssignable, state owner event, delegate owner

금지:

- UINTERFACE에 `OnHealthChanged`, `OnHackCompleted`, `OnAmmoChanged` 같은 알림 함수를 선언하지 않는다.
- 인터페이스에 delegate subscription API를 넣어서 이벤트 허브처럼 만들지 않는다.
- 인터페이스를 "콜백 받을 객체"와 "상태 소유 객체" 양쪽 역할로 섞지 않는다.

권장:

- 인터페이스는 `CanInteract`, `RequestInteract`, `ApplyInteractionResult`, `GetInteractableState`처럼 계약과 호출 가능 동작만 표현한다.
- 인터페이스는 외부에서 대상에게 질문하거나 요청하는 최소 계약이다. `CanX`, `IsX`, `GetX`, `FindX`, `TryX`, `RequestX`, `ApplyX` 중심으로 유지한다.
- 인터페이스는 사용 사례 중심이어야 한다. 대상 내부에 Shield, Armor, WeakPoint가 있더라도 그 값을 모두 getter로 노출하지 않는다.
- 상태 변경 알림은 상태 소유 객체가 `DECLARE_DYNAMIC_MULTICAST_DELEGATE`, native delegate, Gameplay Message, OnRep 등으로 발행한다.
- UI, FX, Audio, 다른 Component는 상태 소유 객체의 delegate/event를 구독한다.
- 인터페이스 구현 객체가 상태 소유자가 아니라면, 상태 소유 객체를 찾는 Query만 제공하고 이벤트는 그 소유 객체에서 구독한다.

예시 구분:

```text
Pseudocode only:
IInteractable: CanInteract(), RequestInteract()
UInteractionComponent: OnInteractionStateChanged delegate
Widget/FX: bind to UInteractionComponent delegate
```

컴파일 가능한 코드라고 제시하려면 UINTERFACE 선언, `UInterface` 클래스, `IInterface` 클래스, `GENERATED_BODY()`, `BlueprintNativeEvent`/`BlueprintCallable` 여부, `Execute_FunctionName` 호출 규칙을 실제 Unreal 시그니처 기준으로 확인한다.

## Receivable Action Interface Scope

`IHackable`, `IInteractable`, `ICapturable` 같은 대상 인터페이스는 "이 시도를 받을 수 있는가"와 "이 시도를 대상에게 적용할 수 있는가"만 표현한다. 대상이 Shield, Lock, CaptureState, Health 같은 내부 상태를 소유한다고 해서 그 내부 값을 모두 노출하는 인터페이스로 확장하지 않는다.

인터페이스 후보를 제안하기 전에는 `Interface Abstractness Audit`을 통과해야 한다. 모든 구현체에 자연스러운지, 특정 구현체 전용 개념인지, 더 작은 인터페이스로 분리할 수 있는지 먼저 검사한다.

적합한 후보:

- `CanReceiveAction`
- `GetReceivableState`
- `GetActionRequirement`
- `ApplyActionAttempt`
- `ResolveActionAttempt`

부적합한 후보:

- 대상 내부 상태의 상세 getter 모음
- `SetTargetState`
- `SetIsVulnerable`
- `RequestInternalRestore`
- `OnActionStarted`
- `OnActionCompleted`
- `OnTargetStateChanged`

상태 수치가 UI에 필요하면 대상 인터페이스가 아니라 상태 소유 객체의 조회 API, UI 전용 read model, status component, 또는 event 구독 흐름으로 분리한다. Shield 복구, 약점 노출, 잠금 해제, 점령 상태 전환 같은 내부 상태 변경 정책은 외부 수신 계약에 기본 포함하지 않는다.

수행자가 진행 프로세스를 소유하는 설계라면 `RequestX`는 대상 인터페이스보다 수행자의 public API로 두는 편이 명확하다.

```text
Pseudocode only:
Controller/Input -> Performer.RequestAction(Target)
Performer -> Target.CanReceiveAction(ActionContext)
Performer owns Progress and Timer
Performer -> Target.ApplyActionAttempt(ActionRequest)
Target resolves resistance, immunity, target-owned state, and events
```

`ApplyActionSuccess`처럼 성공을 전제로 하는 함수는 주의한다. Target이 최종 상태의 SSOT라면 Target은 완료 시점에도 다시 유효성을 검사해야 한다. 가능하면 `ApplyActionAttempt` 또는 `ResolveActionAttempt`처럼 요청과 결과를 함께 표현하는 이름을 우선한다.

`IsVulnerable`, `IsCaptured`, `IsUnlocked` 같은 상태 query는 수신 인터페이스의 필수 API가 아닐 수 있다. 후속 행동, 데미지, UI 상태 표시와 연결되는 Target 상태라면 별도 status query 또는 Target 내부 규칙으로 분리할 수 있다.

Target-facing interface는 구체 구현 클래스에 의존하지 않는다. 프로젝트별 수행자 Actor/Component 타입을 직접 받으면 Target이 수행자의 구현 클래스를 알아야 한다. 설계 리뷰에서는 `AActor* Initiator`, `UObject* InstigatorObject`, 별도 역할 인터페이스, 또는 `FActionRequest` 같은 request object를 우선 제안한다.

`CancelXRequest`와 `GetXProgress`는 Target-facing interface의 기본 함수가 아니다. 수행자가 Progress/Timer를 소유한다면 cancel과 progress query도 수행자 또는 수행자의 전용 Component에 둔다.

선택 Query를 interface에 넣을 때는 기본 구현의 의미를 명시한다. 예를 들어 optional query의 기본값이 false라면 구현 누락이 침묵 실패가 될 수 있다. 필수 구현인지, 별도 interface인지, 미지원 시 의미가 무엇인지 구분한다.

설계 리뷰 답변에서 인터페이스를 제안할 때는 최소 인터페이스, 제외한 함수, 제외 이유, 필요 시 분리할 보조 인터페이스를 함께 제시한다.

## 목적

Unreal C++에서 인터페이스와 API는 호출 방향을 고정하고 결합도를 줄이는 도구다. 로컬 AI는 인터페이스를 남발하지 말고, 필요한 경계에만 작고 명확하게 사용해야 한다.

검색 키워드: UINTERFACE, interface API, BlueprintCallable, BlueprintPure, API design, ActorComponent, subsystem, delegate, gameplay tag, Build.cs, Unreal C++

## 선택 기준

| 필요한 것 | 우선 선택 |
| --- | --- |
| 여러 클래스가 같은 기능 계약을 제공해야 한다 | UINTERFACE |
| 공유 구현과 상태가 필요하다 | Base class 또는 ActorComponent |
| 특정 Actor에 붙였다 뗄 수 있는 기능이다 | ActorComponent |
| 월드/게임 전체 서비스다 | Subsystem |
| 단순 stateless helper다 | BlueprintFunctionLibrary 또는 일반 free function |
| 한 객체가 다른 객체의 사건을 관찰한다 | Delegate/Event |
| 데이터 기반 분기와 식별이 필요하다 | GameplayTag/DataAsset |

## 인터페이스 규칙

1. 인터페이스는 작아야 한다.
   - 한 인터페이스는 한 책임만 표현한다.
   - getter, command, event subscription을 한 인터페이스에 모두 넣지 않는다.
   - 구현 세부 상태를 노출하지 않는다.

2. Unreal 인터페이스 호출 방식을 지킨다.
   - UObject 대상 인터페이스는 `Implements<>()` 또는 `GetClass()->ImplementsInterface()`로 확인한다.
   - Blueprint 구현 가능성이 있으면 `IInterfaceName::Execute_FunctionName(Object, Args)` 호출을 우선한다.
   - C++ 전용 계약이면 가상 함수와 컴포넌트 조합이 더 단순한지 먼저 판단한다.

3. API 이름은 의도를 드러내야 한다.
   - `Get`/`Find`/`Has`/`Can`은 Query이며 상태를 바꾸지 않는다.
   - `Set`/`Try`/`Request`/`Apply`는 Command이며 상태 변경 가능성이 있다.
   - `On`/`Handle`/`Broadcast`는 Event 흐름을 나타낸다.

4. Blueprint 노출은 최소화한다.
   - 디자이너가 호출할 필요가 있는 함수만 `BlueprintCallable`로 둔다.
   - 부작용 없는 조회만 `BlueprintPure`로 둔다.
   - 권한이 필요한 함수는 이름, 주석, 런타임 체크로 서버/클라이언트 조건을 명확히 한다.

5. 반환 타입은 소유권을 드러낸다.
   - 내부 배열/맵의 mutable reference를 반환하지 않는다.
   - UObject 포인터는 nullable 가능성을 고려해 이름과 체크를 둔다.
   - 비동기 결과는 delegate, latent action, async action, gameplay task 등 생명주기가 보이는 형태로 설계한다.

## API 안정성 체크

- 함수가 어느 스레드/월드/권한에서 호출되는지 명확한가?
- 실패 가능한 함수가 실패 이유를 반환하거나 로그를 남기는가?
- 호출자가 UObject lifetime을 안전하게 다루는가?
- `const` 함수가 실제로 상태를 바꾸지 않는가?
- 헤더에 불필요한 include 대신 forward declaration을 사용할 수 있는가?
- Build.cs에 필요한 모듈 의존성이 들어갔는가?

## 네임스페이스 규칙

프로젝트 게임플레이 코드는 웬만하면 namespace를 만들지 않는다. Unreal reflection, UCLASS/USTRUCT/UENUM, generated code, Blueprint 노출 타입은 전역 Unreal 타입 시스템과 함께 동작하므로 불필요한 namespace는 AI가 생성한 코드의 컴파일 실패와 사용성 저하를 만들 수 있다. 순수 C++ 내부 helper나 충돌 회피가 정말 필요한 경우에만 제한적으로 사용한다.
