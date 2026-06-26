# Design Review Scoring Rubric

## 9.2 Quality Gate

설계 리뷰 답변은 컴파일 가능한 C++ 구현 코드를 생성하지 않는다. 필요한 경우 `[의사코드]` 또는 함수 시그니처만 제시한다. 사용자가 구현을 명시적으로 요청했을 때만 `.h/.cpp` 코드를 작성한다.

리뷰 답변은 `Analysis -> Risk Detection -> Corrected Minimal Design -> Self Audit` 흐름을 따른다. 종합 자체 평가가 9.2/10 미만이면 최종 답변 전에 부족한 항목을 수정한다.

검색 키워드: 9.2 quality gate, design review no implementation code, Draft Audit Final, self audit, quality pipeline

## Critical Review Gates

설계 리뷰 중 코드 예시는 반드시 `Pseudocode only` 또는 `Compile-ready Unreal C++`로 라벨링한다. Compile-ready라고 제시할 경우 Unreal API 시그니처와 UFUNCTION/UINTERFACE 구현 규칙을 확인하지 못하면 리뷰 실패로 본다.

검색 키워드: review gate, pseudocode label, compile-ready label, interface event mixing, setter mutation, Unreal API accuracy

즉시 실패 조건에 추가:

- 인터페이스에 Event/Delegate 역할의 `OnXChanged`, `OnXStarted`, `OnXCompleted` 함수를 넣음.
- 설계 설명과 코드 예시가 서로 다른 책임 소유자, API 이름, 이벤트 경계를 사용함.
- 코드 예시에서 선언되지 않은 함수/변수/Delegate/TimerHandle을 사용함.
- Compile-ready라고 표시한 코드가 UINTERFACE/UFUNCTION/GENERATED_BODY/generated.h/Build.cs 규칙을 확인하지 않음.
- 일반 setter `SetX(Value)`를 검증이 필요한 gameplay mutation API로 제안함.
- Unreal API 이름 또는 시그니처를 RAG 근거 없이 확정적으로 사용함.
- WeaponComponent가 Target의 Health/Shield/Armor/WeakPoint/Invincible 내부 상태를 직접 수정함.
- RAG 근거를 `Source 1` 같은 번호만으로 표기하고 문서명/파일명/섹션명/근거 타입을 생략함.

리뷰 질문:

- 인터페이스는 Command/Query 계약만 갖고, Event는 상태 소유 객체의 Delegate/Event로 분리되어 있는가?
- 코드 블록은 의사코드인지 컴파일 가능한 코드인지 명시되어 있는가?
- 컴파일 가능한 코드라면 Unreal reflection/API 규칙을 실제로 만족하는가?
- 설계에서 말한 Owner, Command, Query, Event가 코드에서도 같은 이름과 책임으로 구현되는가?
- 코드에서 사용하는 모든 함수/변수/Delegate/TimerHandle이 선언되어 있는가?
- `SetX` 대신 의도가 드러나는 Mutation API를 사용했는가?
- RAG 근거가 User RAG guideline, Epic official documentation, Unreal Engine source, local project source로 구분되어 있는가?

## 목적

Unreal C++ 설계나 AI 생성 코드 제안을 검토할 때 같은 기준으로 점수화한다. 총점보다 치명적 결함을 먼저 본다.

검색 키워드: design review, scoring rubric, Unreal C++ review, SSOT review, responsibility review, implementation quality, code quality

## 점수 기준

각 항목은 0점에서 5점까지 평가한다.

| 항목 | 5점 기준 | 0점 기준 |
| --- | --- | --- |
| RAG 근거 | Unreal API와 프로젝트 패턴을 근거로 제시한다 | API와 클래스 이름을 추측한다 |
| 책임 분리 | 상태 소유자와 호출 방향이 명확하다 | 여러 객체가 같은 책임을 가진다 |
| SSOT | 원본 상태가 하나이며 캐시/복제/표시가 구분된다 | 같은 상태가 여러 곳에서 수정된다 |
| 인터페이스/API | 작고 의도가 드러나는 API를 제공한다 | public mutable 상태와 거대한 인터페이스를 노출한다 |
| Command/Query/Event | 변경, 조회, 알림이 이름과 흐름으로 분리된다 | Get 함수가 상태를 바꾸거나 Event가 Command처럼 쓰인다 |
| Unreal 생명주기 | Constructor/BeginPlay/EndPlay/OnRep 책임이 적절하다 | CDO/World/lifetime 규칙을 위반한다 |
| GC/메모리 | UPROPERTY/TObjectPtr/TWeakObjectPtr와 해제가 안전하다 | raw UObject 포인터와 미해제 delegate가 있다 |
| 네트워크 | Authority, RPC, Replication 원본이 명확하다 | 클라이언트가 원본 상태를 확정한다 |
| 데이터 설계 | DataAsset/Config/SaveGame/Runtime state가 구분된다 | 하드코딩과 런타임 DataAsset 변경이 섞인다 |
| 검증 가능성 | 컴파일, PIE, 테스트, 로그 확인 방법이 있다 | 검증 방법 없이 코드만 제시한다 |

## 판정

- 45점 이상: 구현 진행 가능. 작은 리스크만 남음.
- 35점 이상: 진행 가능하지만 지적 항목을 먼저 수정해야 함.
- 25점 이상: 구조 재검토 필요. 핵심 책임과 SSOT부터 다시 잡아야 함.
- 25점 미만: 코드 생성 금지. 요구사항과 소유자 설계부터 다시 작성해야 함.

## 점수 제한

다음 항목은 총점보다 우선하는 상한이다.

- 도메인 API 확실성 없음: 최대 8.0
- 코드 예시가 의사코드인데 컴파일 가능처럼 말함: 최대 7.5
- 요청하지 않은 네트워크/대형 구조 제안: 최대 6.5
- Interface에 구현체 전용 개념을 넣음: 최대 7.5
- State SSOT와 Process SSOT를 혼동함: 최대 7.0
- Event를 Interface에 넣음: 최대 7.0
- 일반 setter를 안전한 API처럼 제안함: 최대 7.5
- 자기모순이 있음: 최대 7.0

점수 제한에 걸리면 점수를 보고하고 끝내지 않는다. Final 전에 구조를 수정한다.

## 즉시 실패 조건

다음 중 하나라도 있으면 총점과 무관하게 재설계한다.

- 컴파일 불가능한 reflection 매크로 구조.
- 존재하지 않는 Unreal API를 확정적으로 사용.
- 클라이언트가 보상, 데미지, 인벤토리 같은 권한 상태를 확정.
- 같은 런타임 상태를 두 개 이상의 객체가 독립적으로 수정.
- UObject 수명 관리가 명백히 위험한 raw pointer/async/delegate 구조.
- Runtime 모듈이 Editor 전용 API에 직접 의존.

## 리뷰 응답 형식

1. 결론: 진행 가능 여부와 가장 큰 리스크.
2. 점수: 항목별 짧은 점수.
3. 필수 수정: 즉시 고쳐야 할 문제.
4. 권장 수정: 품질을 높이는 개선.
5. 검증: 컴파일/PIE/테스트/로그 확인 방법.
