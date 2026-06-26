# AI Review Failure Patterns

## 검색 키워드

AI review failure, Unreal design review failure, no compile code in design review, no cpp fence, receivable interface scope, ActiveTarget ownership, process event owner, Request Apply Resolve, action outcome authority, negative example consistency, concise final answer, TakeDamage signature trap, concrete type dependency, cancel contract, duplicate request, failure path, Tick progress

## 목적

이 문서는 로컬 AI가 규칙을 알고도 최종 답변에서 다시 무너지는 패턴을 막기 위한 보정 규칙이다. 설계 리뷰 답변은 "그럴듯한 코드 생성"이 아니라 책임, SSOT, API 경계, 이벤트 위치를 검수하는 산출물이어야 한다.

## Failure Pattern 1: 설계 리뷰에서 C++ 구현처럼 보이는 코드 블록을 출력함

설계 리뷰 요청에서는 `UINTERFACE`, `UCLASS`, `GENERATED_BODY`, `DECLARE_DYNAMIC_MULTICAST_DELEGATE`, include, `.generated.h`, `.h/.cpp` 구조처럼 컴파일 코드로 오해될 수 있는 블록을 쓰지 않는다.

설계 리뷰에서는 기본적으로 ```cpp 코드 펜스를 쓰지 않는다. 함수 목록과 호출 흐름은 ```text 코드 펜스 또는 표로 쓴다. `Pseudocode only`라고 적었더라도 `UINTERFACE`, `GENERATED_BODY`, `virtual ... = 0`, `UFUNCTION`, delegate 선언을 같이 쓰면 로컬 AI가 컴파일 가능한 Unreal 코드처럼 학습하므로 실패로 본다.

허용되는 형식:

```text
Pseudocode only:
IReceivableAction
- CanReceiveAction()
- GetActionRequirement()
- ApplyActionAttempt(ActionRequest)
```

금지되는 형식:

```text
UINTERFACE(...)
class UReceivableAction : public UInterface
{
    GENERATED_BODY()
};
```

사용자가 구현 코드를 명시적으로 요청하지 않았다면, 위와 같은 Unreal reflection 매크로 예시는 설계 리뷰 실패로 간주한다.

## Failure Pattern 2: 인터페이스가 대상 내부 상태를 과도하게 노출함

인터페이스는 사용 사례 중심의 최소 계약이어야 한다. 대상이 Shield, Lock, CaptureState, Health 같은 내부 상태를 가진다고 해서 receivable action interface에 상세 getter나 복구 요청을 자동으로 넣지 않는다.

대상 수신 인터페이스에 적합한 후보:

- `CanReceiveAction`
- `GetReceivableState`
- `GetActionRequirement`
- `ApplyActionAttempt`
- `ResolveActionAttempt`

대상 수신 인터페이스에 부적합한 후보:

- 대상 내부 상태 상세 getter 모음
- `SetTargetState`
- `SetIsVulnerable`
- `RequestInternalRestore`
- `OnActionCompleted`
- `OnTargetStateChanged`

UI가 대상 상태 수치를 표시해야 한다면 별도의 read model, status query, 상태 소유 객체의 조회 API, 또는 UI 전용 presenter 경로를 검토한다. 수신 가능성 판단에 필요한 최소 정보만 대상 인터페이스 계약에 둔다.

`IsVulnerable`, `IsCaptured`, `IsUnlocked` 같은 상태 query도 대상 수신 인터페이스의 기본 함수로 넣지 않는다. 후속 행동/데미지/UI 표시와 관련된 Target 상태라면 별도 status interface, 상태 소유 객체의 query, 또는 후속 receiver가 내부적으로 처리하는 규칙으로 분리한다.

## Failure Pattern 3: 프로세스 이벤트와 상태 이벤트를 한 소유자로 뭉침

상태 SSOT와 프로세스 SSOT가 다르면 Event/Delegate 위치도 다를 수 있다.

- Progress의 SSOT가 수행자이면 `ProgressChanged`, `AttemptFinished` 같은 프로세스 알림은 수행자 또는 수행자 Component가 발행할 수 있다.
- ShieldBroken, VulnerabilityChanged, Captured, HealthChanged 같은 대상 상태 알림은 Target 또는 해당 상태 Component가 발행한다.
- 수행자가 Target의 상태 delegate를 Broadcast하지 않는다.
- Target이 수행자의 진행 타이머를 직접 구동하지 않는다.

답변은 "이 이벤트는 어느 상태 또는 프로세스를 알리는가?"를 기준으로 Event 위치를 나누어야 한다.

`ActionCompleted`는 수행자의 프로세스 완료 알림일 수 있다. `ShieldBroken`, `VulnerabilityStarted`, `Captured`, `Unlocked`는 Target의 상태 변경 알림이다. 이 둘을 같은 Delegate 위치로 합치지 않는다.

## Failure Pattern 4: PlayerController 책임을 과도하게 빼앗음

프로토타입에서 `ActiveTarget`이 로컬 입력, 조준, UI 선택에만 쓰이는 임시 참조라면 PlayerController가 소유해도 된다. 무조건 PlayerState, TargetingComponent, Manager로 옮기라고 제안하지 않는다.

분리해야 하는 경우:

- 타겟 상태가 네트워크로 복제되어야 한다.
- Pawn 사망/교체 후에도 유지되어야 한다.
- AI, UI, 보조 캐릭터, 무기 등 여러 시스템이 같은 targeting 규칙을 공유한다.
- 타겟 검색, 우선순위, line trace, lock-on 유지가 커져서 독립 테스트가 필요하다.

그 외에는 PlayerController가 입력을 명령으로 바꾸고 `ActiveTarget` 참조를 중재하는 단순 구조가 프로토타입에 더 적합할 수 있다.

## Failure Pattern 5: 나쁜 예시 코드가 다른 종류의 오류를 가르침

금지 패턴 예시는 핵심 위반만 보여야 한다. 예를 들어 "Controller가 Target 상태를 직접 수정하는 문제"를 보여주려는데 잘못된 Cast 타입, 선언 안 된 변수, 존재하지 않는 함수까지 섞으면 검수 품질이 떨어진다.

나쁜 예시도 다음을 지킨다.

- 의사코드로 표시한다.
- 핵심 위반 외의 컴파일 오류를 만들지 않는다.
- 금지 패턴을 보여준 뒤, 바로 책임이 분리된 대안을 제시한다.
- 실제 Unreal API 시그니처를 확신하지 못하면 C++ 코드처럼 쓰지 않는다.

## Failure Pattern 6: RAG를 검색했다고 말하지만 근거가 약함

최종 답변에는 "검색했다", "RAG를 봤다" 같은 과정 설명보다 실제 반영한 근거를 적는다.

좋은 표기:

- `User RAG guideline: Interface and API Design Rules > Critical Rule: Do Not Mix Interface and Event`
- `User RAG guideline: AI Review Failure Patterns > Failure Pattern 2: 인터페이스가 대상 내부 상태를 과도하게 노출함`

나쁜 표기:

- `Source 1`
- `RAG에서 봄`
- `공식 문서상 그렇다`라고 말하지만 실제 근거가 사용자 가이드뿐인 경우

## Failure Pattern 7: 숨은 사고 과정을 최종 답변에 노출함

최종 답변에는 긴 내부 사고 과정, "Let me analyze", "Thought", "I need to search" 같은 문장을 출력하지 않는다. 사용자가 필요한 것은 검수 결과, 판단 기준, 수정안이다.

권장 출력:

- 결론
- 책임 분해 표
- 위험 5개
- 수정 방향
- 근거
- 자체 감사

## Failure Pattern 8: 자체 점수와 실제 출력이 불일치함

자체 감사 점수가 9.2 이상이어도 다음 위반이 있으면 실패다.

- 설계 리뷰에서 Unreal reflection 코드 블록을 출력함.
- Interface에 Event 함수를 넣음.
- 일반 setter를 안전한 API로 제안함.
- 금지한다고 말한 패턴을 예시 코드에서 다시 사용함.
- 선언되지 않은 함수, 변수, Delegate, TimerHandle을 사용함.
- RAG 근거 타입을 잘못 표기함.

점수는 면죄부가 아니다. Hard Failure가 하나라도 있으면 점수와 관계없이 답변을 수정해야 한다.

## Failure Pattern 9: RequestX와 ApplyXSuccess의 소유자를 섞음

`RequestX`와 `ApplyXSuccess`를 같은 대상 인터페이스에 무심코 함께 넣으면 호출 방향이 흐려진다.

수행 주체가 진행 프로세스를 소유하는 구조라면:

- `Performer.RequestAction(Target)`은 수행자의 public API다.
- 수행자는 Target이 해당 시도를 받을 수 있는지 확인하고 Progress/Timer를 소유한다.
- 완료 시 Target의 최소 계약을 호출한다.

Target 인터페이스에는 다음 중 하나를 우선한다.

```text
Pseudocode only:
IReceivableAction
- CanReceiveAction(ActionContext)
- ApplyActionAttempt(ActionRequest) -> ActionResult
```

또는 정말 성공 판정이 외부에서 끝난 구조라면:

```text
Pseudocode only:
IReceivableAction
- CanReceiveAction(ActionContext)
- ApplyActionSuccess(Initiator or ActionResult)
```

단, `ApplyActionSuccess`는 Target 내부에서 다시 상태 유효성을 검사해야 한다. 이미 대상 상태가 바뀌었거나 면역/무효 상태가 되었을 수 있기 때문이다.

## Failure Pattern 10: 프로세스 성공 판정과 대상 효과 적용을 혼동함

수행자가 미니게임 성공, 거리 유지, 타이머 완료, 입력 유지, 파워 계산을 판정할 수 있다. 그러나 Target이 Shield, Resistance, Invincible, Vulnerability, Captured, Unlocked 같은 대상 상태를 소유한다면 최종 효과 적용 여부는 Target이 재검증해야 한다.

권장 구분:

- Process success: 수행자가 시도 과정이 끝났는지 판단한다.
- Target acceptance: Target이 현재 상태 기준으로 효과를 받아들일지 판단한다.
- State mutation: Target이 대상 소유 상태를 변경하고 상태 이벤트를 발행한다.

따라서 `Performer decides bSuccess -> Target blindly ApplyActionSuccess` 흐름보다 `Performer builds ActionRequest -> Target Resolve/ApplyActionAttempt -> ActionResult` 흐름이 더 안전하다.

## Failure Pattern 11: 내부 mutation 메서드를 외부 Blueprint API처럼 노출함

`BreakShield`, `StartVulnerability`, `ForceExposeWeakPoint`, `CoolDown`, `AddHeat` 같은 함수는 이름이 setter가 아니어도 위험할 수 있다. 외부에서 원본 상태를 검증 없이 바꿀 수 있다면 사실상 setter와 같은 문제다.

구분한다.

- External command: 외부에서 호출 가능한 안전한 요청. 예: `ApplyActionAttempt`, `ApplyDamageRequest`, `TryFire`.
- Internal mutation: 소유자 내부에서만 호출하는 상태 변경 단계. 예: `BreakShieldInternal`, `BeginVulnerabilityInternal`, `ApplyHeatFromShot`.
- Debug/editor command: 테스트용 강제 변경. 예: `Debug_BreakShield`, `EditorOnly_SetHeat`.

설계 리뷰에서 `BreakShield`를 public `BlueprintCallable`처럼 제안하지 않는다. 외부 API는 검증과 이벤트 발행을 포함한 의도 기반 요청이어야 한다.

## Failure Pattern 12: AddX/CoolDown 같은 일반 mutation을 안전하다고 착각함

`SetX`가 아니어도 `AddHeat(float)`, `CoolDown(float)`, `AddShield(float)`, `RemoveHealth(float)`처럼 값을 직접 바꾸는 함수는 외부 API로 위험할 수 있다.

Weapon Heat의 경우 권장 방향:

- 외부: `TryFire`, `CanFire`, `GetHeatState`
- 내부: 사격 성공 후 `ApplyHeatFromShot`
- 내부/타이머: 냉각 처리
- 디버그: `Debug_SetHeat`

냉각 타이머나 Tick은 WeaponComponent의 프로세스이며, 외부 객체가 임의 DeltaTime으로 `CoolDown`을 호출하지 않게 한다.

## Failure Pattern 13: Unreal Damage API를 확인 없이 정확한 코드처럼 씀

설계 리뷰에서 `AActor::TakeDamage`, `UGameplayStatics::ApplyDamage`, `FDamageEvent`, `Instigator`, `DamageCauser` 같은 API를 정확한 C++ 코드처럼 쓰지 않는다.

특히 ActorComponent의 `this`를 DamageCauser로 넘기는 예시는 위험하다. DamageCauser가 Actor를 요구하는 API라면 WeaponComponent가 아니라 Weapon actor 또는 `GetOwner()` 계열 Actor를 넘겨야 할 수 있다. 정확한 시그니처와 프로젝트 사용 패턴을 확인하기 전에는 다음처럼 표현한다.

```text
Pseudocode only:
WeaponComponent builds DamageRequest
WeaponComponent sends DamageRequest to Target
Target resolves shield, armor, vulnerability, and health change
```

Unreal Damage API를 컴파일 가능한 코드로 제시하려면 공식 API/엔진 소스/프로젝트 기존 사용 예시를 확인한다.

## Failure Pattern 14: 낮은 자체 점수를 그대로 출력하고 끝냄

설계 리뷰 품질 게이트가 9.2라면 8.0이라는 점수를 출력하고 끝내면 안 된다. 모델은 낮은 점수를 "보고"하는 것이 아니라, 감점 원인을 반영해 Final을 다시 작성해야 한다.

허용:

- "초안 기준으로는 8.0이므로 다음을 수정한다"라고 내부 Audit에서 판단한다.
- 수정 후 Final에는 9.2 이상을 목표로 한 구조만 제시한다.

금지:

- 자기 답변이 8.0이라고 하면서 수정하지 않음.
- Hard Failure가 있는데 점수표로 넘어감.

## Failure Pattern 15: 질문 뒤에 의사결정을 미룸

설계 리뷰는 필요한 질문을 할 수 있지만, 질문으로 판단을 대체하지 않는다. 사용자가 제공한 조건만으로 합리적 기본안을 먼저 제시하고, 열린 질문은 "이 조건이면 바뀐다"는 형태로 제한한다.

예:

- 기본안: 수행자가 Progress/Timer를 소유하고 Target이 ActionResult를 적용한다.
- 추가 조건: 동시 시도가 필요하면 Target 또는 전용 Component에 reservation/claim 상태를 둔다.

질문만 나열하고 책임 분리 결론을 흐리면 실패다.

## Failure Pattern 16: 수행자 요구를 무시하고 Target-owned process로 되돌아감

사용자가 특정 객체가 행동을 실행, 채널링, 유지, 진행한다고 명시했는데 Progress/Timer, 완료 통지, 취소 상태를 Target으로 밀면 설계 의도를 잃는다.

기본값:

- Performer owns Progress/Timer/Cancel.
- Target owns target state and target acceptance.

Target-owned process를 제안하려면 "왜 Target이 프로세스를 소유해야 하는지" 별도 근거를 제시한다. 근거 없이 `상태가 Target 소유 -> Progress도 Target 소유`라고 결론 내리면 실패다.

## Failure Pattern 17: Interface가 구체 클래스 포인터에 의존함

Target-facing interface에 프로젝트별 수행자 Actor/Component 구체 타입을 넣으면 Target이 수행자의 구현 클래스를 알게 된다. 단방향 의존성을 말하면서 이런 파라미터를 쓰면 자기모순이다.

권장:

- `AActor* Initiator`
- `UObject* InstigatorObject`
- 역할 중심 최소 인터페이스
- `FActionRequest`

설계 리뷰에서는 request object를 우선하고, 컴파일 가능한 구조체/인터페이스 코드는 구현 모드에서만 작성한다.

## Failure Pattern 18: 같은 API 이름을 금지와 권장에 동시에 사용함

`BreakShield`, `UnlockDoor`, `CompleteCapture` 같은 내부 mutation 이름을 "Interface 금지"라고 말한 뒤 public API 권장 예시에도 같은 이름으로 쓰면 혼란을 만든다.

구분:

- 외부 요청: `ApplyActionAttempt`, `ResolveActionAttempt`, `ApplyDamageRequest`
- 내부 mutation: `BreakShieldInternal`, `UnlockDoorInternal`, `BeginVulnerabilityInternal`
- 디버그 강제: `Debug_BreakShield`, `Debug_UnlockDoor`

같은 이름을 금지/권장 양쪽에 쓰려면 public/private 문맥과 호출자를 명확히 밝힌다.

## Failure Pattern 19: CancelXRequest와 GetXProgress의 소유권을 설명하지 않음

`CancelXRequest`와 `GetXProgress`를 Target interface에 넣기 전에 누가 프로세스 SSOT인지 먼저 정한다.

Performer-owned process라면:

- `CancelX`는 수행자 API다.
- `GetXProgress`는 수행자 또는 UI-facing read model에 둔다.
- Target interface에는 기본적으로 넣지 않는다.

Target-owned process라면:

- Target이 cancel/duplicate/failure 계약을 가진다.
- 왜 Target-owned process인지 근거를 제시한다.

## Failure Pattern 20: optional interface function의 기본값을 숨김

`IsVulnerable()` 같은 선택 함수에 기본 구현을 두면 구현체가 오버라이드하지 않아도 호출이 성공한다. 이 경우 항상 false 같은 침묵 실패가 생길 수 있다.

설계 리뷰에서는 선택 함수라면 다음 중 하나를 명시한다.

- 필수 구현으로 둔다.
- 별도 interface로 분리한다.
- 구현하지 않았을 때의 보장된 의미를 문서화한다.
- 호출자가 feature support를 먼저 확인한다.

## Failure Pattern 21: 진행형 프로세스를 모든 Target Tick으로 처리함

Target이 `ProgressTime += DeltaTime`을 Tick에서 처리하는 예시는 기본값으로 제안하지 않는다. 진행 중인 대상이 하나여도 모든 Target Tick 정책을 고민해야 하기 때문이다.

기본은 timer-based active process다. Tick이 필요하면 진행 중에만 Tick을 켜고 종료/취소 시 끄는 계약을 명시한다.

## Failure Pattern 22: Blueprint interface Execute_ 호출과 virtual 호출을 섞음

`IReceivableAction::Execute_RequestAction(Object, Args)` 같은 호출을 쓰려면 해당 함수가 `UFUNCTION(BlueprintNativeEvent)` 또는 `BlueprintImplementableEvent` 계열이어야 한다. 일반 C++ virtual 예시와 `Execute_` 호출을 같은 답변에 섞으면 선언/호출 불일치다.

설계 리뷰에서는 이 문법을 쓰지 않는다. 구현 모드에서는 UINTERFACE 선언 방식과 Execute_ 호출 방식이 일치하는지 확인한다.

## Failure Pattern 23: 같은 행동의 이름이 문서 안에서 바뀜

한 답변 안에서 수행자 API가 `ExecuteAction`, `RequestAction`, `StartAction`, `BeginAction`으로 계속 바뀌면 구현자가 책임 흐름을 잃는다.

리뷰 답변은 하나의 이름을 선택하고 끝까지 유지한다.

권장:

- 외부 입력 전달: `Performer.RequestAction(Target)`
- 내부 시작: `BeginActionProcess`
- 완료 처리: `CompleteActionProcess`
- 취소: `CancelAction`

## Failure Pattern 24: 중복 요청, 취소, 실패 경로를 성공 경로 뒤에 숨김

진행형 액션 설계는 성공만 있으면 부족하다. 최소한 다음 계약을 검수한다.

- 이미 진행 중인 Target에 다시 요청하면 어떻게 되는가?
- 수행자가 죽거나 비활성화되면 어떻게 되는가?
- Target이 사라지거나 바뀌면 어떻게 되는가?
- 범위 이탈/시야 상실/피격 중단이 있는가?
- 실패 시 누가 어떤 이벤트나 결과를 받는가?

이 계약이 없으면 프로토타입에서 가장 먼저 꼬인다.

## Failure Pattern 25: 자기평가와 수정안이 연결되지 않음

자체 평가에서 `Interface/Event 분리 6/10`, `프로세스 SSOT 4.5/10`처럼 낮게 평가했다면 바로 그 항목을 고치는 수정안을 작성해야 한다.

점수표는 보고서 장식이 아니다. 낮은 항목마다 Final 전에 반영할 수정이 있어야 한다.
