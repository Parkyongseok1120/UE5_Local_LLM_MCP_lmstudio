# 하네스·컴팩터·도구 계약 v3 구현 결과 — 2026-10-27

## 1. 적용 범위와 판정 수준

사용자가 제공한 v3 r2 명세를 기준으로 T0–T8을 구현했다. 기준 커밋은 `c880675`이며 작업 브랜치는 `claude/harness-compactor-contract-design-bc2f57`이다. 아래 §2–§3은 수용한 명세 원문이다. 그 안의 Develop 줄 번호는 **수정 전** 근거이며 현재 구현 위치와 차이는 §4 이후에 기록한다.

이 문서의 완료 판정은 소스·빌드·합성 회귀에 한정된다. LM Studio 설치본 교체, 재시작 및 실모델 W7은 실행하지 않았다. 원본 대화나 사용자 프로젝트 소스를 새 fixture에 복사하지 않았다.

## 2. 결함 목록 (작업과 1:1로 대응)

| ID | 등급 | 결함 | 근거(Develop) | 작업 |
|---|---|---|---|---|
| G1 | P1 | `workingSetExhausted`가 독립 종료 경로다. 압축 **전** 측정값을 쓰고, 리뷰 휴리스틱(`reviewRegister.rows.some(row=>row.bundle)`)에 의존한다. 진행 판정(`usefulProgress`)·`toolStagnationAction`·`noNewEvidenceRounds`를 무시하고, `research_no_new_evidence`로 잘못 라벨링한다. | `prediction-loop.ts:496`, `:1606-1611`. `round-input.ts:203`(`projectionExhausted` 측정)은 `:310-323` `enforceLowWater`보다 먼저 실행된다. | T2 |
| G2 | P1 | 사용자 목표가 setup 때만 쓰인다. `readOnlyReviewRequest`는 profile만 정한다. 최종 지시는 "읽기 전용 복구 종료"라는 틀린 문맥이다. 복구·재계획은 쓰기 도구를 돌려주지 못한다. | `tool-capability-registry.ts:102-112`(`readOnlyReviewRequest`), `prediction-loop.ts:158-161,388,414`, `execution-instructions.ts:89-95`(`RESEARCH_RECOVERY_FINAL_INSTRUCTION`), `recovery-coordinator.ts:213,229-260` | T1, T2 |
| G3 | P1 | 정체/종료 판정기가 6개다. `noNewEvidence→report_now×2`, `workingSetExhausted`, `RecoveryCoordinator` stagnant, `ToolRoundStagnationDetector`, forced partial, round limit. `finishResearch` 호출 지점은 약 15곳이다. | `evidence-manager.ts:140-145`, `prediction-loop.ts:1221-1279`, `recovery-coordinator.ts:43-59`, `delivery-controller.ts:108-111,271-274` | T2 |
| G4 | P2 | 병렬 예약이 과다하게 묶인다. greedy 할당이고, 예약 +1024 / 수신 +128처럼 봉투 상수가 다르다. known-size 반복은 ×1.25+512다. 수신 반영은 라운드 종료 후에만 일어난다. 실측: 실사용 10,737 / 예약 23,075에서 6번째 호출이 거부됐다. | `budget-broker.ts:75-78,102,117,211-234,292-297`, `prediction-loop.ts:697,785,812-818` | T3 |
| G5 | P2 | 도구 분류의 SSOT가 깨졌다. `evidence_first_*`가 하드코딩돼 있어 validator가 읽기 예산(3072+1024, 최소 4096) 때문에 거부됐다. `ephemeral`이 read로 집계된다. 이름 목록이 7벌 중복돼 있다. `config/tool_contract.json`은 소비자가 없는데 drift가 있다. | `tool-capability-registry.ts:26,62-64`, `build-tool-contract.cjs:12`, 중복 목록: `read-ledger.js:15`, `quote-anchoring.ts:10`, `reference-context.ts:74`, `write-api-anchoring.ts:13`, `continuity-model-notes.js:241`, `delivery-controller.ts:126`, `engine-profiles.js`. drift: `tool_contract.json`의 `INVALID_ARGUMENTS` vs `lmstudio-unreal-agent-mcp/src/direct-runtime-shared.js:68`의 `INVALID_ARGUMENT` | T5 |
| G6 | P2 | 오류 봉투가 4가지 방언이다. 하네스는 `retry.*`를 읽지 않는다. RAG `engine_local`의 OUTPUT_LIMIT_EXCEEDED는 재시도 불가이고 최소값도 공표하지 않는다. 하네스는 `maxChars`를 남은 예산(3362)까지 줄여 막다른 길을 만든다. | `lmstudio-unreal-agent-mcp/src/direct-response.js:118-184`, `scripts/direct_rag_result.py:141-162`, `scripts/direct_rag_engine_local.py:79-81,188-202`, `scripts/direct_rag_limits.py:61-69`, `skills/evidence-first-code-audit/scripts/evidence_first_mcp.py:198-212`, `budget-broker.ts:151,234` | T4 |
| G7 | P2 | 반환 소스 판정이 transcript 재스캔이다. 자체 도구 목록을 쓰고 `content` 필드만 인정한다. 그래서 `search_files`로 반환된 엔진 `Controller.cpp:58`을 무시하고 "이번 실행은 해당 엔진 소스를 반환하지 않았습니다"라고 오공지한다. 이전 턴까지 스캔하면서 "이번 실행"이라고 표기한다. | `engine-claim-anchoring.ts:33-76`, `quote-anchoring.ts:44-72`, `prediction-loop.ts:1179,1203-1204,1517-1518` | T6 |
| G8 | P2 | 핀이 목표를 모른다. 선택 guidance 예약이 핀보다 먼저 자리를 차지한다(61,029만이면 high water 63,626 미만이었다). 핀 결정이 두 번 일어나고 예산도 서로 다르다. 현재 턴만 보며, key가 path뿐이다. | `round-input.ts:169-233,416-420`, `context-manager.ts:586-591,680,714-718`, `working-context.js:119-175` | T7 |
| G9 | P3 | telemetry 결함. `restoredReads`가 실제로는 `focusedReads`(첫 집중 + 복원)다. RestoreResult를 기록하지 않는다. `callPurpose`가 항상 research다. `report_review_scheduled.missing`에 `openQuestions`(스키마 필드)가 섞인다. system prefix 재작성을 측정하지 않는다(prefill 40–60s). | `prediction-loop.ts:231,846,1065-1068`, `working-context.js:1128-1133`, `context-manager.ts:151-156`, `round-input.ts:417` | T8 |

---

## 3. 계약 정의 (모든 작업이 따르는 규칙)

각 사실·결정마다 **작성자는 하나**다. 나머지 모듈은 읽기만 한다.

### C1. TaskObjective (사용자 목표)

```ts
// execution-contracts.ts
export type ObjectiveKind = "review" | "mutate" | "build" | "explain";
export type MutationState = "none" | "attempted" | "applied" | "blocked";
export interface TaskObjective {
  readonly kind: ObjectiveKind;
  readonly requiredEffects: ReadonlySet<"read" | "mutation" | "long_running">;
  mutationState: MutationState; // 작성자: mutation effect 도구의 실행 결과만
}
```
- **분류기**: `classifyObjective(text)`를 `tool-capability-registry.ts`에 둔다. 기존 정규식 `REVIEW_REQUEST`/`CHANGE_REQUEST`/`NO_CHANGE`를 재사용한다.
  - `CHANGE_REQUEST`가 일치하고 빌드·컴파일·실행 단어가 있으면 `build`, 없으면 `mutate`
  - 그 외 `REVIEW_REQUEST`가 일치하면 `review`
  - 나머지는 `explain`
  - `readOnlyReviewRequest(text)`는 `classifyObjective(text).kind === "review"`로 다시 구현하고, 기존 동작을 보존한다.
- **mutationState 갱신**: mutation effect 도구(registry 기준)의 결과가 도착했을 때만 바꾼다.
  - 성공: `applied`
  - 실패: `attempted`
  - C2 정책이 수정을 포기한 경우: `blocked`
  - 모델 문장으로는 바꾸지 않는다.
- **불변식**
  - I1a (예방): `kind∈{mutate,build}`이고 `mutationState==="none"`이면, C2 계층 A(`decide()`)는 narrow 라운드를 시도하기 전에 압박·정체를 이유로 finalize하지 않는다. 계층 B 전환(timeout, 출력 한도 등)은 예외로 허용한다.
  - I1b (고지, 단일 지점): `kind∈{mutate,build}`이고 `mutationState!=="applied"`인 상태에서 최종화하면, **trigger와 상관없이** "요청한 변경은 적용되지 않았습니다: <trigger별 원인>"을 넣는다. 넣는 곳은 다음 두 곳뿐이다.
    - 최종 지시: `prediction-loop.ts:409-415`(`roundScopeInstructions` 선택식)의 맨 앞에 `MUTATION_NOT_APPLIED_INSTRUCTION`을 추가한다.
    - 부분 보고: `delivery-controller.ts:64`(`partialReport`)의 첫 줄.
    - 다른 곳에 같은 문구를 중복 삽입하지 않는다.
  - I2: 복구·재계획 뒤 복귀하는 profile은 `requiredEffects`의 도구를 포함해야 한다. 기존 `measureOriginalProfile` 경로를 재사용한다.
- **수명**: execution 하나. 영속화하지 않는다.

### C2. ContinuationDecision (종료/계속 결정의 유일한 권한)

```ts
// execution-contracts.ts
export interface RoundVerdict {          // 작성자: EvidenceManager, 라운드당 1회, 불변
  readonly progressed: boolean;          // 기존 recoveryProgress.usefulProgress와 같은 의미
  readonly noNewEvidence: boolean;       // 기존 evidence-manager.ts:140 식
  readonly failedReads: number;
  readonly budgetDenied: readonly string[];   // 거부된 callId
  readonly mutationOutcomes: readonly ("applied"|"failed")[];
}
export interface PressureSignal {        // 작성자: round-input, 최종 조립 측정
  readonly workingSetExhausted: boolean;
  readonly measuredStage: "final";
}
export type ContinuationAction = "continue" | "recover_read" | "narrow_to_mutation" | "finalize";
```
- **결정 함수**: `RecoveryCoordinator.decide({verdict, objective, pressure, limits, state}) → {action, trigger}`. 순수 함수로 만들고 단위 테스트를 붙인다.

- **2계층 권한 구조 (사용자 확정)**

  **계층 A — 도구 라운드 뒤 계속/종료. `decide()`가 독점한다.**
  - 대상은 모델이 도구를 호출하고 더 진행하려는 라운드(`captured.continueAfterTools === true`)의 끝에서 내려지는 결정이다.
  - 현재 이 결정을 내리는 곳은 아래 4곳이고, 모두 `decide()` 호출 하나로 대체한다.

  | 현재 위치(c880675) | 현재 동작 | 처리 |
  |---|---|---|
  | `prediction-loop.ts:1221-1246` | `noNewEvidence` 카운트 → `report_now` fact, 두 번째에 `reportNowFinalize` | 카운트·fact 생성은 남긴다(입력 신호). `reportNowFinalize` 변수는 삭제하고 `decide()` 입력의 `state.reportNowTriggers`로 넘긴다 |
  | `prediction-loop.ts:496`, `:1606-1611` | `workingSetExhausted` → `finishResearch("research_no_new_evidence")` | 삭제. `PressureSignal`로 대체 |
  | `prediction-loop.ts:1613-1622` | 라운드 한도 → `research_round_limit` (bounded/비bounded 2곳) | `decide()`의 첫 행으로 이동 |
  | `prediction-loop.ts:1247-1279` | `ToolRoundStagnationDetector` 경고/일시정지 | 경고 fact는 남긴다. 종료 권한은 없다(현재도 finishResearch를 직접 부르지 않음. 그대로 유지하고 확인만) |

  **계층 B — 생성 결과에 대한 반응. 제자리 유지, 닫힌 allowlist.**
  - 아래 10곳은 모델 출력 사건(시간 초과, 반복, 출력 한도, 보고 누락 등)에 대한 정해진 반응이다. 정체를 판단하는 곳이 아니다.
  - 위치와 조건은 바꾸지 않는다. 바꾸는 것은 I1b 고지와 trigger 라벨뿐이다.

  | # | 위치(c880675) | trigger | 조건 요약 |
  |---|---|---|---|
  | B1 | `:323` | `research_time_limit` | bounded audit 기한 초과 |
  | B2 | `:489` | `context_budget` | 입력 압박에서 복구 후보 없음(`resolveInputPressure`) |
  | B3 | `:477` | (`startRecovery("context_budget")`) | 입력 압박 + 복구 후보 있음 |
  | B4 | `:1415` | `research_timeout` | phase timeout |
  | B5 | `:1434`, `:1443` | `reasoning_repetition` 재시도 / `research_repetition` | 반복 추론 |
  | B6 | `:1466` | (`startReasoningRecovery()`) | 생각 단계 생성 한도, 도구 활동 없음 |
  | B7 | `:1492`, `:1505` | `recoveryCoordinator.advance()`의 trigger / `endRecovery` | 읽기 복구 라운드 진행 판정 (기존 coordinator 소유, 유지) |
  | B8 | `:1509` | `research_output_limit` | bounded + `maxPredictedTokensReached` |
  | B9 | `:1533` | `report_review` | 도구 없는 EOS 보고 + 누락/엔진 gap |
  | B10 | `:1549`, `:1566` | `output_recovery` / `research_recovery_exhausted` | 출력 한도 이어쓰기 / 복구 라운드의 raw tool text |

  - 표의 `startReplan`(`:1008`), `finishReasoningRecovery`, `terminate`(`:1677`)도 계층 B에 속하고 그대로 둔다.
  - **회귀 방지 테스트(필수)**: `test/transition-authority.test.cjs`를 새로 만든다.
    - `src/prediction-loop.ts` 원문에서 `execution.finishResearch(`, `execution.startRecovery(`, `execution.startReasoningRecovery(`, `execution.startReplan(` 호출을 정규식으로 센다.
    - 각 호출이 감싸는 trigger 문자열(또는 `decision.trigger`)을 위 A/B 목록과 대조한다.
    - 목록에 없는 호출이 생기거나 개수가 바뀌면 실패한다. 실패 메시지는 "계약 §3 C2 표에 추가하거나 decide()를 거치라"로 한다.
    - 같은 방식으로 `workingSetExhausted`, `reportNowFinalize` 식별자가 0개인지도 검사한다.

- **계층 A 정책 표** (`decide()` 안에서 위에서부터 처음 맞는 규칙을 적용. "그 외 → continue"는 계층 A 범위에서만 의미가 있다)

| 조건 | action | trigger |
|---|---|---|
| 라운드/시간 한도 도달 | finalize | 기존 `research_round_limit` |
| `pressure.workingSetExhausted` && objective `review` | finalize | `context_working_set_exhausted` (신규) |
| `pressure.workingSetExhausted` && objective `mutate`/`build` && 이번 execution에서 narrow 미시도 | narrow_to_mutation | — |
| `pressure.workingSetExhausted` && objective `mutate`/`build` && narrow 이미 시도 | finalize, `mutationState="blocked"` | `mutation_blocked_by_context` (신규) |
| `verdict.budgetDenied.length>0` | continue (이번 라운드는 정체 집계 제외) | — |
| `state.reportNowTriggers>=2` && (objective `review`/`explain` 또는 `mutationState!=="none"`) | finalize | `research_no_new_evidence` |
| `state.reportNowTriggers>=2` && objective `mutate`/`build` && `mutationState==="none"` && narrow 미시도 | narrow_to_mutation | — |
| `state.reportNowTriggers>=2` && objective `mutate`/`build` && `mutationState==="none"` && narrow 이미 시도 | finalize, `mutationState="blocked"` | `mutation_blocked_by_context` |
| 그 외 | continue | — |

- **호출 위치**: `decide()`는 현재 `:1605`(`if (!captured.continueAfterTools) break;`) 바로 뒤, 삭제되는 `:1606-1622` 자리에서 한 번만 호출한다.
  - `limits`에는 기존 `boundedAudit`/`auditResearchRounds`/`researchDeadlineAt`/`maxResearchRounds` 판정을 넣는다.
  - `finalizing`/`reasoningRecovery`/`recovering` 중에는 호출하지 않는다. 현재 `:1606`의 가드와 같다.
- **narrow 라운드 재진입**: narrow 라운드 자체는 계층 A의 대상이다(도구가 있다). 그 라운드 끝에서 `decide()`가 다시 판단한다.
  - mutation 결과가 있으면 원래 profile로 복귀하고 continue한다.
  - 없으면 위 표의 "narrow 이미 시도" 행으로 간다.

- **`narrow_to_mutation`의 의미**: 다음 1개 라운드의 도구를 mutation effect 도구, 해당 파일의 재읽기(`read_file`, `read_file_range`), `static_validate_project`로 제한한다. prose는 archive한다(기존 `archiveConversationProse`). 최종 지시 대신 "수집한 근거로 지금 변경을 적용하라. 추가 조사는 하지 말라"는 runtime fact를 넣는다. 이 라운드에서 mutation 결과가 나오면 원래 profile로 복귀한다(I2).
- **불변식**
  - I3: trigger 라벨은 실제 원인과 1:1로 대응한다. `FinalizationTrigger`에 `context_working_set_exhausted`, `mutation_blocked_by_context`를 추가하고, `delivery-controller.ts`와 `prediction-ui.ts`의 문구 매핑도 추가한다.
  - I4: `projectionExhausted`는 `enforceLowWater` 적용 **후**의 최종 입력으로 계산한다.
  - I5: 예산 거부가 생긴 라운드는 정체 카운터(`noNewEvidenceRounds`, `reportNowTriggers`, coordinator `noProgressRounds`)를 올리지 않는다.
- `ToolRoundStagnationDetector`와 report_now 계산은 **입력 신호만** 만든다. `finishResearch`를 직접 부르지 않는다.

### C3. ToolCapability (도구 분류 원천 = `config/stable_tool_manifest.json`)

- manifest에 `evidenceFirstToolEffects`(`evidence_first_contract`, `evidence_first_validate`, `evidence_first_status`, `evidence_first_read_context`)를 추가한다.
- 모든 operation에 `resultKind`를 추가한다. 값은 `source_body|search_hits|archive_page|validation|metadata|proposal|mutation_result` 중 하나다. 필요한 경우 `minimumEnvelopeChars`도 추가한다.
- `build-tool-contract.cjs`가 이것을 `generated-tool-contract.ts`로 출력한다. `--check`가 불일치를 실패로 처리해야 한다.
- `ToolCapabilityRegistry`에 질의 메서드를 둔다: `resultKind(tool, req)`, `needsReadReservation`(source_body/search_hits/archive_page만 true), `isMutation`, `isSourceBody`.
- 하드코딩 목록(`tool-capability-registry.ts:63-64`, 플러그인 ID 문자열 포함)과 §2 G5의 중복 목록 7곳을 registry 질의로 바꾼다.
- `ephemeral`을 read 집합에서 분리한다(`resultKind:"proposal"`, 예약 없음, 복구 대상 아님).
- `config/tool_contract.json`: 해당 섹션 상단에 `"derivedFrom":"config/stable_tool_manifest.json"`를 두고 drift 값(`agentErrorCode`, `static_validate_project` 분류, 누락된 `workspace_status`/`git_*`)을 맞춘다. 일치 검사는 `tests/test_cross_language_tool_contract.py`에 추가한다.

### C4. BudgetReservation

- 작성자는 `BudgetBroker`/`BatchReservation`이다.
- 봉투 토큰 상수는 하나(`ENVELOPE_TOKENS`)로 하고 예약과 수신에 같이 쓴다.
- known-size 반복 읽기는 archive 경로(`budget-broker.ts:198-207`)와 같은 방식으로 `ceil(known/2.5)+ENVELOPE_TOKENS`를 정확히 예약한다.
- **요청 수는 쓰지 않는다**(r2). SDK 1.5.0의 처리 순서는 다음과 같다(`node_modules/@lmstudio/sdk/dist/index.cjs:16490-16575`).
  1. 요청 하나가 파싱됨(`onToolCallRequestEnd`)
  2. `queue.runInQueue` 안에서 그 요청의 guard 실행
  3. allow/deny
  4. `onToolCallRequestFinalized`
  5. 실행

  guard 시점에는 뒤쪽 요청이 아직 스트림에 남아 있을 수 있다. 그래서 대신 다음 세 규칙을 적용한다.
  1. **정확한 반복 예약**: known-size 반복(`budget-broker.ts:220`의 `×1.25+512` 경로)은 `ceil(knownBytes/2.5)+ENVELOPE_TOKENS`로 예약한다. `knownBytes`는 이미 SDK가 받은 실제 bytes다(실측 확인: reserved 5577 = 4051×1.25+512).
  2. **상수 둘, 의미를 분리**
     - `ENVELOPE_TOKENS = 256`: 크기를 이미 아는 양에 쓴다. 대상은 known-size 예약, archive 정확 예약(`budget-broker.ts:198-207`의 현재 값), 수신 계상(`:117`의 128)이다.
     - `UNKNOWN_SIZE_MARGIN_TOKENS = 1024`: 처음 보는 크기의 예약에만 쓴다(`:211`의 현재 값). SDK 재직렬화와 escape로 커질 수 있는 몫이다. 알 수 없는 크기의 첫 읽기 공식(byte→token 배수 + 이 margin)은 바꾸지 않는다.
     - 두 상수는 `execution-contracts.ts`에 이름을 붙여 정의하고, 숫자 리터럴로 중복 사용하지 않는다.
  3. **최소 1건 여유 남기기**: greedy 할당(`:102`)은 유지한다. 단, 이번 호출에 준 뒤 남는 양이 `reserveFloor` 미만이 되면 이번 호출 할당을 `available - reserveFloor`로 줄인다. `reserveFloor`는 registry 기준 가장 작은 source_body/search_hits 최소 예약(현재 `search_files` 1844 상당)이다. 줄인 값이 이번 호출의 minimum보다 작아질 때만 floor를 무시한다.
- **실측 기대치**(T0 테스트 oracle): 수정 후 첫 4건(known-size)의 예약 합은 ≈10,414 이하다(1877+3963+2053+2521, ±봉투 반올림). 5번째(search, adaptive)는 floor를 남기고 할당된다. 6번째 `search_files`(min 1844)는 **허용**된다.
- `needsReadReservation=false`인 도구(C3)는 고정 소액 비용만 계상하고 거부하지 않는다.
- 거부 시 `RoundVerdict.budgetDenied`에 callId를 넣는다.

### C5. ToolResultEnvelope

```json
{ "ok": false, "errorCode": "OUTPUT_LIMIT_EXCEEDED", "message": "…",
  "retry": { "allowed": true, "mode": "different_arguments",
             "minimum": { "field": "maxChars", "value": 6200 } } }
```
- `mode`의 값 집합: `none|same_arguments|different_arguments|reduce_scope|after_state_change`. 이 형태를 manifest `errorEnvelope` 섹션에 문서화한다.
- **서버 측**
  - RAG `failure()`와 `engine_local`의 metadata 초과 경로(`direct_rag_engine_local.py:188-202`)는 `allowed:true`, `mode:"different_arguments"`, `minimum`(실제 필요한 문자 수)을 반환한다.
  - evidence-first의 `{ok:false,error}`에는 `errorCode`와 `retry`를 추가한다. `error` 필드는 호환을 위해 유지한다.
  - agent 서버는 이미 형태를 준수하므로 conformance 테스트만 추가한다. Unity 서버는 바꾸지 않는다.
- **하네스 측**: `tool-result-semantics.js`에 `decodeEnvelope(result)`를 추가한다. Unity의 소문자 필드와 SDK 거부(`{"error": reason}` → `errorCode:"HARNESS_BUDGET_DENIED"`)도 정규화한다. `RoundVerdict`와 다음 라운드 안내가 이 결과를 소비한다.
- `BudgetBroker`는 manifest의 `minimumEnvelopeChars` 또는 직전 응답의 `retry.minimum`보다 작게 `maxChars`/`byteBudget`을 줄이지 않는다. 최소값을 확보할 수 없으면 dispatch 전에 거부하고, 거부 메시지에 필요한 최소값을 적는다.

### C6. ReturnedSource

- 작성자는 `ReadLedger`다.
- 질의 API: `EvidenceManager.returnedSource(sourceKey, span) → {returned:boolean, via:"body"|"search_hit"|"archive_page", scope:"execution"|"conversation"}`
- `search_files` 결과의 `results[].line`과 `text/context`를 해당 줄 반환으로 ledger에 기록한다(`via:"search_hit"`, 본문 전체 coverage로 승격하지 않음).
- `engine-claim-anchoring.ts`와 `quote-anchoring.ts`는 이 질의만 쓴다. `returnedSourceLines`의 transcript 재스캔과 자체 도구 목록은 삭제한다. 본문 필드 인정 범위는 `content/text/body/code`이고, ledger와 일치시킨다.
- 공지 문구는 실제 `scope`에 따라 "이번 대화에서"/"이번 실행에서"로 쓴다.
- claim↔fact 오매칭(중복 `bCanEverTick=true` 지적에 "tick 비활성화" 사실을 연결한 사례)은 **고치지 않는다**. 현재 동작을 fixture로 고정하고 한계로 기록한다.

### C7. WorkingSet 핀

- **선택 우선순위**
  1. objective가 mutate/build일 때의 변경 대상 소스: 현재 execution의 review-register found 행, 그리고 직전 턴 checkpoint `currentTurnFindings`가 가리키는 파일
  2. 미해결 claim span
  3. 기존 규칙(인용 → 재조회 → 최근성)
- **입력 예산 예약 순서**: 핀 → 필수 floor → 선택 guidance.
  - `round-input.ts`의 핀 사다리(1/2, 1/4, 0)는 guidance 예약을 뺀 상태에서 판정한다.
  - guidance는 남은 공간에만 들어간다(`round-input.ts:416-420`).
- `context-manager.ts` `enforceLowWater`의 `pinned_` 후보도 같은 `BudgetBroker.pinnedWorkingSetChars` 결과를 사용한다(핀 결정은 라운드당 1회).
- 핀 key는 `sourceKey`(provider+project+root+path, `evidence-identity.js` 기존 함수)로 바꾼다.
- `working-context.js:160-161`의 stale 주석을 실제 동작으로 고친다.

### C8. Telemetry

- `read_ledger` 이벤트: `firstFocusReads`, `restoreRequiredReads`, `repeatRestores`를 분리한다. 기존 `restoredReads`는 호환을 위해 유지하고, 값은 `restoreRequiredReads`로 정정한다.
- `working_context_restore`: `windowStatus`, `ledgerStatus`, `accessInherited`를 기록한다.
- `execution_outcome`: `objectiveKind`, `mutationState`를 기록한다.
- `callPurpose`: `mutation_focus`(narrow 라운드) 값을 추가한다.
- `report_review_scheduled.missing`: 스키마 필드명(`openQuestions` 등)을 제외한다.
- 신규 `system_prefix_fingerprint` 이벤트: 라운드별 system 메시지 해시와 직전 대비 변경 여부. **측정만 한다.** system 메시지 구조는 바꾸지 않는다.
- `scripts/`의 기존 LM Studio run analyzer(커밋 `1c56497`)가 새 필드를 읽어 요약하도록 갱신한다.

### 수명 표

| 상태 | 생성 | 소멸 | 작성자 |
|---|---|---|---|
| TaskObjective | execution setup | execution 종료 | classifyObjective + mutation 결과 |
| RoundVerdict | 라운드 결과 수집 직후 1회 | 다음 라운드(불변) | EvidenceManager |
| PressureSignal | 최종 입력 측정 시 | 다음 modelInputId | round-input |
| ContinuationDecision | 라운드 끝 1회 | 즉시 소비 | RecoveryCoordinator → ExecutionState |
| narrow 시도 플래그 | 첫 narrow | execution 종료 | RecoveryCoordinator |
| BatchReservation | 라운드 batch 시작 | close(finally) | BudgetBroker |
| 핀 | 입력 조립마다 재계산 | 목표 충족·source 변경 | working-context(선택) + BudgetBroker(예산) |

---

기계 판독용 근거는 [v3 evidence JSON](evidence/harness-compactor-contract-v3-20261027.json)이다. 패킷 검사는 형식·기록 일관성만 검사하며 실제 사실의 증명은 인용한 소스와 회귀 결과에 한정된다.

## 4. 실제 책임과 수명

| 계약 | 결정/사실의 단일 소유자 | 소비자와 수명·무효화 | 구현 위치 |
|---|---|---|---|
| C1 목표 | `classifyObjective`, execution의 `TaskObjective` | 실행 시작 한 번. 실제 mutation 결과로 상태 변경; 종료 고지는 DeliveryController와 최종 지시 두 경로 | `src/execution-contracts.ts:12`, `src/tool-capability-registry.ts`, `src/delivery-controller.ts`, `src/execution-instructions.ts` |
| C2 계속/종료 | `EvidenceManager`의 immutable RoundVerdict + `RecoveryCoordinator.decide` | 완료된 도구 라운드 A 진입점 1곳. pressure는 최종 low-water 후 측정. B 종료 경로는 기존 조건 유지 | `src/evidence-manager.ts`, `src/recovery-coordinator.ts:31`, `src/round-input.ts`, `src/prediction-loop.ts` |
| C3 분류 | `config/stable_tool_manifest.json` | 생성된 CJS capability data/core를 TS registry와 JS 소비자가 공유. 런타임 provider/action 검증. proposal은 read reservation 제외 | `scripts/build-tool-contract.cjs`, `src/tool-capability-core.js:8`, `src/tool-capability-registry.ts` |
| C4 예산 | `BudgetBroker` / `BatchReservation` | 실행별 broker, 배치별 reservation. 알 수 없는 첫 결과 여유 1024 유지; 알려진 결과 `ceil(bytes/2.5)+256`, receive 256; 가능한 최소 source/search 한 건 여유 | `src/budget-broker.ts:221`, `src/tool-boundary.ts` |
| C5 오류/재시도 | decoder의 정규화 결과, provider의 실제 minimum | 요청 인자별 retry floor는 execution 수명. 성공 결과로 폐기. guard trace 없는 SDK 오류는 budgetDenied 권한 없음 | `src/tool-result-semantics.js:58`, `scripts/direct_rag_result.py`, `scripts/direct_rag_engine_local.py`, `skills/evidence-first-code-audit/scripts/evidence_first_mcp.py` |
| C6 반환 소스 | `ReadLedger`의 returned spans/text | 실제 짝이 맞는 반환 결과를 EvidenceManager가 기록. search_hit은 반환 줄일 뿐 file coverage 아님. checkpoint 로드는 conversation scope, 새 결과는 execution scope | `src/read-ledger.js:193`, `src/evidence-manager.ts`, `src/engine-claim-anchoring.ts`, `src/quote-anchoring.ts` |
| C7 핀 | round의 BudgetBroker 허용량 + WorkingContext 선택 | 목표 파일 > claim span/ref > 인용/재조회/최근성. 이전 found 대상은 실행 시작에 보존해 현 턴 checkpoint 교체 뒤에도 유지. provider/project/root/path sourceKey로 분리 | `src/working-context.js:129`, `src/round-input.ts`, `src/context-manager.ts` |
| C8 계측 | 각 소유자가 만든 결과의 telemetry projection | 실행별 system hash, 라운드별 read counts. 분석기는 이전 ambiguous restoredReads를 새 수치로 재해석하지 않음 | `src/evidence-telemetry.ts:36`, `src/prediction-loop.ts`, `scripts/analyze_lmstudio_run.py` |

위 `src/`와 플러그인 `scripts/` 경로는 `lmstudio-context-compactor-plugin/` 기준이다. RAG/Python 경로는 저장소 루트 기준이다.

### 계약 간 연결

1. 도구 manifest → generated capability → profile/guard/reservation/result kind가 같은 분류를 사용한다. Unity 서버의 기존 오류 형식은 변경하지 않았다.
2. request/result의 고유한 짝 → decoder/semantics → mutation state 및 returned-source 사실 → immutable round verdict → 단일 계속 결정 순서다. 모델 문장이나 요약은 mutation 성공·읽기 반환 사실을 만들 수 없다.
3. source projection과 compaction은 같은 핀 허용량을 전달한다. optional guidance는 핀과 mandatory floor 뒤에서 남은 공간에만 들어간다. recovery 후보에도 같은 metadata/예산이 전달된다.
4. 원문 범위/권한, 실제 반환 사실, 모델의 판단은 별개다. 지속 원장의 범위가 남아 있어도 삭제된 본문을 현재 입력에 보인다고 처리하지 않는다. 복원은 기존 access/receipt/CAS 검증을 통과해야 한다.

### C7 독립 반례 검토 결과

- 같은 파일의 1행 claim과 최근 100행 페이지: path만 비교하던 시도에서 span/ref 비교로 고쳤다. SHA 대소문자는 정규화 후 정확 비교한다.
- 직전 checkpoint의 found 대상: 실행 시작에 snapshot을 잡아 현 턴 checkpoint로 교체돼도 유지한다.
- bare/project:// /engine:// /Windows drive 경로를 canonicalize한 뒤 비교한다.
- recovery의 기본6144/무조건0 경로를 제거하고 공유 허용량을 연결했다.
- 입력 61,029 + optional guidance 5,523 > high water 63,626 회귀에서 실제 source 본문을 보존하고 guidance를 제외한다.

## 5. 검증 결과

진단 로그의 공개 별칭은 `${DIAGNOSTICS}/harness-contract-v3-20261027/`이다. 실제 로컬 경로 매핑은 저장소 밖에 보존했다.

| 검사 | T0 기준선 (통과/실패/제외) | 최종 (통과/실패/제외) | 근거 |
|---|---:|---:|---|
| plugin 전체 | 1059 / 0 / 0 | 1131 / 0 / 0 | `T0-baseline-plugin.log`, `T8-plugin.log` |
| Unreal agent | 364 / 0 / 2 | 365 / 0 / 2 | `T0-lmstudio-unreal-agent-mcp.log`, `T8-agent.log` |
| Unity | 132 / 0 / 5 | 132 / 0 / 5 | `T0-lmstudio-unity-mcp.log`, `T8-unity.log` |
| Python 전체 | 1468 / 16 / 41 | 1487 / 0 / 41 | `T0-python.log`, `T8-python.log` |
| T0 새 반례 | 1 / 10 / 0 | 11 / 0 / 0 (plugin 전체에 포함) | `T0-red.log` |
| 생성물 검사 | 4종 일치 | 4종 일치 | tool/design/unity/runtime identity `--check` |
| diff 공백 검사 | 오류 없음 | 오류 없음 | `git diff --check` |

T6는 1121/0, T7은1127/0이었다. C8 최초 focused 30/0, analyzer·workspace·public-path focused Python 28/0이다. 추가 테스트는 합성 fixture이며 실모델의 재조회 감소율이나 prefill 개선율을 보장하지 않는다.

### 기존 실패와 명세 차이

- **사용자 승인 예외:** C4 floor와 충돌한 읽기 복구 fixture에 저렴한 `search_files`만 추가했다. 기존 기대값인 성공 2건 + 예산 거부 1건은 유지했다. 사용자 답변: “fixture만 조정하고 복구 기대값 유지”.
- C7과 충돌하던 optional guidance 보존 테스트 두 건은 원문 보존/guidance 제외 계약으로 변경했다. B계층의 report/repetition/reasoning/read recovery 정책 기대값은 바꾸지 않았다.
- 이전 기준선 Python 16실패 중 11건은 Windows 기본 subprocess 인코딩(cp949), 1건은 테스트 중 HEAD 변경에 따른 package reproducibility였다. UTF-8 환경을 명시하고 최종 전체 검증 중 HEAD와 산출물을 고정했다.
- 나머지 기준선 문제는 실제 구현/문서를 고쳤다: worktree의 `.git` 파일과 저장소 marker를 우선하는 경로 탐색, repeat 응답의 `no new information` 호환 문구, 공개 문서의 개인/엔진 설치 절대경로를 portable alias로 교체. 테스트를 완화하지 않았다.
- 최종 Python 1차 실행에서 새 문서 링크의 패키지 누락 1건을 확인했다(1486 통과/1 실패/41 제외). 문서와 evidence JSON을 명시적 portable allowlist에 추가했고, 기존 패키지 검사에도 두 파일의 포함 기대값을 추가했다. 후속 검사에서 Git 미추적 문서가 제외되는 조건도 확인하고 스테이징한 뒤 재검증했다. 마지막 전체 실행은 1487 통과/0 실패/41 제외(266.92초)이며 패키지 링크·파일 포함·ZIP 재현성이 모두 통과했다.
- 시스템 메시지 구조나 prefix cache 정책은 바꾸지 않고 해시 변화만 측정한다. 새 runtime 설정 키는 추가하지 않았다.

## 6. 잔여 한계와 사람이 수행할 단계

W7 1회차(`1793075034058`): 수정 턴 기능 통과. 남은 비용·예산 결함 D1–D9는 [v4 계약](Harness_Compactor_Tool_Contract_v4_20261027.md)에서 다룸.

- claim과 source 사실의 의미 매칭 정밀도는 이번 범위 밖이다. 기존 휴리스틱의 오매칭 사례를 fixture로 고정했으며 사실 증명으로 승격하지 않는다.
- 반환 text cache는 제한돼 있다. 캐시 일부를 잃으면 ‘반환 안 됨’ 공지를 억제하고 incomplete로 남긴다. bounded span cache 밖의 사실도 unknown이며 재구성/원문 없이는 정확한 인용 검증을 확정하지 않는다.
- 이전 checkpoint 타깃이 경로만 가진 경우 project 권위가 새로 생기지 않는다. 실제 scoped sourceKey가 있을 때만 프로젝트를 특정한다.
- `firstFocusReads`와 `restoreRequiredReads`는 측정용이다. 기존 `focusedReads` 기반 복구 진행 정책을 C8에서 바꾸지 않았다. 공개 `repeatRestores`는 숨겨진 좁은 범위의 반복까지 별도로 측정하며, 복구 진행용 내부 카운터는 기존 정책을 유지한다.
- 설치본의 성능 개선/실모델 완료율은 아직 미측정이다. W7은 동일 모델·프로젝트 snapshot으로 감사→수정 2턴을 3회 실행해야 한다.
- 사용자가 결정할 순서: 짧은 PATH와 `UPDATE_NO_PAUSE=1`로 `UPDATE.bat --dry-run` → 실제 설치 → LM Studio 재시작 → `direct_runtime_identity.loadedBuildId` 확인 → W7.
- W7 합격: mutation 호출 또는 `mutation_blocked_by_context`와 변경 미적용 고지. `unresolved_tool_intent` 종료는 불합격. budget denial, engine 오공지, 각 라운드 prefill 시간을 별도 비교한다.

빌드 식별자: `ef-build-v2-18c5f1e93c6bb49d292df81c9d62ebffd29a06565c20e6911a64e8864e89e1a8`. Node 24.17.0 / TypeScript 5.9.3 / SDK 1.5.0 환경에서 확인했다. 설치 후 확인할 값이며, 현재 설치본의 로드 상태를 확인했다는 뜻은 아니다.

## 7. 변경 이력

| 작업 | 커밋 |
|---|---|
| T0 반례 고정 | `26e2b1b` |
| T1 목표 | `4a45967` |
| T2 결정 권한 | `44f9eb7` |
| T3 예약 | `11e25e9` |
| T4 오류/재시도 | `8d314d0` |
| T5 capability SSOT | `3a53698` |
| T6 returned source | `8caaa43` |
| T7 핀 | `d19181d` |
| T8 계측/문서/최종검증 | 이 문서를 포함한 마지막 커밋 |
