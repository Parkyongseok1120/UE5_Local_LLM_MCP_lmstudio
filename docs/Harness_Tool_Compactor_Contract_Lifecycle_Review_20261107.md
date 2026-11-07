# 도구·하네스·컴팩터 계약, 책임 배분, 수명 전면 검토 (2026-11-07)

## 1. 결론
- **대상:** 도구(provider), 하네스, 컴팩터를 잇는 계약 전체.
- **근거:**
  - 실행 기록 `1793532481549`(조기 종료), `1793984074550`(60라운드 한도와 지적 0건 부분 보고).
  - 기준선 `3972793` 이후 소스 변경 이력.
  - 각 경계의 실제 코드.
- **핵심 판단**
  1. 두 실행의 실패는 모델 품질보다 **경계 계약이 비어 있는 곳**에서 시작됐다. 같은 원인이 반복해서 드러난다.
     - 도구 응답이 모델용인지 호스트용인지 정해져 있지 않다(T1).
     - 크기 결정 책임이 provider, 호스트, 모델 셋에 나뉘어 있다(T2·T3·E).
     - "until_cleared" fact에 해제 조건이 없다(H1).
     - 압축 투영이 내용 보존과 크기 절감 중 어느 것도 보장하지 않는다(K1·K2).
  2. 10/28 감사의 C1–C3, X1–X9, Y1–Y8, Z1–Z6 가운데 R0.5에서 고친 항목(N1, N2, Y4, P1, K3, O1)을 뺀 나머지는 **아직 열려 있다.** 기준선 이후 해당 파일들에는 R0.5와 출력 필터 수정만 들어갔다.
  3. 책임이 `prediction-loop.ts` 한 파일(남은 공간 23B)에 몰려 있다. 그래서 새 계약을 "연결"할 자리가 없다. 이번 수정은 모두 **소유 모듈 쪽으로 책임을 옮기는 방식**으로 해야 하고, orchestrator는 호출만 하게 줄여야 한다.

## 2. 상태 소유자와 수명 지도
"선언"은 코드 주석이나 계약에 적힌 수명이고, "실제"는 코드상 동작이다.

| 상태 | 소유자 | 선언 수명 | 실제 수명 | 문제 |
|---|---|---|---|---|
| runtime fact Map (`prediction-loop.ts:269`) | prediction-loop | key별 `until_cleared` / `round`(`execution-contracts.ts:15-33`) | 실행 단위. `budget`, `stagnation`, `repeated_failure`, `prior_turn`, `report_review`, `delivered_report`에는 **해제 코드가 없다** | H1 |
| `ReadLedger` 본문·범위·hash | WorkingContext가 보유, EvidenceManager가 기록 | 대화 단위, 압축과 재시작을 넘어 유지 | 같음. 상한 64 entry | — |
| `ReadLedger.sizeHints` | 같음 | 명시 없음 | **실행이나 프로젝트 구분 없는 LRU 128개.** 읽으면 무효화된다. `invalidateReturnedSource`가 읽은 적 없는 키도 차지한다(Y6) | K5 |
| 디렉터리 목록(inventory) | **소유자 없음** | — | history에만 있고, 압축 투영에서 이름이 잘린다 | K1 |
| evidence archive와 readHandle | WorkingContext / evidence-archive | 대화 단위 | 같음 | — |
| provider snapshot receipt | provider `file-snapshot-registry` | 30분(`snapshotExpiresAt`) | 같음. 호스트는 만료를 읽지 않는다 | T1(모델에 노출만 됨) |
| BudgetBroker `retryFloors`·`metadataFloors` | BudgetBroker(실행마다 생성, `prediction-loop.ts:239`) | 실행 단위 | 같음. 다만 `context-manager.ts:182,190`이 watermark 계산용 broker를 매번 새로 만든다 | H6 |
| ReviewRegister | prediction-loop(실행마다 생성, `:155`) | 실행 단위 | 같음. 부분 보고 경로가 이 상태를 읽지 않는다 | H8 |
| DeliveryController | prediction-loop | 실행 단위 | **두 번 생성된다**(`:64` 기본 생성, `:244` 도구와 objective를 받아 교체). 그 사이 종료 경로는 objective가 없는 소유자를 쓴다 | H5 |
| guidance sticky retention | round-input | 범위 변경 전까지 | 후보 평가 중에 바뀐다(X2). architecture 활성 여부가 key에 없다(X8) | 기존 |
| model note와 continuity | note-persistence / continuity-model-notes | 대화 단위 | R0.5에서 저장 왕복을 고쳤다. Y5(window와 history 불일치)는 남아 있다 | 기존 |

## 3. 경계별 결함

### T. 도구(provider) ↔ 하네스
- **T1 응답 대상(audience)이 정해져 있지 않음 — P1**
  - 읽기 응답 하나가 모델용과 호스트용을 겸한다. `1793984074550`에서 응답마다 본문 밖 메타데이터가 **약 2.3–2.5KB**였다. 예: 3,113B 응답 중 본문은 678자다.
  - 하네스가 전혀 읽지 않는 필드: `snapshotExpiresAt`, `snapshotOwner`, `resultDigest`, `rangeSha256`, `lineNumberFormat`, `displayPath`.
  - 서로 중복되는 필드: `absolutePath`/`activeProject`/`canonicalProject`, `path`/`projectRelativePath`/`workspaceRelativePath`.
  - 이 바이트가 세 곳을 동시에 키운다. ① 모델 입력과 floor, ② 읽기 예산의 고정비, ③ projection 고정비.
  - 필드마다 소비자(host/model/both)를 tool contract 생성기(`build-tool-contract.cjs`)에서 선언하는 SSOT가 필요하다.
- **T2 `byteBudget`이 메타데이터를 포함한 전체 응답 한도라는 계약이 모델에게 새어 나감 — P2**
  - provider는 `budgetScope:"whole_response"`이고(`direct-read-capabilities.js:67`), 본문 외 고정비 약 2.4KB를 모델이 알 방법이 없다.
  - 그래서 모델이 파일 크기에 가까운 값을 주면 본문이 잘린다. 18건이 잘렸고, 약 18라운드를 썼다.
  - 크기 결정은 호스트(BudgetBroker)의 책임으로 모아야 한다(4차안 E).
- **T3 provider의 이어 읽기 제안이 작은 예산을 복제함 — P2**
  - `continuationArgs`(`direct-read-capabilities.js:76-79`)가 요청의 `byteBudget`을 그대로 `suggestion.args`에 복사한다.
  - 잘린 이유가 예산 부족인데, 제안이 같은 부족한 예산을 다시 권한다. 실제로 `byteBudget:3072` 제안이 그대로 다음 호출이 됐다.
  - 제안에서 `byteBudget`을 빼거나, provider가 계산한 남은 범위 크기를 넣어야 한다.
- **T4 목록 entry의 `size`·`modifiedAt`이 모델 본문에 섞임 — P3**
  - size는 호스트(sizeHint)가 쓰는 값이고, 모델에게는 이름이 필요하다.
  - 압축 투영 body가 이 JSON을 그대로 써서 이름이 밀려난다(K1).

### H. 하네스
- **H1 해제 조건 없는 "until_cleared" fact — P1**
  - 위 6개 key는 한 번 설정되면 실행이 끝날 때까지 매 라운드 다시 실린다.
  - `budget` fact(priority 0)는 R8 무렵 한 번의 잘림으로 설정된 뒤, "Keep the requested page budget stable…" 문장을 계속 싣는다. 압축 후 고정 예산(3072/9736)을 반복한 직접 동인 후보다.
  - `stagnation`, `repeated_failure`도 해소된 뒤까지 남는다.
  - **계약 수정:** `RUNTIME_FACTS`에 `clearOn`(예: `tool_success`, `round_end`, `report_delivered`)을 선언한다. Map 대신 `RuntimeFactStore`가 이벤트를 받아 해제한다. 수명 규칙을 표 하나(SSOT)에 둔다.
- **H2 예산 거부 코드가 흩어져 있음 — P2**
  - 생산 6곳, 해석 3곳(`budget-broker.ts`, `tool-boundary.ts:180`, `prediction-loop.ts:964`)에 나뉘어 있다(4차안 A).
- **H3 한도 뒤 도구 재개방 — P1**
  - `research_round_limit` 뒤 raw tool intent가 나오면 `fresh_tool_planning_retry` → `read_recovery_resumed_original`이 원래 도구 38개를 다시 연다(4차안 F2).
  - 한도라는 종료 조건과 복구 정책이 서로 다른 모듈에서 판단돼 충돌한다.
- **H4 objective 분류와 리뷰 대상 집합이 하드코딩됨 — P2**
  - `REVIEW_REQUEST`에 `찾을/문제점/위반`이 없다.
  - `["review","explain"]`이 `report-scope.ts:17`에 리터럴로 박혀 있다(4차안 G·C).
- **H5 DeliveryController 이중 생성 — P3**
  - 초기화 실패 경로에는 objective와 도구를 모르는 종료 소유자가 쓰인다.
  - 생성 지점을 하나로 두고, 늦게 정해지는 값은 포트로 주입해야 한다.
- **H6 상태 없는 계산을 상태 있는 클래스가 제공 — P3**
  - `BudgetBroker.watermarks`가 floor 상태를 쓰지 않는데도 context-manager가 broker를 새로 만들어 호출한다.
  - 순수 함수 `watermarks(config, measured, …)`로 분리하면 broker는 예약과 floor 상태만 소유하게 된다(SRP).
- **H7 orchestrator 비대 — 구조**
  - `prediction-loop.ts`가 111,977B(LF 기준)로 상한 112,000B에 걸려 있다.
  - 객체 분류, 예산, 가드, 복구, 검토, 전달, telemetry 연결을 모두 이 파일이 들고 있다.
  - 새 계약은 소유 모듈에 두고, orchestrator는 이벤트를 전달만 하도록 줄여야 한다. 10/28 계획 R6의 범위다.
- **H8 부분 보고가 발견을 버림 — P1**
  - `partialReport`(`delivery-controller.ts:430-485`)는 근거 범위와 발췌만 낸다.
  - 이번 실행의 사고 과정을 실제 `ReviewRegister`로 재생하면 found 행이 9개 이상 나오는데, 사용자에게는 0건이 전달됐다(4차안 F3).

### K. 컴팩터
- **K1 목록 projection에서 이름 소실 — P1**
  - 2,048자를 넘는 목록은 archive되고, `evidenceBodyView`(`evidence-archive.js:15-22`)는 `JSON.stringify(entries)`를 body로 쓴다.
  - bodyFirst excerpt(수백 자)에는 앞쪽 entry 몇 개만 남는다. 모델은 목록을 9번 다시 불렀다(4차안 B).
- **K2 projection이 절감도 보존도 보장하지 않음 — P1(측정 필요)**
  - projection은 메타데이터 약 30개 필드를 고정으로 싣는다(`working-context.js:782-826`).
  - while 루프는 "원본보다 작음"만 보장한다(`:870` 부근). 그래서 2–3KB 결과는 거의 줄지 않으면서 내용만 잃는다.
  - 최소 절감률 조건(예: 30% 미만이면 투영하지 않음)과 projection 메타데이터 축소가 필요하다. floor가 10–21K에서 30K로 커지는 현상의 유력한 원인이므로 H 측정으로 먼저 확인한다.
- **K3 floor 구성 분해 불가 — P2**
  - floor는 `buildCompactedHistory(history,0)`의 정확한 토큰 수 하나뿐이다(`context-manager.ts:740`).
  - 범주별 telemetry가 없어서 K2와 T1의 기여를 분리할 수 없다.
- **K4 압축 순서와 목표 정의 — 기존 C1–C3, X3 미해결**
  - 압축 직후 optional이 다시 입장한다(두 실행 모두 재현).
  - 두 개의 "low" 정의, `targetMet`이 설정값과 비교하는 문제도 남아 있다.
- **K5 sizeHint 수명 — P3**
  - 실행이나 프로젝트 구분이 없고 LRU라서, E(전체 읽기 하한)의 입력이 밀려날 수 있다.
  - 실행 단위 목록(B)과 같은 키(`fileSourceKey`)로 묶어야 한다.
- **K6 내부 frame 헤더 registry 부재 — 기존 X7 미해결**
  - 헤더가 10여 개 파일에 분산돼 있다.
  - 새 header를 추가할 때마다 echo 필터, private 판정, incremental 판정 세 곳을 함께 고쳐야 한다.
- **K7 pinned working set 고정 상한 — 관찰**
  - 35,789자 고정이다.
  - floor가 늘수록 압축 여유가 줄어 reclaimRatio가 떨어진다(0.28 → 0.16). K3 측정 뒤 비율 조정을 검토한다.

## 4. 목표 책임 배분

| 책임 | 단일 소유자 | 다른 모듈의 역할 |
|---|---|---|
| 응답 필드 대상 선언 | tool contract 생성기(SSOT) → provider가 따른다 | 하네스는 선언을 읽기만 한다 |
| 읽기 크기 결정 | BudgetBroker(`reserveReadResult`) | provider 제안은 크기를 복제하지 않는다. 모델은 생략할 수 있다 |
| 예산 거부 코드와 처분 | `read-budget-denial.ts` | guard와 복구는 조회만 한다 |
| 목록 해석 | `directory-listing.js` | archive body view, ledger, coverage가 사용한다 |
| 실행 단위 inventory | ReadLedger | coverage gap, 최종 안내 |
| runtime fact 수명 | `RuntimeFactStore` + `RUNTIME_FACTS.clearOn` | prediction-loop는 이벤트만 전달한다 |
| 종료 후 복구 정책 | recovery-coordinator(`mayReopenTools`, `researchRoundNotice`) | prediction-loop는 결과만 적용한다 |
| 최종·부분 보고 | DeliveryController(생성 1회, findings 포트) | register는 읽기 전용으로 제공한다 |
| 압축 목표와 순서 | ContextManager(단일 low 정의, R3) | round-input은 후보만 만든다 |
| projection 정책 | WorkingContext(최소 절감률, body view) | 소비자는 projection kind만 안다 |
| 내부 frame 헤더 | `internal-data-contract.js` registry(X7) | output 필터, context-manager, review 계열이 파생한다 |

## 5. 실행 순서 (4차안과 10/28 R1–R6 통합)
- **1단계 — 실측 실패를 직접 막는 수정** (4차안 + 이번 검토 추가분)
  1. G 분류
  2. A 거부 코드 SSOT
  3. **H1 fact 수명(`clearOn`)**
  4. E 읽기 크기 + **T3 제안에서 byteBudget 제거**
  5. B 목록(파서, body view, ledger)
  6. F 마무리(고지, 재개방 금지, 부분 보고에 발견 포함 + **H5 생성 1회**)
  7. C 범위 판정
  8. D 엔진 사실
  9. H 측정(floor 구성, projection 절감량)
- **2단계 — 측정 근거로 진행**
  - T1 응답 필드 대상 선언과 슬림화(provider와 tool contract)
  - K2 projection 최소 절감률
  - K4 = R3 압축 순서와 단일 low
  - K6 = R2 frame registry
  - H6 watermark 순수 함수
- **3단계 — 기존 계획 잔여**
  - R1 sourceKey 단일화(Y1)
  - R4 note 트랜잭션(Y5)
  - R5 RAG 패턴 수명(Z1–Z6)
  - R6 orchestrator 축소(H7)

## 6. 수명 검증 행렬 (1단계 테스트 기준)

| 상태 | 생성 | 갱신 | 해제 | 검증 |
|---|---|---|---|---|
| `budget` fact | 예산 제한 관찰 | 새 제한 관찰 | 제한 없는 읽기가 성공한 라운드 끝 | 설정 → 해소 → 다음 라운드 입력에 없음 |
| `stagnation`·`repeated_failure` | 판정 | 재판정 | 새 진전이 있는 라운드 | 해소 뒤 사라짐 |
| `round_limit` 고지 | 남은 라운드 임계 | 없음 | 실행 종료 | 정확히 1회 |
| inventory | 완료된 목록 | 같은 path 재관찰 | 새 executionId | toJSON/load 왕복, 실행 교체 |
| 목록 projection body | 첫 archive | 없음(불변) | 대화 종료 | 이름 보존, 기존 archive 불변 |
| DeliveryController | 실행 시작 1회 | 포트 주입 | 실행 종료 | 초기화 실패 경로도 같은 소유자 |

## 7. 확인 방법
- 대화 JSON 집계: 라운드별 토큰, 읽기 요청 크기와 실제 반환 본문, 목록 재호출, 종료 이벤트.
- 실제 `dist/review-register`로 사고 과정을 재생했다.
- 소스 대조: 각 항목의 file:line.
  - provider는 `lmstudio-unreal-agent-mcp/src/direct-read-capabilities.js`.
  - 하네스와 컴팩터는 `lmstudio-context-compactor-plugin/src`.
- 기준선 `3972793` 이후 변경 파일 목록으로, 10/28·10/31 항목이 미해결임을 확인했다.
- 이 문서는 읽기 전용 검토 결과이며 코드를 바꾸지 않았다.
