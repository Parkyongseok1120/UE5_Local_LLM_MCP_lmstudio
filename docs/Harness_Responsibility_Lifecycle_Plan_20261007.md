# 하네스 책임·경계·분할·수명 관리 개선 계획

기준일: 2026-10-07 / 기준 커밋: `78abd7fee5a1172b7bdb4d5e99c0dc3f51afbef8` (RC2)

상태: 사용자 승인 후 계획의 저장소 구현과 자동 검증을 진행했다. 1~12절은 기준 커밋에서 조사한 문제와 구현 계획이며, 수정 결과·검증·남은 범위는 **13절**에 기록한다. 설치본과 게임 프로젝트는 변경하지 않았다. 이 문서는 저장소 검토용이며 모델 상시 주입 대상이 아니다.

## 1. 판단과 구현 범위

기존 구조를 유지하면서 개선할 수 있다. 이미 실행, 도구 권한, 예산, 근거, 판단 메모, provider 작업이 분리되어 있다. 문제는 그 사이에서 서로 다른 사실을 같은 값으로 취급하거나, 필요한 사실이 소비자까지 전달되지 않는 경계에 집중되어 있다.

우선순위는 다음과 같다.

1. 종료된 라운드가 다시 변경되는 경계와 잘못된 프로젝트 설정의 출처 혼합을 바로잡는다.
2. 도구의 결과 크기·추출 수준·거부 이유를 실제 계약에 맞춘다.
3. 프로젝트/엔진 관측의 출처와 무효화 조건을 보완한다.
4. Auto의 요청 인식과 엔진 참고 선택을 함께 고친다.
5. 기존 짧은 지침과 근거 표현을 보강하고 실제 모델 행동은 별도로 평가한다.

추가 판단 모델, 서브에이전트 실행기, 전역 작업 원장, 모델의 코드 품질을 자동 판정하는 게이트는 필요하지 않다. 기존 Hybrid의 조건부 요약 호출은 이미 있는 기능이다. 이번 계획에서 추가 모델 호출을 만들지 않는다.

## 2. 조사 범위와 확인 수준

활성 경로의 `prediction-loop → round-input → tool-boundary → SDK/provider → returned evidence → reference/working context → next input/final delivery`를 추적했다. Unreal Direct 읽기·변경 경계, Python RAG의 프로젝트/작업 경계, Unity Node/Editor의 버전·세션·취소 경계, evidence-first 설치/검증 계약을 함께 확인했다.

저장소의 모든 소스 줄, 모든 Legacy 경로, 실제 Editor/LM Studio 실행을 전부 검증한 것은 아니다. 아래 재현은 순수 함수와 모의 콜백의 자동 확인이며 실제 모델 품질 또는 실제 transport 발생 빈도의 증명이 아니다.

- 기존 Auto/Focused/Design/Reference/Continuity/Architecture 테스트: **69/69 통과**.
- 독립 검토의 Reference/ToolScope 테스트: **35/35 통과**. 앞의 69개와 일부 중복되므로 합산하지 않는다.
- 생성 지침 번들 `--check`: 통과.
- 신규 조사 재현: API 요청 분류 누락, 엔진 절 누락, 종료 후 콜백, 혼합 배치 상태, held 예약 경쟁, 잘못된 config 출처, 엔진 관측 무효화, Unity 버전 충돌.
- 조사 파일: `artifacts/architecture-plan-20261007/`. 개인 경로와 로컬 실행 자료가 있어 Git 배포 대상에서 제외한다.
- 검토 가능한 근거 기록: [architecture-lifecycle-plan-20261007.json](evidence/architecture-lifecycle-plan-20261007.json). 형식 검사는 소스의 진실이나 전체 작업 완료를 인증하지 않는다.

## 3. 현재 책임과 유지할 소유자

| 상태/판단 | 현재 소유자 | 수명 | 변경 방향 |
| --- | --- | --- | --- |
| 사용자 목표와 이어서 하기 | continuity-objectives | 현재 목표/대화 | 다른 모듈에서 목적을 재해석하지 않음 |
| 공유 Unreal activeProject | Python project_controller | 공유 설정의 명시적 선택 | compactor에 별도 전역 선택 상태를 만들지 않음 |
| 실행 도구·프로젝트 인자 | ToolScope + ToolBoundary | 실행별 고정 범위 | 설정·워크스페이스 출처 혼합 제거 |
| 관측된 프로젝트·엔진·패키지 | EvidenceManager + reference-context | 실행 내 제한된 관측 | 실행 권한과 독립된 참고용 view 제공 |
| 라운드 signal·SDK 콜백 | runOneToolRound | 한 act 호출 | 종료 시 콜백과 반환 snapshot 확정 |
| 완료된 planning/반환 결과 채택 | RoundTransaction | 한 라운드의 commit | 미수신 결과와 미완료 문장을 완료로 승격하지 않음 |
| 배치 예약·정산 | BudgetBroker / BatchReservation | 한 모델 라운드 | 실패 이유·회계 단위 명확화 |
| 정확한 입력 fit·선택적 지침 채택 | ContextManager / round-input | 최종 SDK 입력 | 실제 tokenizer 측정과 기존 fallback 유지 |
| provider 반환량·페이지·검색 범위 | 각 read capability + serializer | 요청별 | 입력에 맞출 수 있는 공개 크기 계약 보강 |
| 요청/결과 pairing | tool-exchange-index | 메시지 구간 | 기존 공통 matcher 재사용 |
| 기록 근거와 읽기 범위 | WorkingContext / EvidenceArchive / input-availability | 현재 입력, window, 제한된 archive | 역사적 자료와 현재 읽기 사실을 구분 |
| 이전 판단·기각·질문 | continuity-model-notes | 목표·프로젝트에 묶인 짧은 note | 별도 메모 DB를 추가하지 않음 |
| 참고 문서 내용 | docs/model-guidance + catalog | 버전 관리/빌드 | Markdown 정본, 생성 번들은 파생물 |
| Unreal 파일 적용·복구 | mutation/version/transaction 모듈 | 작업/파일/transaction | 기존 atomic commit·receipt 보존 |
| RAG refresh 게시·취소 | RequestInbox / RagOperation / generation swap | 요청/게시 transaction | 게시 전 취소와 게시 완료 후 정리 구분 유지 |
| Unity Editor 작업·관측 | Bridge / Operations / EvidenceStore | Editor session/domain/operation | Node의 응답 대기 취소와 Editor 작업 취소 구분 |
| 근거 패킷 형식 검사 | evidence-first MCP | 호출별 stateless | 의미 판정·프로젝트 권한·실행 승인 역할을 부여하지 않음 |

SSOT는 위 상태들을 하나의 거대 저장소에 합치는 뜻이 아니다. 같은 사실의 출처·식별자·무효화 규칙을 공유하고, 각 소비자는 필요한 view를 만든다. 서로 다른 사실인 설정 버전, 실행 Editor 버전, 설치 소스 버전은 별도 값이어야 한다.

## 4. 수정 전 확인된 문제 — 기준 커밋의 조사 기록

### F01. 명시적인 API 확인 요청이 Auto에서 공통 주제로 분류된다

**P2 · Bug · TestVerified(순수 함수)**

다음 다섯 문장이 모두 `core / no_explicit_topic`으로 나왔다.

- “언리얼 API 제대로 확인해서 위의 계획대로 다시 해봐”
- “UPROPERTY와 UFUNCTION 사용이 맞는지 확인해”
- “FORCEINLINE 필요한 함수인지 검토해”
- “델리게이트 바인딩과 해제를 확인해”
- “Enhanced Input API 구현해”

근거: `guidance-topic-selection.ts:6–13`, `:16–47`. 해당 selector는 도구·모델·파일 IO 없이 유한한 사용자 문장만 처리한다. 로그의 API 재확인 turn에서도 네 라운드가 `no_explicit_topic`이었다.

반대 근거: core/proof에도 API 확인 원칙은 있다. 전문 지침 누락만으로 모델 오판 전체를 설명할 수 없다. 인용·로그·경로·복잡한 부정문을 보수적으로 처리하는 기존 정책은 유용하다.

최소 변경: 기존 selector에서 명시적인 구현/검토 동사와 API·include·매크로·델리게이트·입력 관련 표현을 함께 인식한다. 단어 하나가 등장했다는 이유로 주제를 선택하지 않는다. 설명 요청·인용문·도구 출력은 계속 제외한다.

### F02. 올바른 주제가 선택되어도 엔진 절이 후보에서 빠진다

**P2 · Bug · TestVerified(순수 함수)**

`engine=unreal, source=config, primary=debugging`에서도 가장 큰 Auto 후보는 `core/proof + debugging/definitions + debugging/preserve-contract + debugging/verification`이었다. `unreal/api`는 어떤 축소 후보에도 없었다. Unity debugging도 같은 방식으로 엔진 절이 빠진다. design의 기본 후보 역시 공통 design 절이 자리를 차지한다.

근거: `design-guidance.ts:128–155`. 동일 priority에서 ID 정렬 후 최대 두 개의 추가 pack만 합치므로, 관련 엔진 pack이 후보 생성 단계에서 밀린다. 정확한 토큰 예산 채택에 도달하기 전의 문제다.

반대 근거: lifecycle 또는 진단 signal 조합은 다른 절을 선택할 수 있다. 모든 엔진 문서가 항상 누락되는 것은 아니다.

최소 변경: 단순한 숫자 priority 조정 대신 선택 역할을 구분한다. `core/proof`, 주제의 공통 확인 절, 적용 가능한 엔진 절 하나를 먼저 구성하고 명시된 보조 주제/현재 진단을 기존 상한 안에서 반영한다. 최대 6절·4후보·정확 토큰 예산을 유지한다. 엔진 절이 빠졌다면 `engine_pack_unavailable/omitted_*`와 같이 원인을 관측할 수 있게 한다.

### F03. 실행 범위와 참고 관측 범위가 같은 객체에 의존한다

**확인된 구조 제약 · SourceVerified 및 순수 함수 재현**

ToolScope는 실행 시작에 한 번 결정되고 도구 필터·인자 결속·지침에 함께 사용된다. `available_tools`/`ambiguous`면 reference-context는 결과 정규화 전에 수집을 건너뛴다. 정규화에도 `get_active_project`/`unreal_get_active_project`의 프로젝트 관측을 만드는 경로가 없다.

근거: `prediction-loop.ts:82–102,639`, `reference-context.ts:128–243,273`, `design-guidance.ts:99`. 실제 프로젝트 조회가 성공해도 실행 중 지침의 엔진 근거가 생기지 않는 조건이 있다.

반대 근거: 확인되지 않은 도구 결과로 실행 권한을 확대하지 않는 정책은 유지해야 한다. 현재 fail-closed 동작 자체를 제거하면 안 된다.

최소 변경: 기존 EvidenceManager에서 신뢰된 provider·유일한 request/result pairing으로 얻은 **참고용 프로젝트 관측**을 관리한다. 실행 권한용 ToolScope는 불변으로 유지하고 selector에는 읽기 전용 관측 view만 전달한다. 일반 파일 내용·모델 문장·요청 인자는 프로젝트 확인으로 승격하지 않는다.

충돌은 같은 종류의 사실끼리 비교한다. `shared_default_selection`, `requested_call_target`, `observed_result_target`, `observed_editor_project`를 구분한다. 공유 기본 선택 B와 명시 호출 대상 A는 정상 조합일 수 있다. A를 대상으로 실행한 도구가 실제 대상을 B로 반환했을 때 해당 호출의 충돌을 기록하고 엔진 전용 적용을 보류한다. 공유 기본값 B를 읽었다는 이유만으로 A의 근거를 내리지 않는다.

Node `get_active_project`의 성공은 공유 설정 값과 파생 경로를 받았다는 뜻이며 실제 Editor 프로젝트·설치 엔진·파일 존재까지 증명하지 않는다(`direct-project-capabilities.js:50–54`). RAG의 `projectBindingStatus`도 bound/stale/unbound를 그대로 보존한다. 참고 적용에 필요한 근거 수준에 못 미치면 관측 사실만 표시하고 보류한다. 외부 전환을 관측하지 못했으면 “마지막 관측”으로만 표현한다. 자동 탐색 호출이나 권한 변경은 추가하지 않는다.

명시 mixed scope는 도구를 둘 다 노출하지만 현재 reference 수집은 engine equality 때문에 이를 수용하지 않는다. 첫 구현에서는 이 제한을 telemetry에 명확히 표시하고 공통 참고로 축소한다. 두 엔진의 버전을 단일 applicability에 합치지 않는다. mixed 관측을 완전히 지원하려면 engine/project별 view가 필요하며 별도 후속 범위로 둔다.

### F04. 잘못된 config 경로가 workspace에서 확인된 identity처럼 남는다

**P2 · Bug · TestVerified(기존 workspace 읽기)**

존재하지 않는 `configuredProjectIdentity`와 정상 Unreal workspace를 주면 반환값의 `source`는 `workspace`지만 `projectIdentity`는 잘못된 config 경로다. `configuredIdentity || workspace.projectIdentity`가 원인이다. 이후 bindProjectArguments가 그 identity를 도구 인자에 강제한다.

근거: `tool-scope.ts:145–171,217–233`.

반대 근거: provider가 잘못된 프로젝트를 다시 거부할 수 있으므로 실제 잘못된 파일 수정까지 증명된 것은 아니다. 명시 engine 설정의 선택은 별도 계약이다.

최소 변경: 명시된 config가 유효하지 않다면 출처와 오류 상태를 그대로 유지한다. 정상 workspace의 증명을 빌려 쓰지 않는다. 사용자 명시 대상을 다른 workspace로 조용히 바꾸는 fallback도 하지 않는다. 진단 가능한 unverified/conflict 상태와 필요한 기존 경계의 거부를 사용한다.

### F05. 엔진 관측의 출처와 무효화가 충분히 구분되지 않는다

**P2 · Bug · TestVerified(모의 관측)**

- Unreal: engine_local에서 5.7.4를 확인한 뒤 활성 `.uproject` 수정 성공을 관측해도 `applicability.engineVersion=5.7.4`가 남는다. 파일 관측 무효화와 엔진 결속의 의존 관계가 연결되지 않았다. 수정이 실제로 EngineAssociation을 바꿨는지 알 수 없으므로 새 버전을 추정할 수 없으며, applicability를 재확인 대상으로 내려야 한다.
- Unity: 연결된 Editor의 6000.0.44f1 뒤에 ProjectVersion.txt의 6000.1.0f1을 읽으면 단일 engineVersion이 파일 값으로 바뀐다. 연결 끊김 뒤에도 파일 값이 남는다. 디스크 설정/마지막 저장 버전과 실행 Editor 버전이 같은 슬롯에서 최신 순으로 경쟁한다.

근거: `reference-context.ts:100–105,137–149,194–198,234–240,255–257,301–307,386–387`; `.uproject` 변경 허용은 `direct-mutation-scope.js:55`.

반대 근거: Unity session/domain 변경 및 같은 배치의 모호한 관측을 제한하는 기존 처리는 있다. 수명 관리 전체가 없는 것은 아니다. 과거 엔진 소스를 읽었다는 사실도 지워서는 안 된다.

최소 변경: `configured/last_saved`, `observed_runtime`, `observed_engine_source`, `index_target` 의미를 구분한다. 관측마다 provider·project·session/engine identity·근거 버전을 유지한다. 관련 descriptor/설정 수정, binding 실패, 충돌 관측은 applicability를 무효화한다. 과거 읽기 사실은 역사적 근거로 남긴다. Unity 파일 기준 지침과 runtime 기준 지침은 필요한 버전 종류를 명시한다.

### F06. 읽기 provider의 결과 크기와 예약 계약 사이에 간격이 있다

**확인된 계약 간격 · SourceVerified / 예약 경쟁 재현**

Unreal `read_file_range`, `read_symbol`, `search_files`는 호출별 반환 envelope 바이트 상한을 공개하지 않는다. 브로커는 조정 필드가 없는 읽기에 고정 4096을 예약한다. provider는 별도 전체 응답 상한을 사용하므로 출력량을 그 예약에 맞추기 어렵다.

근거: `direct-tool-catalog.js:179–220`, `direct-read-capabilities.js:101–166,221–343`, `direct-runtime-context.js:36–42,99–104`, `budget-broker.ts:99–157`.

Unity의 `byteBudget`·전체 줄 단위 페이지·nextStartLine 계약은 비교 가능한 기존 구현이다. 새 공통 IO 프레임워크를 만들 이유는 없다.

**이전 설명 정정:** SDK 1.5.0은 병렬 도구 결과를 모아 onMessage로 보낸다. 브로커 receive는 그 이후다. 기록된 overrun 10건이 guard 거부 6건을 직접 일으켰다고 단정하지 않는다. capacity 29997에서 read_file 세 개가 각각 9216을 held로 예약하면 남은 2349로 4096 요청을 받을 수 없다. 이후 실제 결과가 작게 도착해 여유가 생기는 것은 보수적 예약의 정상 결과다.

최소 변경:

- 기존 Unreal 도구에 선택적 envelope `byteBudget`을 추가하고 결과·metadata·escaping을 포함한 실제 UTF-8 직렬화 크기를 provider가 책임진다. 기존 `maxBytes`의 원문 읽기 의미를 바꾸지 않는다.
- 줄/검색 행 경계, partial/coverage, 다음 읽을 범위, 원본 hash를 보존한다. 범위가 너무 크면 조용히 완전한 읽기처럼 반환하지 않는다.
- 브로커는 공개된 계약을 소비하고 실패 이유를 반환한다. ToolBoundary에서 회계식을 복제하지 않는다.
- held 부족, 개별 예약 초과, batch 종료, 중복 예약, 실제 입력 fit 실패를 구분한다. 회계상 보수적 allowance와 정확한 모델 token 수를 같은 의미로 표시하지 않는다.
- schema에 없는 인자를 구 provider에 주입하지 않는다. provider가 지원하지 않으면 보수적 legacy 경로를 유지한다.
- byteBudget이 제한하는 표현 단위를 명시한다. provider payload와 MCP/SDK 포장 비용을 중복 계산하거나 누락하지 않도록 serializer까지 대조한다. 정확한 다음 모델 입력 크기는 여전히 ContextManager에서 재측정한다.

### F07. read_symbol의 추출 수준이 정의 확인처럼 보일 수 있다

**P2 · Bug(계약 표현) · SourceVerified**

`read_symbol`은 C++ AST 또는 엔진 선언 resolver가 아니다. 정규식의 첫 함수 형태 매치와 brace scan으로 본문을 추출한다. 주석·호출·overload 모호성에 대한 증명 수준이 반환에 없다. 도구 설명은 “one C/C++ function body”라고 한다.

근거: `direct-read-capabilities.js:289–343`, `direct-tool-catalog.js:213–220`.

최소 변경: 기존 도구 결과에 lexical candidate라는 추출 방법과 부분/모호 상태를 표시한다. 일치 실패를 API 부재로 보고하지 않는다. 정확한 설치 엔진 계약 확인에는 이미 있는 `unreal_symbol_lookup(sourceMode=engine_local)`와 bounded header evidence를 활용하도록 설명한다. 후보를 선언으로 승격하는 parser 확장은 별도 범위로 둔다.

### F08. 닫힌 라운드에 늦은 콜백이 들어올 수 있다

**P2 · Bug · TestVerified(모의 콜백); 실제 host 발생은 미확인**

act가 throw한 뒤 runOneToolRound가 반환해도 자체 signal은 abort되지 않았고 저장된 onMessage를 호출하면 반환된 messages 배열이 0→1로 바뀌며 외부 onMessageCaptured도 실행됐다.

근거: `round-loop.ts:86–117,215–230,235–267`. 설치 SDK의 tool implementation 오류는 act 조기 reject 경로에 연결되고, 모든 peer 결과는 batch가 끝나야 전달되는 경로가 있다. 따라서 provider에서 실행된 결과 전부를 하네스가 항상 수신한다고 보장할 수 없다.

최소 변경: runOneToolRound가 callback 유효 기간과 자체 signal 정리를 소유한다. 종료는 idempotent하며 fence를 먼저 닫고 필요하면 abort하고 listener를 제거한다. 반환은 messages/statistics snapshot으로 확정한다. 늦은 콜백은 UI·budget·planning·현재 evidence를 변경하지 못한다. 실제로 전달된 결과 채택은 기존 RoundTransaction에 둔다.

SDK가 전달하지 않은 mutation 결과는 unknown이며 자동 재실행하지 않는다. 과거 결과를 별도 회수하는 SDK adapter 변경은 실제 transport 재현 후 별도 결정한다. 정상 tool handoff와 사용자 취소의 이유를 cleanup abort 때문에 바꾸지 않도록 시험한다.

### F09. 혼합 배치의 대표 상태가 실행 사실을 단순화한다

**P3 · Bug(진단 표현) · TestVerified**

한 호출은 허용·실행되고 다른 호출은 거부된 입력에도 `guard_denied_before_provider_execution`이 배치 전체 상태로 나온다. per-call trace와 counters는 남아 있지만 대표 상태를 읽으면 모두 미실행인 것처럼 오해할 수 있다.

근거: `tool-boundary.ts:57–85`.

최소 변경: 기존 per-call trace를 정본으로 삼고 배치는 mixed 또는 상태별 count를 파생한다. guard 허용, provider 실행 관측, SDK가 만든 거부 결과, provider 반환을 구분한다. 알 수 없는 dispatch 수를 허용 수로 확정하지 않는다.

### F10. provider 카탈로그와 하네스의 읽기 분류가 다르다

**확인된 계약 차이 · SourceVerified 및 순수 함수 재현**

RAG 카탈로그는 8개 도구 중 set_active_project/refresh 두 개를 mutation으로 분류한다. compactor는 RAG의 symbol_lookup/search만 read로 인정한다. 프로젝트/status/health/capabilities 조회는 일반 approval_required로 분류되고 read recovery에서도 빠진다.

근거: `scripts/direct_rag_contract.py:18–30`, `tool-capability-registry.ts:89–90,111–125`.

반대 근거: 보수적 allowlist 자체는 의도된 안전 경계다. 이 차이가 현재 모든 승인 대기의 원인이라는 뜻은 아니며 Allow all 처리도 따로 있다. 각 status handler의 실제 부수효과를 확인한 뒤 범위를 확장해야 한다.

최소 변경: provider 정본의 정확한 이름·operation 분류와 하네스 허용 목록의 일치 검사를 둔다. 원격 도구의 자기 주장만 믿고 권한을 확장하지 않는다. 필요하면 검토된 provider 계약에서 빌드 시 정적 자료를 생성한다. 읽기 효과, archive 적합성, reference 정규화 지원 여부는 서로 다른 속성으로 유지한다.

### F11. 설치본의 근거 검토 계약이 저장소와 다르다

**환경 관측 · 2026-10-07 파일 확인**

저장소 evidence-first schema는 1.1.2이고 설치 skill은 1.1.1이었다. 설치본에는 작은 패킷 단위 검증 정책이 없었다. compactor의 관련 소스 네 파일은 설치본과 저장소가 동일하며 manifest revision만 120/121이다. compactor 전체를 구형이라고 설명하면 안 된다. 이번에 현재 실행 프로세스가 어떤 파일을 로드했는지 재시작/접속으로 확인하지는 않았다. 10월 4일 기록에서는 validator 1.1.1 응답을 확인했다.

근거: `evidence_packet_contract.py:8,85–101`, `runtime-identity.ts:33–54`, installer `install.py:2306–2315`. 설치 smoke는 도구 존재/readOnly/기본 오류 처리를 보지만 기대 schemaVersion 일치 조건은 없다.

최소 변경: 구현 테스트 전에 기존 설치·status 경로로 component별 실행 버전을 대조한다. smoke에 기대하는 contract revision/policy의 호환 확인을 추가한다. 기존 installer를 사용하고 별도 자동 updater는 만들지 않는다. 과거 로그를 최신 수정의 효과로 계산하지 않는다.

## 5. 현재 보존해야 할 개선과 후순위 위험

- 판단 note의 현재 상태와 WorkingContext.note는 `prediction-loop.ts:174–190`의 교체 경로에서 맞춰진다. restore는 objective/project/attachment 조건을 검사한다. RC2 이전 note 부활 문제를 새 결함으로 반복하지 않는다.
- 공유 activeProject는 `scripts/project_controller.py:4`의 소유 상태다. `unreal-config.js:28`도 공유 설정 직접 쓰기를 금지한다. 실제 엔진 Build.version 검증은 기존 `direct_rag_engine_local.py`를 재사용하며 selector에서 탐색·해시 계산을 복제하지 않는다.
- `tool-exchange-index`를 reference/recovery/note 소비자가 재사용한다. pairing을 다시 구현하지 않는다.
- `change-evidence-memory.js:65–79`는 unavailable/conflict/unknown 관측과 버전 변경으로 변경 근거를 내린다. 역사적 적용 사실과 최신 상태의 증명을 구분한다.
- EvidenceArchive는 TTL·record/window 상한·정리 경계를 가진다. 새 저장소로 긴 대화 상태를 복제하지 않는다.
- Unreal process runner는 취소와 bounded termination을 처리한다. Python RAG RequestInbox/RagOperation도 요청별 취소와 게시 경계를 갖는다. Unity Bridge는 취소 후 이미 제출된 작업을 unknown으로 유지하고 operationId 재조회 경로를 제공한다.
- 읽기/검색 내부의 협력적 취소는 추가 보완 후보다. Unreal/Unity 양쪽에서 request signal이 내부 파일 순회까지 모두 전달되지는 않는다. 파일/디렉터리 경계에서 확인하도록 보완할 수 있지만, 이번 로그 지연의 직접 원인이나 무한 실행으로 확정하지 않는다. 기존 읽기 상한은 존재한다.
- prediction-loop가 큰 조립 모듈인 것은 유지보수 부담이다. 파일 길이만으로 SOLID 위반을 단정하지 않는다. 라운드 정리·관측 적용 계약을 먼저 확정한 뒤 그 계약을 옮기는 수준으로 분할한다.

## 6. 구현 단위와 순서

| 단계 | 작업 | 주 소유자/영향 파일 | 완료 기준 | 범위 |
| --- | --- | --- | --- | --- |
| 0 | 설치·실행 기준 확인 | installer, evidence status, runtime identity, smoke | source/dist/schema/active process 출처를 구분한 기록 | 작음 |
| 1A | 잘못된 config identity 처리 | tool-scope, 기존 scope tests | 잘못된 명시 config가 workspace 사실로 승격되지 않음 | 작음 |
| 1B | 라운드 종료 fence/snapshot | round-loop, RoundTransaction 연계 tests | 모든 종료 경로에서 늦은 이벤트의 현재 상태 변경 0 | 작음~중간 |
| 2A | 예산·혼합 호출 진단 | BudgetBroker, ToolBoundary, 기존 telemetry | held/overrun/input-fit 및 per-call 결과를 구분 | 작음 |
| 2B | Unreal 반환량 계약 | Direct catalog/read capabilities/response, broker adapter | 모든 페이지가 약속한 envelope 상한과 coverage를 지킴 | 중간 |
| 2C | lexical 추출 수준 명시 | read_symbol/catalog, 엔진 조회 설명 | 후보와 확인된 선언을 혼동하는 반환 표현 제거 | 작음 |
| 3A | 버전 출처·무효화 | reference-context/EvidenceManager, provider 반환 metadata | descriptor/session/config 충돌 후 과거 applicability 사용 0 | 중간 |
| 3B | 참고용 프로젝트 관측 | 기존 reference-context + round-input view, read classification | get_active_project 관측으로 참고만 보강, 실행 권한 변화 0 | 중간 |
| 4A | Auto 요청 인식·선택 역할 | topic selector, design-guidance/catalog, telemetry | 명시 API 요청과 엔진 절이 예산 내 후보까지 연결 | 작음~중간 |
| 4B | 짧은 공통/엔진 지침 | Markdown 정본, generated bundle | 한 곳의 공통 기준과 엔진별 세부 기준, 상한 유지 | 작음 |
| 5 | 실제 효과 확인 | 기존 live/eval 도구 및 로그 | 기계적 성공과 모델 행동 개선을 별도 보고 | 선택한 고정 사례 |

0 이후 1A/1B/2A는 독립된 작은 변경으로 검토할 수 있다. 3B는 1A와 3A의 identity 정의를 먼저 확정한다. 4A의 pure selector 보완은 독립적으로 가능하지만, 실제 엔진 참고 주입의 완료는 3B와 결합해 확인한다. 2B는 provider/schema/serializer/broker를 하나의 계약 단위로 마무리한다. 각 단계는 별도 commit으로 되돌릴 수 있게 한다.

첫 구현 묶음은 **0, 1A, 1B, 2A, 2C**를 권장한다. 구조를 크게 바꾸지 않으면서 확인된 오류와 오해를 줄인다. 그다음 **2B, 3A, 3B, 4A, 4B**를 계약 단위로 진행한다. 협력적 읽기 취소와 SDK peer 결과 회수는 추가 재현이 필요한 후속 작업이다.

## 7. 구체적인 계약과 수명 규칙

### 프로젝트·엔진 관측

- 필수 식별: 검토된 provider, 실제 tool, execution/round/call, canonical project, 관측 종류, provenance.
- 선택 식별: Editor session/domain, observed engine root/version/Build.version hash, index target/generation, 해당 파일 hash.
- user 설정, 실제 반환 관측, 요청 인자, archive body는 서로 다른 신뢰 수준이다.
- 공유 기본 프로젝트와 호출별 명시 target은 별도 사실이다. 선택 설정, 요청 대상, 실제 결과 대상, Editor 프로젝트가 무엇을 증명하는지 metadata에서 구분한다. 설정 조회의 성공을 Editor 연결/설치 엔진 검증으로 승격하지 않는다.
- 동일 배치의 서로 충돌하는 프로젝트/버전은 도착 순서로 현재값을 정하지 않는다.
- 성공한 binding 변경, 관련 파일 변경, 연결 실패/모호한 결속, session 변경 후 어떤 파생 applicability를 내리는지 명시한다.
- 모든 mutation 후 전부 지우는 방식도 피한다. dependency에 해당하는 사실만 내리며 이전 근거는 historical로 남긴다.
- 실행 종료 시 임시 reference state를 폐기한다. 다음 실행으로 복원할 자료는 기존 archive/provenance 경계에서만 검증하며, 오늘의 도구 registry를 과거 결과의 provider 증명으로 사용하지 않는다.

### 읽기·부분 결과

- source hash는 버전 식별이고 전체 본문을 모델이 읽었다는 증명은 아니다.
- `returned range`, `total`, `hasMore`, `continuation`, `coverage/partial`, `representation`의 뜻을 도구 설명·생산자·소비자가 일치시킨다.
- 전체 줄 하나가 예산보다 큰 경우 partial line을 전체 줄로 표시하지 않는다. 이미 있는 byte window 경로 안내 또는 명확한 bounded 오류를 사용한다.
- cursor를 새로 도입한다면 provider가 project/query/source version/만료를 결속한다. compactor가 cursor를 생성하거나 역사적 cursor를 재사용 가능한 것으로 저장하지 않는다.
- archive는 과거 자료 확인에 사용할 수 있다. 현재 파일 버전, 쓰기 권한 또는 최신 상태를 확인해야 하는 경우 fresh read가 필요하다. 재읽기를 무조건 금지하지 않는다.

### 실행 종료

- 정상 완료, 도구 handoff, 사용자 취소, 제한 시간, argument generation failure, provider error를 각각 기록한다.
- callback fence는 최종 snapshot 확정 전에 닫는다. cleanup은 두 번 실행해도 안전해야 한다.
- cleanup이 실패 원인을 덮어쓰지 않는다. 종료 이유와 자원 해제는 별도 의미다.
- 전달된 tool result는 보존 가능하지만 guard 허용/요청 생성/dispatch 추정만으로 적용 완료를 만들지 않는다.
- 오래된 라운드 결과를 회수하려면 원래 identity가 있는 별도 historical 경로가 필요하다. 다음 라운드의 현재 상태로 섞지 않는다.

### 판단 메모와 높은 심각도 보고

- 기존 decisions/rejectedHypotheses/openQuestions의 ID·status·supersedes·refs를 재사용한다.
- 4개 항목/1500자 제한을 곧바로 늘리지 않는다. 모든 파일의 진행표를 note에 넣으면 근거와 판단을 다시 중복하게 된다.
- “읽은 범위”, “모델이 검토했다고 주장한 범위”, “실제 결함 증명”은 각각 다른 사실이다.
- 하네스는 파일 hash/관측 identity의 불일치 같은 기계적인 모순을 표시할 수 있다. P0/P1의 정당성·SOLID 준수·사용자 목표 완료를 자동 판정하지 않는다.
- evidence-first의 작은 패킷 검증을 사용하되 전체 감사 내용을 매번 다시 생성하지 않는다. 검사 성공은 구조/기록 일관성만 의미한다.

## 8. 주입 문서 보강 내용

`core/proof`의 기존 API·수명·완료 기준을 유지하면서 순서를 명확히 한다. 새 외부 API 또는 “필드/함수가 없다”는 가설로 수정하기 전에 실제 선언/부모/수신 타입을 확인한다. 같은 버전·같은 계약의 근거는 재사용하고 부분 검색을 전체 부재로 확대하지 않는다. 기존 900자 검증 상한을 유지하고 모든 문서에 문단을 복사하지 않는다.

Unreal의 기존 API/수명 절에는 다음 판단 기준을 짧게 둔다.

- FORCEINLINE: 작은 함수라는 사실, 호출 빈도/측정, 컴파일러 최적화, 코드 크기를 구분한다. 필수 correctness 매크로로 취급하지 않는다.
- UPROPERTY: GC 추적, 직렬화, 에디터/Blueprint 노출, 복제 목적을 구분한다. 모든 멤버에 일괄 추가하지 않는다.
- UFUNCTION: Blueprint/RPC/RepNotify/dynamic delegate/Exec 목적과 실제 시그니처를 구분한다. `Exec`를 OnRep의 일반 표기로 쓰지 않는다.
- Delegate: native/dynamic, single/multicast, UObject/weak/raw/lambda capture를 구분한다. 등록 중복과 해제 책임, 종료·늦은 호출을 해당 수명에 맞춰 확인한다.
- 입력: Mapping Context와 Action asset, 실제 LocalPlayer 등록, InputComponent 바인딩과 재생성/재소유를 구분한다. 함수 이름만으로 Legacy API 사용 여부를 판단하지 않는다.

Unity에서는 기존 wiring/lifetime 절을 재사용해 대응되는 확인을 보강한다.

- 실행 Editor 버전, 디스크 ProjectVersion, 설치·해결된 패키지, asmdef를 구분한다.
- SerializeField/직렬화 가능한 필드, 일반 C# property/event, UnityEvent를 사용 목적에 맞춰 확인한다.
- Awake/OnEnable/Start/OnDisable/OnDestroy, Pool 대여/반환, network Spawn/Despawn의 수명은 서로 다르다.
- 이벤트·delegate·async 결과의 등록/취소/정리는 그 작업의 수명에 맞춘다. 무조건 OnDisable 해제 또는 destroy token 하나로 통일하지 않는다.

세부 문장은 실제 적용 엔진/패키지의 공식 문서·소스로 검증한 뒤 정본에 추가한다. 현재 문서의 [근거 목록](model-guidance/sources.md)을 재사용한다. 이 계획의 예시를 특정 게임만을 위한 고정 규칙으로 만들지 않는다.

## 9. SSOT·SOLID 관점의 제약

| 원칙 | 이 계획에서 지킬 조건 |
| --- | --- |
| SSOT | provider 계약/문서/목표/근거의 기존 정본을 재사용. 서로 다른 버전 종류를 같은 scalar로 합치지 않음 |
| SRP | scope는 실행 결속, reference는 관측, selector는 선택, broker는 예약, provider는 반환 크기, round는 종료를 담당 |
| OCP | 기존 catalogue와 선택적 계약 필드를 확장. 새 엔진 분기와 API 이름을 prediction-loop에 누적하지 않음 |
| LSP | partial/unknown/lexical 결과가 complete/verified 결과처럼 소비되지 않도록 같은 도구 계약의 의미를 지킴 |
| ISP | selector에는 필요한 사실 view, 모델에는 필요한 절·범위만 제공. 전체 runtime 객체나 원장을 전달하지 않음 |
| DIP | 선택 정책은 관측 snapshot/공개 계약에 의존. 파일 읽기·SDK 호출·프로젝트 변경을 직접 수행하지 않음 |

전체 책임 분리는 새 class 개수나 파일 개수로 평가하지 않는다. 상태를 갱신하는 곳, 종료시키는 곳, 신뢰 수준을 바꾸는 곳이 분명한지를 검증한다.

## 10. 검증과 수용 기준

| 경계 | 대표 사례 | 합격 기준 |
| --- | --- | --- |
| config/project | 정상·없음·잘못된 명시 경로·다른 engine·복수 project | 출처가 다른 identity를 합성하지 않음; 조용한 재결속 없음 |
| 관측 scope | 미결속 조회 성공·실패·충돌·동시 전환·실행 종료 | 충분한 관측만 참고에 적용; 권한/프로젝트 인자 변화 없음; shared B/요청 A/결과 A는 정상, 요청 A/결과 B는 충돌 |
| Unreal binding | descriptor 변경·local lookup 실패·다른 engine root/hash | 과거 API 근거 유지, 현재 적용 가능성은 재확인 |
| Unity version | runtime/file 일치·불일치·disconnect·domain reload·package 변경 | 버전 종류별 의미 유지; stale runtime 사실 재사용 없음 |
| Auto | 한국어/영어 API·macro·delegate·input 요청, 인용·부정·긴 문장 | 관련 요청 선택 및 false positive 제한, 최대 6절/4후보 |
| 후보 채택 | engine known/unknown/mixed, budget 충분/부족, 측정 실패 | 단일 엔진 후보 경로 확인; mixed는 이번 단계에서 공통 참고와 미지원 이유로 축소; 두 버전을 한 applicability에 합치지 않음 |
| read envelope | UTF-8·escaping·CRLF·초대형 한 줄·metadata 초과·구 provider | 실제 직렬화 상한, 누락/중복 없는 범위, truthful partial |
| budget | 병렬 held 부족·결과 overrun·최종 input fit | 원인별 telemetry; 정확 토큰과 추정 회계 구분 |
| symbol | 주석·선언·호출·overload·불완전 body | lexical 추출 수준/ambiguity를 숨기지 않음 |
| round | EOS·handoff·cancel·timeout·reject·late callback·중복 cleanup | 반환 snapshot 고정; 현재 UI/evidence/budget 후속 변경 없음 |
| 결과 | 한 호출 허용/다른 호출 거부·synthetic result·unknown mutation | per-call 사실 보존; 배치가 전부 성공/전부 미실행으로 축약되지 않음 |
| 기존 수명 | note clear/restore, archive expiry, file unavailable, RAG 게시, Unity operation | RC2에서 보정한 보호 회귀 없음 |
| 설치 | repo/package/installed/running component 불일치 | 기대 계약 버전과 실행 출처 표시; 무관한 runtime 자동 변경 없음 |

실제 모델 평가는 동일한 코드 snapshot·요청·엔진/패키지·모델·KV 설정·context/output cap을 고정한 작은 사례에서 진행한다. 짧은 리뷰, 여러 파일 관계, API 선택, 중요한 판정, 긴 도구 흐름을 각각 본다. 이번 조사에서 새로운 live 모델 실행은 하지 않았다.

지표는 분모를 함께 남긴다: 새로 사용한 외부 API 중 버전 근거를 먼저 확인한 비율, 완료 주장 중 실제 적용 근거가 있는 비율, 근거 부족한 높은 심각도 판정 수, 같은 hash/같은 범위의 불필요한 재읽기 수, held 부족/결과 초과/argument 생성 실패 수, 추가 모델 호출 수. 최신 상태 확인을 위한 정상 재읽기는 낭비에서 제외한다.

기계적 계약 테스트 통과와 모델 성능 개선은 별도 결과다. 개선률이나 특정 frontier 모델과의 동급 여부는 이 계획만으로 수치화하지 않는다.

## 11. 보류할 변경과 복구 방식

- 전역 작업 상태 DB, 두 번째 판단 모델, 자동 분업, 코드 품질 강제 validator는 보류한다.
- 도구마다 무조건 사전 검색, 매 라운드 engine scan, 모든 문서 주입, 고정 파일 수 제한을 넣지 않는다.
- 전체 context/output budget 상향, 모든 읽기의 직렬화, 무조건 archive 사용은 해법으로 고정하지 않는다.
- 정식 C++ parser 도입과 SDK act 전체 교체는 별도 근거·비용 검토가 필요하다.
- 선택적 새 필드의 기본값과 구 provider 동작을 유지한다. 지침·관측 실패는 기존 입력/공통 참고로 축소한다.
- 관측 schema를 바꿀 때 호환되지 않는 파생 메모리는 unknown으로 내려 재관측한다. 원본 파일이나 적용 기록을 되돌리지 않는다.
- 설치/배포는 기존 installer의 검증·복구 경계를 사용한다. 이 계획 작성으로 설치·재시작·커밋·배포를 실행하지 않았다.

## 12. 구현 착수 판단

즉시 수정 가능한 기계적 경계와, 모델 행동을 유도하는 개선을 독립 commit으로 검토할 수 있다. 선행 조건이 해결되지 않은 엔진 지침 보강을 “자동 검증 완성”으로 보고하지 않는다. 첫 묶음의 종료·identity·진단 계약을 확인한 뒤 provider 크기 계약과 관측 수명, Auto 선택을 연결하는 순서가 현재 구조에 가장 작은 변경으로 맞는다.

## 13. 구현 결과 — 2026-10-07

### 13.1 반영한 범위

| 단계 | 실제 반영 내용 | 검증 경계 |
| --- | --- | --- |
| 0 | 저장소/설치본/실행 활성 여부를 분리해 기록. 설치 smoke가 저장소의 기대 schema·validation policy와 설치 서버의 반환 계약을 비교 | 설치 상태 조회와 격리된 계약 불일치 테스트. 현재 LM Studio 프로세스의 새 코드 실행은 미확인 |
| 1A | Auto에서 잘못된 명시 project identity를 workspace/message 근거로 보완하지 않음 | 잘못된 절대·상대 경로의 실행 결속 방지 |
| 1B | round 소유 signal과 종료 fence, 반환 메시지 snapshot. 정상/실패/취소 이후 callback과 늦은 승인 차단 | 모의 SDK 종료·승인 테스트 및 실제 prediction-loop 조립 경로 |
| 2A | held 예약 부족, 남은 용량 부족, 반환 overrun, 닫힌 배치 등을 구분. 혼합 허용/거부 상태와 호출별 사실 유지 | 예산·도구 경계 테스트. 추정 allowance를 정확한 모델 토큰 수로 표현하지 않음 |
| 2B | Unreal Direct 소스 읽기에 선택적 byteBudget 계약. 실제 JSON UTF-8 envelope, 온전한 줄 페이지, 다음 위치, bounded JSON 오류 | 한국어·escaping·CRLF·큰 단일 줄·오류 metadata. 기존 Git compact JSON 계약과 분리 |
| 2C | read_symbol의 lexical 후보/중복/부분 범위를 명시. 텍스트 추출은 순수 helper로 분리 | IO·프로젝트 선택 책임은 기존 read capability에 유지. 의미 분석/매크로 확장 검증을 주장하지 않음 |
| 3A | Unity runtime/saved-project 버전, Unreal installed-engine-source 출처 분리. descriptor·session·root manifest 변화 및 동일 배치 충돌 무효화 | 표시 상한 때문에 충돌이 사라지지 않음. 샘플 하위 manifest는 현재 패키지를 무효화하지 않음 |
| 3B | 기존 EvidenceManager 관측에서 참고 전용 GuidanceScope를 도출. 공유 기본 선택과 실제 결과 target을 구분 | 실행 scope와 도구 project 인자 불변. RAG 읽기 도구 목록은 provider 계약과 대조 |
| 4A | 명시 API/매크로/delegate/input 요청 인식, 현재 반환 본문의 선택적 API signal, 단일 엔진 pack 우선 후보 | 최대 6절/4후보와 최종 정확 토큰 측정 유지. 인용·부정·불확실 mixed scope는 기존 보수적 선택 유지 |
| 4B | 공통 proof 및 Unreal/Unity 기존 절 보강, 공식 근거 목록 갱신, 생성 번들 재생성 | 정본은 Markdown 한 곳. 상한 및 번들 일치 검사 |
| 5 | 실제 모델 품질 비교는 미실시 | 설치/재시작 후 고정 사례로 별도 확인할 범위 |

### 13.2 매크로·API 문제를 다룬 방법

매크로 자동 수정기나 의미 판정 validator를 추가하지 않았다. 기존 경로의 세 지점을 보강했다.

1. **선택:** UPROPERTY/UFUNCTION/FORCEINLINE/delegate 등을 확인하는 작업을 Auto가 API 계약 주제로 인식한다. 이미 반환된 본문의 관련 표기는 선택적 참고 신호로만 사용한다. 추가 검색이나 모델 호출은 발생시키지 않는다.
2. **근거:** 설치 엔진 헤더의 선언 발췌에 인접한 여러 줄 UFUNCTION/UPROPERTY/UMETA를 제한된 범위에서 포함한다. 문자열·주석의 괄호는 매크로 인자 경계 계산에서 제외한다. 발췌 시작·잘림·lexical 확인 수준을 반환하며, 발췌에 없다는 사실을 전체 선언의 부재로 승격하지 않는다.
3. **판단 기준:** 목적 → 실제 사용·바인딩 → 현재 엔진 선언·부모 계약 순서로 확인하도록 기존 엔진 절에 적는다. UPROPERTY의 GC/직렬화/노출/복제, UFUNCTION의 Blueprint/RPC/RepNotify/dynamic delegate/Exec, delegate의 등록·해제·늦은 호출, FORCEINLINE의 성능 판단과 C++ inline/ODR 조건을 구분한다. Unity도 SerializeField/일반 property/event/UnityEvent와 패키지·asmdef 근거를 구분한다.

정규식이나 텍스트 일부만으로 매크로 누락·정확성·사용자 목표 완료를 확정하지 않는다. 실제 선언과 사용처의 연결, UHT/컴파일 결과, 런타임 수명 확인은 서로 다른 증거다.

### 13.3 검토 중 보완한 호환성 문제

- 새 Direct 소스 읽기 계약에 `$comment: direct-json-text-utf8` 표시를 두어, 기존 Git의 compact JSON byteBudget에 pretty JSON envelope 규칙을 잘못 적용하지 않게 했다. Broker의 새 기본 byte allowance도 해당 계약에만 적용한다.
- 지원하지 않는 긴 인자의 오류도 요청 byteBudget을 지키는 파싱 가능한 JSON으로 반환한다.
- 현재 프로젝트와 다른 과거 진단은 새로운 참고 주제를 결정하지 않는다. SDK가 보관한 바인딩 전 요청 인자와 실제 바인딩 후 결과 대상도 구분한다.
- 메타데이터 표시 개수 제한 전에 버전 충돌을 계산한다. 화면에 일부 근거가 생략됐다는 이유로 충돌이 해소되지 않는다.
- descriptor 또는 manifest 읽기와 엔진/패키지 근거가 같은 배치에 있으면 결과 도착 순서로 선후관계를 만들지 않는다.

별도 읽기 전용 검토 두 건에서 찾은 호환성·무효화 문제를 재현하고 위 수정에 포함했다.

### 13.4 유지한 책임과 수명

- 실행 대상은 ToolScope/ToolBoundary, 관측은 EvidenceManager/reference-context, 선택은 guidance selector, 예약은 BudgetBroker, 반환량은 provider, 종료는 round-loop가 각각 소유한다.
- GuidanceScope는 참고 선택에 필요한 view이며 실행 project identity나 변경 권한을 가지지 않는다.
- 관측과 예약 상태는 기존 실행/라운드 수명에 머문다. 전역 원장이나 별도 판단 모델을 만들지 않았다.
- OFF 기본값, 기존 host 승인 정책, 정확한 최종 입력 fit, mutation receipt와 복구 경계는 유지한다.
- 소스 문서와 생성 번들은 정본/파생물 관계를 유지한다. 새 helper는 순수 텍스트 추출만 담당한다.

### 13.5 검증과 남은 범위

검증 로그는 기존 gitignore 대상 `artifacts/architecture-plan-20261007/`에 둔다. 버전 관리할 근거는 [구현 근거 기록](evidence/architecture-lifecycle-implementation-20261007.json)에 요약한다. 원래 [계획 근거 기록](evidence/architecture-lifecycle-plan-20261007.json)은 수정 전 기준 커밋의 조사 이력으로 유지한다.

| 최종 검증 | 결과 | 로컬 로그 |
| --- | --- | --- |
| compactor `npm test` — build 포함 | 523 passed, 0 failed | `compactor-full-final.log` |
| 별도 round/approval/architecture 경계 테스트 | 23 passed, 0 failed | `boundaries-final.log` |
| Unreal Direct `npm test` | 273 passed, 1 skipped, 0 failed. skip은 Windows에서 실행하지 않는 POSIX 전용 사례 | `unreal-full-final.log` |
| Python RAG/response budget/설치/Unity 설치 통합 묶음 | 175 passed, 3 skipped | `python-integration.log` |
| Python evidence MCP/engine-header identity/교차언어 계약/공개 경로 묶음 | 26 passed | `contracts-final.log` |
| 생성 guidance 번들 `--check`, `git diff --check` | 통과 | 명령 결과 |
| 구현 근거 JSON 형식 검사 | 6 claims, 0 errors, 0 warnings | 근거 패킷 구조·기록 일관성 검사 |

묶음 간 일부 경계 검증은 중복될 수 있어 합산한 총 테스트 수를 성능 지표로 쓰지 않는다. 근거 JSON 검사는 소스의 진실이나 전체 작업 완료를 인증하지 않는다.

현재 확인한 것은 저장소 소스·타입 검사·모의 SDK/transport·provider·설치 계약 테스트다. 실제 모델이 항상 사전 검증하는지, 잘못된 매크로를 놓치지 않는지, 긴 대화의 판단 품질이 얼마나 개선되는지는 이 테스트만으로 확정할 수 없다.

로컬 설치본과 저장소 소스가 다르며, 조사 시 설치된 Codex evidence 스킬 계약은 1.1.1, 저장소 계약은 1.1.2였다. 새 smoke는 이런 불일치를 감지하지만 설치본을 이 작업에서 교체하지 않았다. 현재 LM Studio의 실행 활성 여부는 status만으로 증명할 수 없다.

후속 범위는 기존 계획대로 실제 설치 후 live 평가, 여러 엔진의 완전한 참고 분할, 내부 읽기 작업의 협력적 취소, SDK가 아직 전달하지 않은 병렬 결과 회수다. 정식 C++ 의미 분석이나 자동 코드 품질 판정을 구현했다고 주장하지 않는다. 커밋·push·설치·재시작·배포·게임 프로젝트 변경은 수행하지 않았다.
