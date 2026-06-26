# Quality Gates for Unreal C++ Design Review

## 검색 키워드

quality gate, Unreal review, self audit, Draft Audit Final, interface event separation, SSOT, process SSOT, mutation API, code consistency, design review no code generation, 9.2 quality gate

## 목적

이 문서는 Unreal C++ 설계 리뷰 답변을 단순 답변이 아니라 검수 파이프라인 산출물로 만들기 위한 최상위 품질 게이트다. 설계 리뷰 요청에서는 컴파일 가능한 C++ 구현 코드를 작성하지 않는다. 필요한 경우 `[의사코드]` 또는 함수 시그니처 수준만 제시한다. `.h/.cpp` 구현 코드는 사용자가 명시적으로 구현을 요청했을 때만 작성한다.

모든 설계 리뷰 답변은 최종 출력 전에 `Draft -> Audit -> Final` 흐름을 통과해야 한다. 내부 Draft에서 위험을 찾고, Audit에서 금지 규칙 위반을 제거한 뒤, Final만 사용자에게 제시한다.

## Review Mode / Implementation Mode

| 모드 | 조건 | 허용 | 금지 |
|---|---|---|---|
| Design Review Mode | 사용자가 설계 검토, 구조 평가, 리뷰, 방향 판단을 요청함 | 책임 분해, 위험 검출, 수정안, 의사코드, 함수 시그니처 | 컴파일 가능한 `.h/.cpp` 전체 구현 코드 |
| Implementation Mode | 사용자가 구현, 코드 작성, 파일 수정, 컴파일 가능한 예제를 명시적으로 요청함 | RAG/API 확인 후 `.h/.cpp` 코드 작성 | 확인 안 된 Unreal API/시그니처를 컴파일 가능 코드처럼 제시 |

Design Review Mode에서는 Unreal API 시그니처를 단정하지 않는다. 확실하지 않은 API, 매크로, 함수 시그니처, 버전 차이는 `확인 필요`, `의사코드`, `컴파일 근접 코드가 아님` 중 하나로 표시한다.

Implementation Mode에서도 코드 작성 전 RAG 근거, 프로젝트 기존 패턴, Unreal API 시그니처, UFUNCTION/UINTERFACE 규칙, generated.h 위치, include, Build.cs 의존성을 확인한다.

Design Review Mode에서는 `UCLASS`, `UINTERFACE`, `USTRUCT`, `GENERATED_BODY`, `DECLARE_DYNAMIC_MULTICAST_DELEGATE`, include, `.generated.h`, `.h/.cpp`처럼 컴파일 구현으로 오해될 수 있는 코드 블록을 만들지 않는다. 사용자가 구현을 요청하지 않았다면 함수 목록, 책임표, 호출 흐름, `Pseudocode only` 텍스트만 사용한다. 설계 리뷰에서는 기본적으로 ```cpp 코드 펜스를 쓰지 않는다.

## Required Review Pipeline

설계 리뷰 답변은 다음 4단계를 따른다.

1. 분석
   - 행동 주체, 행동 대상, 상태 SSOT, 프로세스 SSOT, 결과 판정자, 최종 상태 변경자, 외부 API, 금지 데이터, Event 위치를 표로 분리한다.

2. 위험 검출
   - SSOT 위반, God Object, Interface 비대화, 일반 setter, Event/Interface 혼합, 선언/호출 불일치, Unreal C++ 구현 위험을 찾는다.
   - 위험을 지적할 때 현재 문제, 즉시 문제, 확장 시 깨지는 지점, 더 나은 분리, 최소 수정안을 함께 설명한다.

3. 수정안 생성
   - 위험을 제거한 최소 구조만 제안한다.
   - 설계 리뷰라면 전체 C++ 구현 코드 대신 `[의사코드]`, 책임표, 호출 흐름, 함수 시그니처만 제시한다.
   - 프로토타입 리뷰에서는 무조건 Component, Manager, PlayerState로 분리하지 않는다. 로컬 입력용 임시 상태는 PlayerController에 남기는 편이 더 단순할 수 있다.
   - Request/Apply API의 소유자를 분리한다. 수행 주체의 `RequestX`와 대상의 `ApplyX`/`ResolveX`를 같은 인터페이스에 섞지 않는다.
   - 사용자가 특정 객체가 행동을 실행, 유지, 채널링, 진행한다고 말하면 performer-owned process를 기본값으로 둔다. Target-owned process를 제안하려면 별도 근거를 제시한다.

4. 자체 감사
   - 방금 제안한 구조가 앞에서 말한 금지 규칙을 어기지 않는지 다시 검사한다.
   - 종합 점수가 9.2/10 미만이면 최종 답변 전에 무엇을 고쳐야 하는지 먼저 정리하고 수정안을 다시 작성한다.

## Gate 1: Responsibility Decomposition

최종 답변 전 반드시 다음 표를 만든다.

| 항목 | 설명 |
|---|---|
| 행동 주체 | 행동을 실제 수행하는 객체 |
| 행동 대상 | 행동의 영향을 받는 객체 |
| 상태 SSOT | 변경되는 상태를 소유하는 객체 |
| 프로세스 SSOT | 진행률, 타이머, 시도 상태를 소유하는 객체 |
| 결과 판정자 | 성공/실패, 결과량, 적용 가능 여부를 판정하는 객체 또는 규칙 |
| 최종 상태 변경자 | 실제 상태 변경을 수행하는 객체 |
| 외부 호출 API | 외부에서 호출 가능한 안전한 함수 |
| 외부 직접 수정 금지 데이터 | 외부에서 직접 수정하면 안 되는 변수/상태 |
| Event/Delegate 발생 위치 | 상태 변경 후 알림을 발행하는 객체 |

상태 SSOT와 프로세스 SSOT는 다를 수 있다. 예를 들어 상호작용 진행률은 수행자의 InteractionComponent가 소유하고, 최종 잠금 해제 상태는 Target의 LockStateComponent가 소유할 수 있다.

수행자는 `Progress`와 `Timer` 같은 프로세스의 SSOT를 가질 수 있다. 이것은 대상의 `Health`, `Shield`, `Vulnerability`, `Captured`, `Unlocked` 같은 상태 SSOT를 침범하는 것이 아니다. 수행자가 금지되는 것은 대상의 내부 상태를 직접 수정하거나 대상의 상태 이벤트를 직접 Broadcast하는 것이다.

진행 시간이 반드시 대상에 있어야 하는 것은 아니다. 대상 상태 소유와 진행 프로세스 소유를 분리한다.

성공/실패 판정은 반드시 한 객체로 고정하지 않는다. Performer-owned resolution, Target-owned resolution, Shared resolution, Separate Resolver/System-owned resolution 중 하나로 분류하고 이유를 설명한다.

## Gate 2: Interface / Event Separation

Interface는 외부 호출 계약이다. Event/Delegate는 이미 일어난 일을 알리는 통지다.

Interface에 허용:

- `CanX`
- `IsX`
- `GetX`
- `FindX`
- `TryX`
- `RequestX`
- `ApplyX`

Interface에 금지:

- `OnXChanged`
- `OnXStarted`
- `OnXCompleted`
- `OnXFailed`
- `SetX`
- `BeginInternalX`
- `TransitionToX`
- `TickX`
- `BroadcastX`
- UI/VFX/SFX 알림 함수
- Timer/Progress 내부 관리 함수

Event/Delegate는 상태 소유 객체에 둔다. 상태 변경 후 내부 정합성 정리까지 끝난 뒤 Broadcast한다. 외부 객체가 다른 객체의 Delegate를 직접 Broadcast하지 않는다.

상태 SSOT와 프로세스 SSOT가 다르면 Event/Delegate 위치도 나뉜다. 진행률, 타이머, 시도 상태 변경 알림은 프로세스 소유 객체가 발행할 수 있고, Shield/Health/Vulnerability 같은 대상 상태 변경 알림은 상태 소유 객체가 발행한다.

`ActionCompleted`가 프로세스 완료 알림이면 프로세스 소유자 이벤트다. `ShieldBroken`, `VulnerabilityStarted`, `Captured`, `Unlocked` 같은 대상 상태 변경 알림이면 대상 또는 대상 상태 Component 이벤트다.

## Gate 3: Mutation API

상태 변경 API는 의도를 표현해야 한다. 일반 setter는 기본 금지다.

나쁜 API:

- `SetHealth`
- `SetShieldValue`
- `SetState`
- `SetCooldown`
- `SetAmmo`
- `SetIsHacked`

좋은 API:

- `ApplyDamage`
- `ApplyDamageRequest`
- `RestoreShield`
- `ApplyActionAttempt`
- `ResolveActionAttempt`
- `ApplyActionFailure`
- `ConsumeAmmo`
- `StartCooldown`
- `CompleteObjective`

`BreakShield`, `BeginVulnerability`, `AddHeat`, `CoolDown` 같은 이름은 owner-internal mutation으로는 가능하지만, 외부 public API로 제안하려면 검증, 권한, 이벤트 발행, 호출 조건을 포함해야 한다.

디버그/에디터 전용 setter는 `Debug_SetX` 또는 `EditorOnly_SetX`처럼 명시한다. 이 API를 일반 런타임 gameplay mutation API로 제안하지 않는다.

## Gate 4: Code Consistency

설계 리뷰 중 코드 예시는 기본적으로 `[의사코드]`다. 컴파일 가능하다고 주장하려면 다음을 모두 검사한다.

- 호출하는 함수가 선언되어 있는가
- 사용하는 변수가 선언되어 있는가
- Delegate 타입과 Delegate 멤버가 선언되어 있는가
- TimerManager를 쓰면 `FTimerHandle`이 선언되어 있는가
- BlueprintNativeEvent면 `_Implementation`이 필요한가
- Blueprint 구현 가능 인터페이스면 `Execute_` 호출 방식이 필요한가
- private `UPROPERTY` + `BlueprintReadOnly`면 `meta=(AllowPrivateAccess="true")`가 필요한가
- `.generated.h` 위치가 마지막 include인가
- include와 forward declaration이 맞는가
- Build.cs 의존성이 맞는가

하나라도 불확실하면 `컴파일 가능한 코드`라고 말하지 않는다. `확인 필요`, `[의사코드]`, `컴파일 근접 코드가 아님`으로 표시한다.

설계 리뷰에서 나쁜 예시를 들 때도 핵심 위반 외의 오류를 섞지 않는다. 잘못된 Cast 타입, 선언되지 않은 변수, 존재하지 않는 함수, 불확실한 Unreal API를 예시 코드에 넣지 않는다. 확신이 없으면 C++ 형태 대신 텍스트 의사코드로 쓴다.

`AActor::TakeDamage`, `UGameplayStatics::ApplyDamage`, `FDamageEvent`, `Instigator`, `DamageCauser` 같은 Unreal Damage API는 시그니처와 프로젝트 사용 패턴 확인 전에는 컴파일 코드처럼 쓰지 않는다. 설계 리뷰에서는 `DamageRequest` 흐름으로 표현한다.

`IInterface::Execute_Function` 호출 예시는 UFUNCTION BlueprintNativeEvent/BlueprintImplementableEvent 선언과 함께 맞아야 한다. 설계 리뷰에서는 Execute_ 호출을 컴파일 코드처럼 쓰지 않는다.

## Gate 5: Self-Contradiction Check

최종 답변 전 다음 질문에 모두 통과해야 한다.

- 금지한다고 말한 패턴을 코드 예시에서 다시 사용했는가?
- Interface와 Event/Delegate를 섞었는가?
- 상태 SSOT와 프로세스 SSOT를 혼동했는가?
- 일반 setter를 안전한 API처럼 제안했는가?
- 구체 클래스 포인터를 interface 계약에 넣어 단방향 의존성을 깨뜨렸는가?
- 중복 요청, 취소, 실패 경로 계약을 빠뜨렸는가?
- 프로세스 이벤트와 상태 이벤트 위치를 섞었는가?
- 구현체 전용 개념을 범용 인터페이스 계약에 넣었는가?
- RAG 예시의 Project-specific 용어를 Core 규칙처럼 사용했는가?
- 위험 지적에서 즉시 문제와 확장 시 깨지는 지점을 설명하지 않았는가?
- 선언에 없는 함수/변수/Delegate/TimerHandle을 사용했는가?
- RAG 사용자 가이드를 Epic 공식 근거처럼 말했는가?
- RAG 근거를 Source 번호만으로 표기했는가?

위반이 있으면 최종 답변 전에 수정한다.

## Gate 6: RAG Evidence Citation

RAG 근거는 Source 번호만 쓰지 않는다. 문서명/파일명/섹션명/요약을 함께 쓴다.

근거 타입을 구분한다.

- User RAG guideline: 사용자 작성 프로젝트 규칙
- Epic official documentation: Epic 공식 문서
- Unreal Engine source: 엔진 소스
- Local project source: 로컬 프로젝트 소스

예시:

- `User RAG guideline: Quality Gates for Unreal C++ Design Review > Gate 2: Interface / Event Separation`
- `User RAG guideline: Damage Responsibility Rules > Target Responsibilities`
- `Unreal Engine source: LyraAttributeSet.h > ATTRIBUTE_ACCESSORS comment`

사용자 작성 가이드를 Epic 공식 근거처럼 말하지 않는다.

## Gate 7: Final Self Audit Score

설계 리뷰 답변 끝에는 다음 자체 평가 표를 포함한다.

| 항목 | 점수 / 10 | 감점 이유 |
|---|---:|---|
| 책임 분리 |  |  |
| 상태 SSOT / 프로세스 SSOT 구분 |  |  |
| Interface / Event 분리 |  |  |
| Mutation API 설계 |  |  |
| Unreal C++ 구현 안정성 |  |  |
| 자기모순 없음 |  |  |
| 근거 사용 정확성 |  |  |
| 종합 |  |  |

종합 점수가 9.2 미만이면 Final을 출력하지 않는다. 먼저 부족한 항목을 수정한 뒤 9.2 이상으로 다시 자체 감사한다.

## Hard Failure Conditions

다음 중 하나라도 있으면 설계 리뷰 실패다.

- 설계 리뷰 요청인데 컴파일 가능한 `.h/.cpp` 전체 구현 코드를 작성함.
- 설계 리뷰 요청인데 `UCLASS`, `UINTERFACE`, `GENERATED_BODY`, delegate 선언처럼 구현 코드로 오해될 수 있는 Unreal reflection 코드 블록을 작성함.
- 설계 리뷰 요청인데 ```cpp 코드 펜스로 Unreal C++ 예시를 작성함.
- Interface에 `OnXChanged`, `OnXStarted`, `OnXCompleted` 같은 Event 함수를 넣음.
- 일반 setter `SetX(value)`를 정상 gameplay mutation API처럼 제안함.
- `BreakShield`, `AddHeat`, `CoolDown` 같은 내부 mutation을 검증 없는 public API처럼 제안함.
- `RequestX`와 `ApplyXSuccess`의 호출 방향을 설명하지 않고 같은 인터페이스에 섞음.
- 사용자가 특정 객체가 행동 실행자라고 했는데 근거 없이 Progress/Timer를 Target으로 돌림.
- Target-facing interface에 프로젝트별 구체 Actor/Component 포인터 의존성을 넣음.
- `CancelXRequest`, `GetXProgress`를 프로세스 소유권 설명 없이 Target-facing interface에 넣음.
- Blueprint `Execute_` 호출 방식과 일반 C++ virtual 선언 방식을 같은 예시에서 섞음.
- Tick 기반 Progress를 모든 Target Actor의 기본 패턴처럼 제안함.
- 중복 요청, 취소, 실패 경로를 정의하지 않음.
- 금지한 패턴을 같은 답변의 코드 예시에서 다시 사용함.
- 코드 예시에서 선언되지 않은 함수/변수/Delegate/TimerHandle을 사용함.
- 확인되지 않은 Unreal API, 매크로, 함수 시그니처를 컴파일 가능한 코드처럼 단정함.
- 자체 평가가 9.2 미만인데 수정하지 않고 낮은 점수를 그대로 최종 출력함.
- RAG 사용자 가이드를 Epic 공식 문서처럼 표기함.
- RAG 예시를 현재 프로젝트의 필수 구조처럼 일반화함.
- Interface에 구현체 전용 개념을 넣음.
- 성공/실패 판정을 근거 없이 한 객체로 고정함.
