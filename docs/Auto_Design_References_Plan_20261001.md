# Design references Auto 구현 계획

작성일: 2026-10-01 (Asia/Seoul)  
기준 커밋: `6a0e9b7`  
상태: 계획. 현재 소스의 책임과 호출 경로를 확인했으며 Auto 구현·LM Studio 실행·모델 품질 평가는 하지 않았다.

추가 검토: [작업 보고·API 확인·수명 검토 개선 계획](Model_Behavior_Incident_Plan_20261001.md). 새 로그에서 확인한 실제 변경과 완료 보고의 불일치, API 추측, 입력·수명 연결을 I1–I3에서 우선 보완한다. Auto는 그 계획의 I4로 연결한다. Auto 없이도 앞 단계가 유효해야 하며, 주제 자동 선택만으로 완료 환각·API 문제를 해결했다고 보고하지 않는다. 공통 기준은 기존 `core/proof`와 예산 축소 후보를 공유하고 별도 완료 판정기를 만들지 않는다.

현재 구조 재대조(`6893493`): 위 계획 §7.3/§8/§14의 RAG 조회 분류·관찰 identity·mutation origin·checkpoint optional projection·동기 서버 deadline·portable helper 편입을 I2–I3의 독립 보완으로 명시했다. Auto selector가 분류/권한/취소를 관리하는 변경은 아니다. 본 계획의 순수 주제 선택·추가 도구 호출 없음·기존 budget/최종화 경계는 유지한다.

## 1. 목적과 완료 기준

사용자가 Auto를 명시적으로 선택했을 때 현재 작업과 관련된 짧은 참고를 제공한다. 모델이 문제 원인, 구현 방법, 다음 도구, 작업 완료 여부를 판단하는 책임은 그대로 둔다. 추가 모델 추론이나 도구 실행 없이 기존 참고 선택을 확장하는 것이 이번 변경의 목적이다.

완료 조건은 다음과 같다.

- Off, 수동 주제, Documents/Focused의 기존 조합이 동작을 유지한다.
- Auto는 사용자 목적에서 주요 주제 하나를 정하고, 같은 실행의 유효한 관측에서 보조 주제 하나까지 정한다.
- 선택 결과가 프로젝트 사실·원인 진단·실행 지시·작업 완료 판정으로 사용되지 않는다.
- 기존 근거·예산·엔진 범위·취소·복구 담당자를 재사용한다.
- 참고를 넣을 수 없으면 이미 측정한 기준 입력으로 진행한다.
- 실제 prediction handler에서 추가 추론·도구·권한 변경이 없고, 관련 참고가 들어가는 정상 경로도 검증한다.

참고 입력이 정확해졌다는 결과와 모델이 더 좋은 코드를 만든다는 결과는 구분한다. 후자는 실제 사례를 별도로 관찰해야 한다.

## 2. 현재 확인한 구조

| 책임 | 현재 소스 | 확인한 계약 |
|---|---|---|
| 설정 선언·읽기 | `src/direct-config.ts`, `src/execution-config.ts` | 주제 기본 Off, delivery 기본 documents, 토큰 allowance 기본 2048 |
| 수동 선택·완결된 절 조합 | `src/design-guidance.ts` | 명시된 주제와 확인된/설정된 엔진을 사용. 주제 자체를 자동 변경하지 않음 |
| 문서 정본·배포 번들 | `docs/model-guidance/catalog.json`, Markdown, `scripts/build-design-guidance.cjs` | 주제·신호·적용 조건·동반 절을 정본에서 생성 |
| 사용자 목적의 이어짐 | `src/continuity-objectives.js` | 사용자 메시지와 짧은 계속 요청을 구분. activeObjective 및 출처를 제공 |
| 근거 수명 | `src/evidence-manager.ts`, `src/reference-context.ts` | 실행별 상태, 요청/결과 짝, 프로젝트·세션·파일 변경 확인 |
| 라운드 입력 구성 | `src/round-input.ts` | 기준 입력을 조립·측정한 뒤 참고를 선택. 참고는 영속 이력에 넣지 않음 |
| 예산 적용 | `ContextManager.addOptionalReferences`, `BudgetBroker` | 정확 측정·기존 watermark·출력 예약 적용. 참고가 맞지 않으면 기준 입력 유지 |
| 모델·도구 실행 | `src/prediction-loop.ts`, `src/tool-boundary.ts` | 모델 호출, 도구 범위·승인·실행 단계·종료를 관리 |
| 기존 별도 요약 추론 | `src/semantic-handoff.ts` | Hybrid에서 조건부로 같은 모델에 도구 없는 요약 요청. Auto와 별도 기능 |

현재 focused 선택은 관측 신호를 이용해 **수동으로 고른 주제 안에서** 절을 정렬한다. Auto 구현에서 모든 주제를 한꺼번에 열고 현재 정렬을 그대로 적용하면 진단 신호가 주요 작업을 밀어낼 수 있다. 주요 주제와 보조 주제를 먼저 구분해야 한다.

현재 `ReferenceSnapshot`의 `diagnostic_present`는 실패를 뜻하지 않는다. 경고도 진단일 수 있다. `compilation_pending`, `operation_outcome_unknown`, `source_body_unavailable`도 각각 대기·결과 불명·본문 가용성에 관한 신호이며 원인 진단이 아니다.

소스 확인은 설치된 LM Studio 설정이나 실행을 확인한 결과가 아니다. 기존 Hybrid의 추가 요약 호출은 존재하며 이번 계획에서 변경하지 않는다.

## 3. 설정과 호환성 결정

`designGuidanceMode`에 `auto`를 추가한다. 기본은 계속 `off`다. 새로운 GUI 숫자 설정이나 자동 설정 이전은 추가하지 않는다.

| mode | 저장된 delivery | 실제 delivery | 동작 |
|---|---|---|---|
| off | documents / focused | 없음 | 참고 선택·관측 수집·추가 측정을 건너뜀 |
| 수동 주제 | documents | documents | 기존 문서 단위 선택 유지 |
| 수동 주제 | focused | focused | 기존 명시 주제 안의 절 선택 유지 |
| auto | documents / focused | focused | 사용자 목적과 관측으로 주제를 정한 뒤 필요한 절만 선택 |
| 알 수 없는 mode | 무엇이든 | 없음 | 기존 정규화 규칙에 따라 Off |

Auto는 독립된 명시 선택이므로 한 번 선택해서 사용할 수 있게 한다. Auto에서 effective delivery가 focused라는 점을 GUI 설명과 README에 명시한다. 저장된 delivery 값은 바꾸지 않으며, 다시 수동 주제로 바꾸면 원래 delivery 설정이 적용된다. 로그에는 configuredDelivery와 effectiveDelivery를 구분한다.

- Observe only, allowance=0, 강제 최종 보고에서는 Auto도 생략한다.
- research recovery, tool-planning retry, reasoning recovery에서는 기존 focused 정책에 따라 생략한다.
- bounded audit의 일반 조사 라운드에서는 참고를 허용하되 읽기 전용 도구 경계에 영향을 주지 않는다.
- Hybrid/Deterministic/Legacy 작업 컨텍스트 설정은 이번 기능의 활성 조건으로 바꾸지 않는다. 각 경로에서 기존 측정·예산 계약을 따른다.
- Auto 값을 이전 버전이 읽으면 기존 mode 정규화에 의해 Off가 되는 호환 결과를 문서화한다.

`DesignGuidanceMode`에 문자열만 추가하면 기존 manual selector가 auto를 일반 주제로 처리할 수 있다. 설정 모드와 실제 참고 주제 타입을 분리하고, manual selector에는 auto가 들어가지 않도록 타입과 실행 시 방어를 함께 둔다.

## 4. SSOT와 SOLID의 책임 배치

| 데이터/행동 | 단일 담당자 | Auto가 할 일 |
|---|---|---|
| 현재 사용자 목적과 계속 요청 | 기존 objective continuity | 출처가 확인된 목적의 읽기 전용 결과를 받음 |
| 프로젝트/엔진 범위 | ToolScope | 주어진 범위만 사용 |
| 도구 관측, 세션·파일 유효성 | EvidenceManager/reference-context | 유효한 snapshot을 받음 |
| 주제 선택 규칙 | 새 순수 모듈 `guidance-topic-selection.ts` | 제한된 입력을 주제와 reason code로 변환 |
| 절 본문·적용 조건·동반 절 | 기존 catalogue/생성기/design-guidance | 기존 조합기를 재사용 |
| 참고의 토큰 증가와 입력 채택 | ContextManager/BudgetBroker | 후보를 전달하고 결과를 받음 |
| 모델 추론·도구 승인·종료·재시도 | 기존 실행 담당자 | 변경하지 않음 |

새 모듈은 모델 클라이언트, controller, 파일 시스템, 도구 registry, 예산 관리자를 받지 않는다. 별도 agent 객체, planner, tool router, 설계 점수기, 프로젝트 사실 DB, 영구 목표 저장소를 만들지 않는다.

기존 절 선택 내부의 적용 조건·동반 절 closure·중복 제거·렌더링을 공통으로 사용한다. 수동 선택은 기존 정책을 유지하는 wrapper로 남기고, Auto는 주요/보조 주제 순서만 제공한다. 버전 비교나 BudgetBroker 계산을 새 모듈에 복제하지 않는다.

OCP는 주제 규칙을 제한된 표로 추가할 수 있는 정도로 적용한다. 범용 policy framework, 플러그인식 분류기, 프로젝트마다 별도 구현 클래스는 필요하지 않다.

## 5. 주요 주제 입력과 출처

기존 `buildObjectiveContinuity` 결과를 재사용한다. `modelNotes.objectiveFingerprint`도 같은 함수에 의존한다. Auto를 위해 계속 요청/목표 변경 판정기를 따로 만들지 않는다.

1. 실행 시작 시 기존 `normalizeMessage`의 index 규칙을 적용한 원래 대화에서 objective continuity를 계산한다. 이미 압축된 대화라면 기존 `extractPriorContinuityState`로 얻은 이전 state를 재사용한다. 새 checkpoint JSON 파서는 만들지 않는다.
2. activeObjective가 실제 사용자 메시지인지, messageIndex와 source가 일치하는지 확인한다.
3. 사용자 목적을 참고 선택용 읽기 전용 값으로 변환한다. 기존 objective fingerprint는 연결 식별자로만 쓰고 사실 증명으로 쓰지 않는다.
4. 같은 실행의 라운드에서는 이 주요 주제를 유지한다. 모델 생각, 응답, 도구 오류가 주요 목적을 교체하지 않는다.
5. 다음 사용자 입력이 기존 실행 경계로 전달되면 그 경계에서 다시 계산한다.

`계속해` 같은 이어가기에서 antecedent가 확인되면 기존 목적을 사용한다. 이전 목적의 사용자 출처나 완전한 문맥을 확인할 수 없으면 작은 core로 낮춘다. 이전 assistant 요약, note의 decisions/openQuestions, 보관된 과거 목표를 새 사용자 목적처럼 분류하지 않는다.

prior_checkpoint의 사용자 목적은 기존 복원 범위·출처·잘림 여부가 확인되는 경우에만 사용한다. JSON 파싱 성공만으로 사용자 원문과의 일치를 증명했다고 표시하지 않는다. 기존 helper를 가산 타입 경계로 노출하더라도 목적 선택 규칙이나 note fingerprint 계산의 기존 의미는 변경하지 않는다.

완전성 판정은 구체적으로 제한한다. 해당 복원 범위 안의 실제 사용자 원문과 비교할 수 있으면 원문 길이와 일치를 확인한다. prior checkpoint만 남아 원문 완전성을 확인할 수 없으면 `objective_completeness_unknown`으로 core에 내려간다. 현재 activeObjective에는 원문 완전성을 증명하는 별도 필드가 없으므로 문자열 길이가 짧다는 이유만으로 완전하다고 인정하지 않는다.

현재 activeObjective는 최대 4000자이며 긴 문장의 중간을 생략할 수 있다. 자동 분류에서는 생략된 조각을 이어 붙여 의미를 판단하지 않는다. 원래 사용자 목적이 상한을 넘었거나 이미 생략됐으면 `objective_truncated`로 기록하고 특정 주요 주제를 정하지 않는다. 새 영구 저장 형식이나 checkpoint 파서를 추가해서 이 한계를 우회하지 않는다.

현재 continuity의 목적 판정은 문장 규칙이다. 모든 자연어의 목표 유지/교체를 보장하지 않는다. Auto는 이 책임을 확대하거나 기존 목적을 수정하지 않고, 모호할 때 생략/축소한다.

## 6. 주요 주제 규칙

첫 버전은 한국어·영어의 명확한 작업 표현만 다룬다. 고정된 유한 규칙으로 확인하며 점수/확률을 출력하지 않는다. 사용자가 언급한 주제와 작업 동사가 직접 연결될 때 특정 주제를 선택한다.

| 사용자 목적의 명확한 표현 예 | 주요 주제 |
|---|---|
| 멀티플레이 권한/RPC/복제/예측 구현·조사 | multiplayer |
| SSOT/SOLID, 상태 소유자, 시스템 의존성·구조 정리 | design |
| 타이머/이벤트 해제, 생성·종료, Dispose/EndPlay/OnDisable 수명 문제 | lifecycle |
| 컴파일 오류, 정의·헤더·API 계약 조사 | debugging |
| 줄간격·들여쓰기·포맷·가독성 정리 | code-style |
| 특정 주제를 확정하기 어려움 | core/proof만 우선 제공 |

이 표는 실제 API 위치나 프로젝트 구조를 증명하지 않는다. 규칙은 코드에서 주제 선택용으로만 사용한다.

- `multiplayer`라는 단어 하나, 파일명·경로, 패키지 설치, 연결된 도구, 클래스 이름만으로 선택하지 않는다.
- fenced code, Markdown 인용, 인라인 코드, 분명한 로그/인용 영역은 목적 분류에서 제외한다.
- 명확한 부정/제외 표현은 해당 주제의 긍정 일치보다 먼저 처리한다. 적용 범위가 불명확하면 특정 주제를 생략한다.
- 설명·비교 대상의 언급을 곧바로 구현 요구로 바꾸지 않는다.
- 작업 요청과 여러 주제가 충돌하면 임의의 전역 우선순위로 선택하지 않고 core로 낮춘다. 예외는 규칙에 명시한 직접 작업 표현이며 테스트로 고정한다.
- 명확한 참고 제외 요청을 인식한 경우 해당 입력에서는 생략하되 저장된 GUI 설정을 수정하지 않는다.
- 구조가 없는 붙여넣기나 복잡한 부정은 완벽하게 구분할 수 없다. 지원하지 않는 문장을 정교하게 해석하려고 분류기나 모델 호출을 추가하지 않는다.

첫 버전에서 인라인 코드는 모두 분류 대상에서 제외한다. 예를 들어 인라인 기술 명칭 뒤에 ‘이벤트 해제를 정리해 줘’라는 직접 요청이 있으면 바깥 문장의 lifecycle 표현으로 선택할 수 있다. 인라인 기술 명칭과 ‘고쳐 줘’만 남아 주제를 판단할 수 없는 요청은 core로 낮춘다. 기술 명칭 복원을 위해 별도의 코드 분석기를 붙이지 않으며 이 한계를 T07에 고정한다.

분류기는 최대 4000자, 고정된 규칙 목록, 제한된 일치 기록만 처리한다. 새 비용 상한은 선택기의 입력/기록 크기에만 적용하며 실행·읽기 횟수를 제한하지 않는다. 정규식의 중첩 반복과 입력 크기에 따라 늘어나는 외부 조회를 피한다.

## 7. 보조 주제 규칙

첫 버전의 보조 주제는 debugging 하나로 제한한다. 주요 주제가 debugging이면 중복 추가하지 않는다. 보조 주제는 각 일반 라운드에서 기존 snapshot으로 다시 계산하며 주요 주제를 교체하지 않는다.

| 현재 snapshot | 선택 처리 | 해석 한계 |
|---|---|---|
| 유효한 진단 존재/재등장 | debugging 참고 허용 | 경고 포함 가능. 실패나 같은 원인이라고 단정하지 않음 |
| 컴파일 요청 accepted/pending | verification 절 후보 | 요청 수락과 완료를 구분. 자동 polling 없음 |
| operation_outcome_unknown | verification 절 후보 | 결과 불명. 자동 재실행 없음 |
| 소스 변경만 관측 | 주요 주제 유지 | 모든 편집에 debugging을 추가하지 않음 |
| source_body_unavailable만 존재 | core의 가용성 설명 | 자동 파일 재읽기 없음 |
| Netcode 패키지만 관측 | 적용 조건만 제한 | multiplayer 작업으로 전환하지 않음 |

관측은 EvidenceManager가 확인한 동일 실행·프로젝트·provider·세션 범위만 사용한다. 원문 오류 문자열을 검색해서 원인이나 주제를 추정하지 않는다.

이미 더 최신 관측이 이전 진단/대기 상태를 대체한 경우 과거 신호가 보조 주제를 계속 유지하게 해서는 안 된다. 예를 들어 Unity의 같은 세션에서 compilationId가 바뀐 status가 반환됐는데 이전 compilation의 오류가 남으면, 이를 현재 오류로 취급하지 않는다. 불확실한 경우 보조 주제를 생략한다. 필요한 최소 최신성 정보는 기존 reference-context가 소유하며 Auto 안에 별도 시간순 원장이나 성공/실패 파서를 만들지 않는다.

현재 snapshot의 signals를 그대로 신선한 신호로 쓰지 않는다. reference-context가 기존 관측 상태에서 Auto용 `autoSignals` 가산 필드를 파생하도록 계획한다. 각 항목은 기존 signal, 소수의 기존 observation ID, `last_observation` 또는 `order_unknown` 상태만 제공한다. 다른 compile/run으로 대체된 항목은 제외하고, 순서 불명의 항목은 Auto 보조 선택에 사용하지 않는다. 상한은 기존 signal 종류 수와 관측 상한을 재사용한다. 새 원장·시간 TTL·영구 상태는 없다.

기존 snapshot.items/signals와 원문·수동 후보의 의미는 보존한다. 새 메타데이터를 외부 도구 결과나 모델 reference 본문에 자동 노출하지 않는다. Auto는 autoSignals가 없거나 불명확하면 보조 선택을 생략한다. 같은 batch의 복수 결과는 도착 순서를 최신 실행 순서로 간주하지 않는다.

`last_observation`은 외부 변경이 없다는 보장이 아니다. 현재 reference state에는 시간 TTL이나 도구 밖 변경 감지가 없고, 원문이 현재 입력에서 빠져도 제한된 관측 자료는 남을 수 있다. 원문 제거를 새 읽기나 현재 상태 확인으로 표시하지 않는다. 새로운 Unity compilationId 관측만으로 빌드 성공을 추론하지도 않는다.

Unity compilation accepted, compiler 관측, Player build를 구분한다. Unreal failed build, up-to-date 결과, 부분 진단도 실제 반환 의미를 유지한다. 임의의 로그를 compiler 관측으로 승격하지 않는다.

## 8. 절 조합과 감소 후보

Auto는 주요 주제 1개, 보조 주제 1개, 공통 절을 합친다. 기존 상한인 최대 6절·4후보를 유지한다. 기존 allowance 기본은 2048토큰이며 사용자 설정의 0~8192 범위를 따른다. Auto 자체가 allowance를 높이지 않는다.

주요 주제와 보조 주제를 합친 뒤 모든 신호를 동일한 가중치로 정렬하지 않는다. 먼저 적용 가능한 주요 pack을 고르고, 보조 pack은 그 다음에 넣는다. 공통 `core/proof`와 동반 절을 중복 없이 포함한다. 여유 절 수는 이미 선택한 주요/보조 주제 안의 관련 절을 추가하는 데만 사용한다. 세 번째 주제를 열지 않는다.

6절을 넘으면 관련 추가 pack부터 제외하고, 그래도 초과하면 보조 pack 전체를 제외한다. 주요 closure는 보존한다. 공통 필수 절을 포함한 주요 closure 자체가 6절에 들어가지 않거나 적용 조건을 충족하지 못하면 core로 내린다. 보조 closure를 중간에서 자르지 않으며 `supplement_omitted_section_limit`을 기록한다.

후보는 다음 순서로 만든다. 중복 후보는 제거한다.

1. 주요 pack + 보조 pack + 관련 추가 절 + 제한된 관측 자료.
2. 최소 주요 pack + 보조 pack + 최신성이 확인된 최소 관측 자료.
3. 최소 주요 pack만, 관측 자료 없음.
4. `core/proof`만. 이것도 맞지 않으면 기준 입력.

‘최소 pack’도 동반 절을 모두 포함한다. 크기·순환·적용 조건 문제로 주요 pack이 성립하지 않으면 보조 debugging만으로 대신하지 않고 core로 낮춘다. 축소 과정에서도 주요 작업을 버리고 진단만 남기지 않는다.

엔진·버전·패키지 필터는 현재 조합기를 재사용한다. scope가 ambiguous/available_tools면 공통 참고만 사용한다. 명시 mixed는 기존 엔진 문서 정책을 따르되 현재 관측 adapter가 mixed에서 엔진 관측을 수집하지 않는 한계를 우회하지 않는다. 복수 프로젝트·복수 framework의 자료를 결합해 적용 가능하다고 만들지 않는다.

Unreal build의 engineAssociation은 요청된 association이며 실제 설치/실행 patch 버전이 아니다. 현재 버전 관측 경로가 해당 UE 버전을 확인하지 못하면 UE 버전 제한 절은 제외한다. Auto 선택 정확도를 위해 새로운 엔진 탐색이나 버전 조회 도구를 실행하지 않는다.

본문·동반 절을 중간에서 잘라 계약을 부분 전달하지 않는다. 새로운 rule을 추가해도 모델의 필수 다음 도구를 지정하는 문장은 넣지 않는다.

## 9. 입력부터 실행까지의 연결

```text
명시 설정 + 기존 사용자 목적 + ToolScope
    → 실행별 읽기 전용 Auto intent
    → 기존 EvidenceManager의 현재 snapshot
    → 주제 ID와 선택 이유
    → 기존 절 조합기: 최대 4후보
    → ContextManager/BudgetBroker: 실제 입력 측정·채택 또는 생략
    → 기존 prediction/tool 실행 경계
```

연결 변경은 두 경계로 제한한다.

- prediction-loop 실행 시작: Auto의 effective delivery와 주요 주제를 준비. 관측 수집의 활성 조건에 명시 Auto를 포함.
- round-input 일반 라운드: scope, intent, 최신 snapshot으로 Auto 후보를 만든 뒤 기존 `addOptionalReferences`로 전달.

Off/Observe only/0 allowance에서는 intent·snapshot·선택·추가 측정을 호출하지 않는 기존 fast path를 유지한다. 자동 참고가 생략되더라도 execution phase를 바꾸지 않는다. 최종화·복구 라운드는 후보를 생성하기 전에 기존 단계 조건으로 제외한다.

reference instruction과 외부 파생 자료의 전달 역할도 유지한다. 외부 자료는 system 지시로 올리지 않고, 현재 assistant reference data 경로를 사용한다. 선택 reason code는 디버그 정보이며 ‘권한 구조 확인 완료’ 같은 모델 메모로 넣지 않는다.

## 10. 수명·취소·재진입

| 상황 | 계약 |
|---|---|
| 실행 시작 | 기존 목적에서 주요 주제 계산. 읽기 전용 로컬 값으로 보관 |
| 같은 실행의 다음 라운드 | 주요 주제 유지. 보조 주제와 가용성만 snapshot으로 재평가 |
| 압축 | 주요 주제를 assistant 요약에서 재분류하지 않음. 참고 문서/선택 결과는 checkpoint에 저장하지 않음 |
| 새로운 사용자 목적 | 기존 실행 경계에서 새 계산. 직전 보조 주제는 이월하지 않음 |
| 프로젝트/엔진 변경 | 새 ToolScope 실행에서 새 값 생성. 이전 관측/주제 캐시를 공유하지 않음 |
| Unity session/domain 변경 | 기존 근거 담당자의 무효화 사용. Auto가 이전 항목 복구하지 않음 |
| 파일 변경·충돌·부분 적용·rollback 불완전 | 기존 근거 유효성 사용. 패키지/버전 정보를 임의로 유지하지 않음 |
| 취소 | 기존 AbortSignal/guardAbort 전달. 참고 측정 실패로 취소를 삼키지 않음 |
| 재시도 | 기존 복구 phase를 따름. Auto가 재시도 횟수나 다음 도구를 추가하지 않음 |
| 정상 종료/실패/객체 폐기 | Auto용 타이머·구독·파일·영구 상태 없음. finally에서 새 작업 없음 |
| 같은 handler 재사용/병행 실행 | 서로 다른 실행의 intent·snapshot·선택 로그가 섞이지 않음 |

선택기가 예외를 내거나 catalogue 후보가 없으면 참고만 생략한다. 기존 기준 입력의 실패를 Auto 성공으로 바꾸지 않는다. 예상하지 못한 오류는 제한된 reason으로 기록하고 원래 취소는 전파한다.

## 11. 하네스가 판단을 가져가지 않게 하는 조건

Auto의 출력은 주제 ID·reason code·출처 식별자다. 다음 값은 출력 계약에 넣지 않는다.

- 원인/수정 방법/설계 점수/계약 준수 합격 여부.
- requiredNextTool, tool arguments, 승인 상태, 빌드·포맷·정적 검사 명령.
- objectiveSatisfied, 작업 완료, 새 실행 phase, retry/polling 계획.
- 프로젝트 소유자/권한/수명 관계를 확인했다는 사실.

‘진단이 반환됨 → 디버깅 참고 선택’까지가 역할이다. ‘진단이 반환됨 → 헤더를 교체해야 함’은 모델의 문제 해결 판단이다. 이 구분을 코드 타입과 handler 테스트로 확인한다.

추가 `.act()`/classifier LLM/embedding/RAG 조회는 없다. 기존 Hybrid 요약 호출과 복구 호출은 별도로 유지한다. 같은 관측·압축 사건 fixture에서 Auto를 켜도 새 호출 종류나 Auto 전용 summary 요청이 생기지 않아야 한다. 실제 모델의 출력이 달라지면 후속 작업량도 달라질 수 있으므로 모든 실사용 대화의 총 호출 수가 동일하다고 약속하지 않는다.

## 12. 로그와 사용자가 확인할 정보

기존 `design_guidance_input` event를 확장한다. 기존 필드 의미는 보존한다.

- configuredMode, configuredDelivery, effectiveDelivery.
- primaryTopic, supplementTopic, 제한된 selectionReasonCodes.
- objectiveFingerprint 또는 기존 실행 식별자, 선택에 쓰인 기존 관측 ID 소수.
- 기존 requestedIds/selectedIds/omittedIds, dataIds, addedTokens.
- 선택 성공과 실제 입력 채택 여부를 별도로 표현.

예: Auto / multiplayer / debugging / `user_topic_explicit`, `diagnostics_available` / 입력 증가 630 / included.

예산 초과·목표 불명·인용/부정 모호·관측 최신성 불명도 코드로 구분한다. 사용자 원문·전체 로그·민감한 경로를 새로 기록하지 않는다. 분류 확률이나 모델이 참고를 활용했다는 표시를 만들지 않는다. 기본 화면에 라운드마다 새 블록을 추가하지 않고 기존 debug 표시를 사용한다.

## 13. 예상 변경 파일과 변경 범위

모든 아래 경로는 `C:/Users/sster/Documents/Git/UE5_Local_LLM_MCP_lmstudio` 기준이다.

| 파일 | 계획된 변경 |
|---|---|
| `lmstudio-context-compactor-plugin/src/direct-config.ts` | Auto 항목과 effective delivery 설명 |
| `lmstudio-context-compactor-plugin/src/design-guidance.ts` | mode/topic 구분, 기존 조합 내부 재사용, 주요/보조 후보 지원 |
| `lmstudio-context-compactor-plugin/src/guidance-topic-selection.ts` (신규) | 출처가 있는 목적/관측의 제한된 순수 주제 선택 |
| `lmstudio-context-compactor-plugin/src/execution-config.ts` | 명시 Auto의 정규화와 설정 조합 읽기 |
| `lmstudio-context-compactor-plugin/src/execution-contracts.ts` | 필요한 가산 타입만 연결 |
| `lmstudio-context-compactor-plugin/src/context-ports.ts` | 기존 objective/이전 state helper의 작은 읽기 전용 타입 경계 |
| `lmstudio-context-compactor-plugin/src/prediction-loop.ts` | 실행별 intent, effective focused 활성 조건, 기존 반환 관측 수집 연결 |
| `lmstudio-context-compactor-plugin/src/round-input.ts` | Auto 분기, 기존 후보 admission과 로그 연결 |
| `lmstudio-context-compactor-plugin/src/reference-context.ts` | Auto용 autoSignals 가산 정보. 기존 관측 수명과 items/signals 의미 유지 |
| `lmstudio-context-compactor-plugin/test/auto-guidance.test.cjs` (신규) | 주제·인용/부정·보조·후보·수명 계약 |
| 기존 guidance/reference-context/prediction-loop 테스트 | manual 호환과 실제 입력/승인/호출 회귀 |
| `lmstudio-context-compactor-plugin/package.json` | 명시 테스트 목록에 신규 파일 포함 |
| `scripts/build_integrated_package.py`, `tests/test_integrated_package.py` | 신규 소스·테스트의 설치 패키지 포함 |
| `lmstudio-context-compactor-plugin/scripts/status.cjs`, `test/status.test.cjs` | 신규 소스를 DIRECT_SOURCE_FILES에 등록. 소스 디렉터리와 목록의 일치 확인 |
| `lmstudio-context-compactor-plugin/README.md` | 설정 행렬·한계·수동 복귀 방법 |

ContextManager/BudgetBroker/tool-boundary/semantic-handoff의 알고리즘 변경은 예상하지 않는다. 변경 필요성이 발견되면 해당 근거를 기록하고 Auto 선택기의 책임 확대를 먼저 반박한다. catalogue의 주제·본문·revision·생성기는 이번 기능에 필요한 근거가 없으면 변경하지 않는다. Auto는 콘텐츠 주제가 아니라 선택 모드이므로 catalogue TOPICS에 auto를 추가하지 않는다.

Unity/Unreal producer나 Bridge, 게임 프로젝트 코드를 바꾸는 계획은 아니다. adapter가 부족하면 원문 계약을 바꾸기보다 Auto의 선택을 보수적으로 낮춘다.

## 14. 구현 순서

### P0 — 호환과 책임 경계 고정

- 기준 manual 후보/설정/정확 측정/승인/복구 fixture를 고정한다.
- 현재 목표 helper와 Unity/Unreal 최신성 사례를 확인한다.
- 기존 모든 조합의 effective delivery와 Auto 예외를 명문화한다.
- 종료 조건: 예상 변경 범위와 금지 출력이 테스트 입력/기대 결과로 구체화됨.

### P1 — 설정과 순수 주제 선택

- 명시 auto 값, mode/topic 분리, effective delivery 계산을 넣는다.
- 기존 objective helper의 읽기 전용 결과로 주요 주제를 정한다.
- 한국어·영어 직접 요청, 인용·부정·상한·모호함의 유한 규칙을 구현한다.
- 종료 조건: 새 모듈에 I/O/모델/실행 제어 의존성 없음. Off/manual 호환 통과.

### P2 — 주요/보조 절 조합

- 기존 applicability/closure/렌더링을 공통 사용한다.
- 주요 주제 보존, 보조 debugging, 최대 6절/4후보를 구현한다.
- 기존 근거 소유자가 autoSignals를 파생하고 Auto는 그 가산 정보로만 보조 주제를 선택한다.
- 종료 조건: 진단이 주요 주제를 밀어내지 않으며 정상 입력에 실제 관련 절이 포함됨.

### P3 — 실제 실행 연결과 수명

- 실행 시작/일반 라운드의 두 경계에만 연결한다.
- 기존 ContextManager로 전달하고 취소·Off·Observe only·복구·최종화 생략을 확인한다.
- 종료 조건: handler fixture에서 Auto를 통해 측정된 입력이 실제 dispatch되고 도구 권한이 유지됨.

### P4 — 패키지·문서·회귀

- 명시 테스트 목록, 설치 패키지 필수 파일, README를 갱신한다.
- 설치 상태 검사 DIRECT_SOURCE_FILES 목록에도 신규 모듈을 등록한다. 현재 status 테스트는 src의 ts/js 파일 전체와 목록의 정확한 일치를 확인한다.
- 깨끗한 package 재생성으로 새 소스/테스트 누락을 확인한다.
- 기존 CI의 Context Compactor job은 npm test를 실행하므로 신규 테스트 포함을 확인한다. 별도 Auto 전용 CI 플랫폼을 만들지 않는다.
- 종료 조건: local build/관련 테스트/전체 plugin 회귀/통합 package 검사 통과. 실제 GitHub CI 결과는 실행 확인한 경우에만 기록.

### P5 — 실제 사용 확인과 복귀

- 구현 후 사용자가 GUI에서 Auto를 선택할 때 작고 관련된 실제 사례를 확인한다.
- 설정/모델/로드 context/동일 소스 상태를 기록하고 선택 주제·자료·추론 비용·반복/의미 변경을 관찰한다.
- 처음부터 대규모 자동 평가나 대량 게임 빌드를 요구하지 않는다.
- 문제 발생 시 GUI에서 Off 또는 기존 수동 주제로 복귀. 원문 근거·메모리·프로젝트 파일을 삭제하지 않는다.
- 종료 조건: 실제 입력 포함 확인과 모델의 행동 결과를 따로 기록. 모델 품질 개선의 크기는 아직 미확정.

이번 요청에서는 계획 문서와 근거만 작성한다. P0 이후의 구현은 아직 진행하지 않았다.

## 15. 검증 사례

| ID | 사례 | 확인할 결과 |
|---|---|---|
| T01 | 설정 누락/알 수 없는 mode | Off 유지, 자동 활성 없음 |
| T02 | 기존 수동 6주제 × documents/focused | 기존 후보·본문·선택 순서·delivery 유지 |
| T03 | Auto × 저장된 documents/focused | effective focused, 저장 값 수정 없음 |
| T04 | allowance=0, Observe only, Off | intent/snapshot/관측 수집/추가 측정 생략 |
| T05 | 한국어/영어 직접 주제 요청 | 의도한 주요 주제의 실제 절 포함 |
| T06 | 단독 키워드·파일명·경로·패키지명 | 특정 주요 주제의 근거가 되지 않음 |
| T07 | fenced/인용/인라인 코드 속 지시·기술 명칭 | 인용 속 지시는 목표로 분류하지 않음. 바깥 직접 문장으로 선택 가능하며 인라인 명칭+모호한 수정 요청만 있으면 core |
| T08 | 부정·제외·비교·복수 주제 충돌 | 특정 선택을 낮추고 reason 기록 |
| T09 | 목적 상한 초과/이미 생략된 문맥 | 잘린 구문으로 분류하지 않음 |
| T10 | 짧은 계속 요청 + raw/압축 대화의 확인된 antecedent | 기존 helper/이전 state 추출을 따라 주요 주제 유지 |
| T11 | 계속 요청 + 목적 출처/원문 완전성 불명 | assistant 요약으로 대체하지 않고 objective_completeness_unknown/core |
| T12 | 새 사용자 목적/새 프로젝트/같은 handler | 이전 주요/보조/엔진 자료 누수 없음 |
| T13 | multiplayer 작업 중 Unreal 빌드 진단 | multiplayer 유지 + debugging 보조 |
| T14 | design 작업 중 진단 | design 유지 + debugging 보조 |
| T15 | debugging 작업 중 진단 | 주제 중복 없음 |
| T16 | 경고만 포함한 성공 결과 | failure reason/원인 확정 없음 |
| T17 | Unity accepted/pending | verification 참고, 완료·Player build 성공 주장 없음 |
| T18 | outcome_unknown/transport 문제 | 참고만 선택, 자동 retry/polling 없음 |
| T19 | 더 최신 동일 target 빌드 결과 | 과거 진단을 현재 진단으로 유지하지 않음 |
| T20 | Unity status의 새 compilationId, 이전 오류 | 현재 오류로 취급하지 않음; 불명확하면 보조 생략 |
| T21 | 같은 batch의 복수 run/status/세션 | 도착 순서로 최신 상태 확정하지 않음 |
| T22 | session/domain 변경, provider 중복, 다른 프로젝트 | 기존 근거 담당자가 제외한 자료를 재사용하지 않음 |
| T23 | 패키지 삭제/변경/부분 lock/복수 framework | 특정 framework 문서 적용 보수적으로 유지 |
| T24 | 엔진 ambiguous/tool-only/mixed, UE association만 존재 | 기존 scope 정책 유지, 프로젝트 자료/UE 실제 버전을 임의 확정하지 않음 |
| T25 | source 변경/본문 불가만 존재 | 자동 debugging 전환·추가 읽기 없음 |
| T26 | 동반 절 누락·순환·6절 초과·버전 부적합 | 완결된 pack만 전달, 주요 실패를 보조 단독으로 대체하지 않음 |
| T27 | 모든 Auto 감소 후보 및 6절 초과 | 추가 pack→보조 전체 순서로 제외, 주요 closure 보존, 세 번째 주제 없음, 4후보 상한 |
| T28 | 낮은 allowance/예산 부족/부정확 측정/조립 실패 | 기준 입력·출력 예약·기존 근거 유지 |
| T29 | 측정 도중 취소 | 취소 전파, 추가 prediction 없음 |
| T30 | 압축 및 재조립 | 선택 이유·참고가 checkpoint/note/system 외부 자료로 영속되지 않음 |
| T31 | research/tool-planning/reasoning recovery/최종 보고 | 기존 focused 생략 정책 유지 |
| T32 | bounded audit 일반 라운드 | 참고 포함 가능, 쓰기 승인/도구 범위 불변 |
| T33 | 정상 handler의 모델 도구 요청 | 기존 tool catalogue·인수 binding·승인 정책 유지 |
| T34 | 동일 관측·압축 사건의 호출 추적 | Auto 전용 act/classifier/도구 호출 없음; 기존 Hybrid 요약 별도 구분 |
| T35 | 병행 실행·handler 재사용·종료/실패 | 전역 state/timer/subscription 누수 없음 |
| T36 | SDK 최종 입력·reference data·telemetry | 선택과 실제 채택을 구분, 원문 이력 변조 없음 |
| T37 | Hybrid/Deterministic/Legacy | 기존 작업 모드 정책과 정확 측정 조건 유지 |
| T38 | 이전 note/result/schema 및 rollback 버전 | 새 저장 형식 요구 없음, 이전 mode 정규화 호환 |
| T39 | 설치 package만으로 build/test/status | allowlist·필수 파일·status 목록에 새 모듈/테스트 포함, repo 밖 프로젝트 의존 없음 |
| T40 | 단순 실제 사례의 입력/행동 관찰 | 문서 포함과 코드 품질 개선을 구분해서 기록 |

형식 검사는 이번 계획/구현 산출물에 대한 개발 검증이다. 모델 사용 중 필수 static Validate로 추가하지 않는다. 새 사용자 기능을 검증하는 사례 위주로 작성하며 내부 정규식을 그대로 복사한 테스트는 피한다.

## 16. 주요 위험과 대응

| 위험 | 대응 |
|---|---|
| 자연어 규칙의 잘못된 주제 선택 | 명확한 직접 표현만 처리, 모호함은 core/생략, 수동 선택 제공 |
| 진단이 원래 작업을 밀어냄 | 실행별 주요 주제 유지, 보조 역할 구분, 감소 후보에서도 주요 보존 |
| 과거 관측이 보조 주제를 고정 | 기존 근거 담당자에서 최신성 확인, 불확실하면 생략 |
| Auto/default documents 조합 혼란 | GUI/README/telemetry에 effective focused 명시, 저장 값 변경 없음 |
| 소스/엔진 버전 추정이 사실로 승격 | 기존 provenance/applicability 필터 사용, 모르는 값은 모름 |
| 참고 비용으로 예산·출력이 압박 | 기존 실제 측정 admission, 맞지 않으면 기준 입력 |
| 설계 조언이 작업 절차/승인으로 확장 | 출력 타입 제한, 실행 경계 의존성 금지, handler 회귀 |
| 요약기의 추가 추론과 Auto 비용 혼동 | 기존 Hybrid 요약을 별도 기록, Auto 호출 증가를 fixture로 확인 |
| 지나치게 많은 추상화/설정 | 새 순수 모듈 하나, 기존 조합/예산/근거 담당자 재사용 |
| 테스트는 통과하지만 실제 참고가 안 들어감 | 정상 사례의 본문·출처·실제 dispatch 긍정 검증 |

## 17. 아직 확정할 수 없는 것

- 현재 자연어 규칙이 사용자의 모든 표현을 정확히 분류하는지.
- 특정 모델에서 작은 core와 주요/보조 pack의 가장 좋은 분량.
- 실제 LM Studio host에서 참고를 활용한 정도와 모델 출력의 품질 개선 폭.
- 기존 목적/관측 경계가 실제 사용자 런타임에서 모두 기대대로 제공되는지.

이 불확실성을 해결하려고 Auto에 추가 모델, 자체 문제 해결 루프, 의무 정적 검사, 자동 빌드를 붙이지 않는다. 선택 기능이 소유한 입력 품질과 한계를 먼저 확인한다.

독립 검토: `auto_reference_plan_review`가 현재 소스와 요청만으로 읽기 전용 검토했다. Auto/parser 조합, 목적 출처/이전 state, mixed 관측 범위, 진단의 현재성, UE 버전 불명, 최종 통합 후보 상한, 기존 Hybrid 추론, 설치 allowlist/status 등록을 확인하고 계획에 반영했다. 초안 재검토에서 prior 목적의 완전성, 6절 초과 시 제외 순서, 주제 확장 범위, 인라인 기술 명칭의 한계도 구체화했다. 구현 또는 실제 모델 동작을 검증한 결과는 아니다.

근거 기록: [auto-design-references-plan-20261001.json](evidence/auto-design-references-plan-20261001.json).

2026-10-03 후속: 사용자 요청으로 Auto와 사건 개선 경로를 구현했다. 위 계획의 불확실성은 계속 적용되며 현재 구현 및 검사 범위는 [구현 기록](Model_Behavior_Implementation_20261003.md)에 남긴다. 기본 Off, 추가 모델·도구 호출 없음, 참고의 선택적 예산 처리를 유지한다.
