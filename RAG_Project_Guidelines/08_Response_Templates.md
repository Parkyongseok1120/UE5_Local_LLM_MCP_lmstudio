# Response Templates

## Design Review Pipeline Template

설계 리뷰 요청에서는 컴파일 가능한 전체 `.h/.cpp` 구현 코드를 작성하지 않는다. 필요한 경우 `[의사코드]` 또는 함수 시그니처만 제시한다.

```text
분석:
| 항목 | 내용 |
|---|---|
| 행동 주체 | |
| 행동 대상 | |
| 상태 SSOT | |
| 프로세스 SSOT | |
| 외부 호출 API | |
| 외부 직접 수정 금지 데이터 | |
| Event/Delegate 발생 위치 | |

위험 검출:
- [SSOT 위반 / Interface-Event 혼합 / 일반 setter / 선언 불일치 / Unreal API 불확실성]

수정안:
- [위험을 제거한 최소 구조]
- [의사코드 또는 함수 시그니처만 필요 시 사용]

근거:
- User RAG guideline: [Document] > [Section]
- Unreal Engine source: [File] > [Section]

자체 감사:
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
```

종합 점수가 9.2 미만이면 최종 답변 전에 수정안을 다시 작성한다.

## Critical Response Rules

코드 블록은 반드시 `Pseudocode only` 또는 `Compile-ready Unreal C++`로 구분한다. 확신이 없으면 의사코드로 둔다.

설계 리뷰에서는 ```cpp 코드 펜스를 쓰지 않는다. `UCLASS`, `UINTERFACE`, `GENERATED_BODY`, `DECLARE_DYNAMIC_MULTICAST_DELEGATE`, include, `.generated.h` 같은 Unreal 구현 코드 형태도 쓰지 않는다. 구현 요청이 아니라면 함수 목록, 책임표, 호출 흐름만 쓴다.

인터페이스에는 Event/Delegate 역할 함수를 넣지 않는다. `OnXChanged`, `OnXStarted`, `OnXCompleted`는 상태 소유 객체의 Delegate/Event로 분리한다.

일반 setter는 기본 제안에서 제외한다. `SetHealth`, `SetAmmo`, `SetIsHacked`보다 `ApplyDamage`, `ConsumeAmmo`, `ApplyActionAttempt`, `ResolveActionAttempt`, `RestoreShield`처럼 의도가 드러나고 재검증 가능한 Mutation API를 우선한다.

설계와 구현은 같은 이름을 사용해야 한다. 설계에서 Owner, Command, Query, Event를 정했으면 코드 예시도 같은 책임과 이름을 따라야 한다.

코드에서 호출하는 함수, 변수, Delegate, TimerHandle은 반드시 선언되어 있어야 한다. 생략된 부분은 `Pseudocode only` 또는 `project-specific type required`로 표시한다.

나쁜 예시도 검수 대상이다. 금지 패턴을 보여주기 위해 잘못된 Cast, 선언되지 않은 변수, 불확실한 Unreal API를 추가로 섞지 않는다.

`RequestX`와 `ApplyXSuccess`를 같은 인터페이스에 넣기 전에 호출 방향을 설명한다. 수행자가 프로세스를 소유하면 `RequestX`는 수행자 API, 대상 인터페이스는 `CanReceiveX`/`ApplyXAttempt` 같은 최소 계약을 우선한다.

`BreakShield`, `AddHeat`, `CoolDown`처럼 이름이 setter가 아니어도 외부에서 원본 상태를 직접 바꾸면 위험하다. 외부 command와 내부 mutation을 분리해서 답한다.

`AActor::TakeDamage`, `UGameplayStatics::ApplyDamage` 등 Unreal Damage API는 시그니처 확인 전에는 컴파일 가능한 코드로 쓰지 않는다.

RAG 근거는 `Source 1` 같은 번호만으로 쓰지 않는다. 문서명/파일명/섹션명과 근거 타입을 함께 쓴다. 사용자 작성 가이드는 Epic 공식 문서처럼 말하지 않는다.

검색 키워드: response code label, pseudocode only, compile-ready Unreal C++, no interface events, avoid setter, mutation API template

## Compile-Ready Code Header

```text
Code type: Compile-ready Unreal C++
Checked:
- Unreal API signatures
- UCLASS/USTRUCT/UINTERFACE/UFUNCTION rules
- generated.h position
- Build.cs dependencies
- Header/CPP include requirements
- Declared functions, member variables, delegates, and timer handles
- Interface Execute_FunctionName rules when Blueprint implementation is possible
```

## Pseudocode Header

```text
Code type: Pseudocode only
Purpose:
- Shows ownership and call flow.
- Do not copy directly into Unreal without checking API signatures, reflection macros, includes, and Build.cs.
```

## RAG Citation Template

```text
근거:
- User RAG guideline: [Document Name] > [Section Name]
- Epic official documentation: [Document Title] > [Section Name]
- Unreal Engine source: [File Name] > [Class/Function/Section]
- Local project source: [File Name] > [Class/Function/Section]
```

User RAG guideline은 프로젝트 규칙이고, Epic official documentation이나 Unreal Engine source는 엔진 근거다. 둘을 섞어 말하지 않는다.

## 목적

로컬 AI가 Unreal C++ 질문에 답할 때 일관된 형식으로, 근거와 안정성 체크를 포함하게 한다. 템플릿은 그대로 복사하기보다 상황에 맞게 짧게 조정한다.

검색 키워드: response template, Unreal assistant, Korean answer, code generation template, design review template, RAG citation, local AI

## 코드 생성 템플릿

```text
결론:
이 기능의 상태 원본은 [Owner]에 두고, [Caller]는 Command를 요청하는 구조가 안전합니다.

설계:
- Owner: [상태 소유자]
- Command: [상태 변경 함수]
- Query: [조회 함수]
- Event: [변경 알림]
- Data source: [DataAsset/Config/Runtime/SaveGame]
- Network: [Authority/Replication 필요 여부]

수정 파일:
- [Header]
- [CPP]
- [Build.cs 필요 여부]

코드:
[컴파일 가능한 최소 예시]

검증:
- [컴파일 명령 또는 Unreal Editor 빌드]
- [PIE에서 확인할 시나리오]
- [로그/OnScreen/Automation Test 확인]

주의:
[RAG 근거가 부족하거나 프로젝트별 타입 확인이 필요한 부분]
```

## 설계 리뷰 템플릿

```text
결론:
[진행 가능/재설계 필요]입니다. 가장 큰 리스크는 [리스크]입니다.

점수:
- 책임 분리: [0-5]
- SSOT: [0-5]
- 인터페이스/API: [0-5]
- Command/Query/Event: [0-5]
- Unreal 구현 안정성: [0-5]
- 네트워크/저장 영향: [0-5 또는 해당 없음]

필수 수정:
- [컴파일, 권한, SSOT, 수명 문제]

권장 수정:
- [품질 개선]

검증:
- [빌드/PIE/테스트]
```

## RAG 근거 부족 템플릿

```text
현재 RAG 컨텍스트만으로는 [API/클래스/모듈]을 확정하기 어렵습니다.

확인하면 좋은 검색어:
- [Unreal class/function]
- [project class/module]
- [feature keyword]

확인 전까지 안전한 방향:
- [추측 없이 가능한 설계 원칙]
- [컴파일 리스크를 줄이는 대안]
```

## 버그 수정 템플릿

```text
원인:
[Command/Query/Event, lifetime, SSOT, replication 중 어디가 깨졌는지]

수정:
- [변경 1]
- [변경 2]

왜 안전한가:
- [상태 원본 유지]
- [Unreal lifecycle/GC/authority 조건]

검증:
- [재현 단계]
- [수정 후 기대 결과]
```

## 답변 품질 규칙

- 한국어로 먼저 결론을 말한다.
- 코드가 있으면 Header/CPP/Build.cs 영향 여부를 함께 말한다.
- 프로젝트 가이드라인 RAG가 검색되면 일반 Unreal 예제보다 우선한다.
- 불확실한 Unreal API는 확정적으로 말하지 않는다.
- 숨은 추론 과정을 길게 쓰지 말고, 판단 근거와 실무 단계만 간결하게 제시한다.
- "RAG를 검색했다"는 과정 설명 대신 실제 반영한 문서명/섹션명을 쓴다.
- 자체 평가가 기준 미만이면 낮은 점수를 출력하고 끝내지 말고 수정안을 먼저 반영한다.
