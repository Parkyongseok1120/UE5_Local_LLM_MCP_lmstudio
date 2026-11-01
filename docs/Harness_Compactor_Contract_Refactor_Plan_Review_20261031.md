# 하네스·컴팩터 전체 계약 감사 계획 — 재조사 결과와 보완안

- 작성일: 2026-10-31, Asia/Seoul.
- 검토 대상 문서: 「하네스·컴팩터·아키텍처 기능 전체 계약 감사와 리팩토링 계획」(2026-10-28, codex worktree의 `docs/Harness_Compactor_Contract_Refactor_Plan_20261028.md`).
- 확인 수준: 모든 항목은 SourceVerified(소스 줄 대조)다. 테스트 재실행·재현 fixture·실제 모델 실행은 하지 않았다.

## Context

사용자가 작성한 「하네스·컴팩터·아키텍처 기능 전체 계약 감사와 리팩토링 계획(2026-10-28)」이 타당한지 코드 기준으로 다시 확인하고, 계획에 없는 추가 수정사항을 찾는 작업이다.

- 검토 대상 코드: `${WORKTREES}/harness-output-contract-fixes/UE5_Local_LLM_MCP_lmstudio` (`codex/harness-output-contract-fixes` @2b13b38 + **미커밋 수정 42개 파일, 미추적 신규 파일 약 25개**). 이 worktree(`claude/harness-compactor-contract-audit-4fb294` @20ae99e)에는 아키텍처 구현이 없다.
- 방법: 결함 C1–P1을 하나씩 소스 줄 단위로 대조하고, 같은 경계 안에서 추가 결함을 찾았다. 테스트 재실행이나 실제 모델 실행은 하지 않았다(빌드 단계가 생성 파일을 다시 쓰기 때문).
- 아래 경로 표기: `src/` = `lmstudio-context-compactor-plugin/src/`.

## 1. 총평

방향은 맞다. 정본과 파생 view의 분리, typed part, 결과형 note, 인과 순서 기반 최신성, 저장 round-trip은 모두 실제 코드 결함과 대응한다. 결함 13개 중 11개는 정확하고, 1개는 범위가 더 넓으며, 1개(K4)는 일부만 맞다. 다만 아래 다섯 가지는 고쳐야 한다.

1. **기준선이 커밋돼 있지 않다.** 수정 42개 파일과 신규 파일, CI `guidance:check` 단계가 모두 미커밋이다. R0의 "baseline 기록"만으로는 롤백 기준이 되지 않는다. 리팩토링을 시작하기 전에 현재 상태를 브랜치에 커밋하는 단계가 R0의 첫 항목이어야 한다.
2. **작은 P1 결함이 대형 단계에 묶여 있다.** O1(RuntimeVerified), N2, N1의 null→clear 경로, P1 wiring, K3는 각각 몇 줄 단위로 독립 수정이 가능하다. 그런데 R2/R3/R4/R5/R6 안에 들어가 있어서 대형 리팩토링이 끝날 때까지 실사용 피해가 계속된다. R0 다음에 **R0.5 핫픽스 묶음**을 둘 것을 권한다(4절).
3. **C1의 범위가 문서보다 넓다.** 과거 optional이 부풀린 측정값은 `enforceLowWater` 하나만이 아니라 세 곳에서 소비된다. projection/pin 해제 게이트(`round-input.ts:207-232`), `deepenLowWater`(`:252-253`), low-water 판단이다. R3는 세 게이트 모두에 같은 baseline을 쓰도록 명시해야 한다.
4. **K4는 일부 정정이 필요하다.** bundled catalogHash 변경은 이미 `syncBinding`에서 stale 처리되고 L06 테스트도 있다(`architecture-round.ts:29`, `architecture-state.ts:34-42`). 결함은 **RAG origin 패턴의 hash/generation 변경**에 한정된다. 오히려 `syncBinding:39`가 executionId·scopeRevision 변경에도 패턴 의존 판단을 stale로 만드는 과잉 무효화가 있다.
5. **"압축 후 재증가"의 직접 원인 하나가 빠져 있다.** 아래 X1(architecture 단독 part의 매 라운드 누적)이 문서의 §2 측정과 가장 직접적으로 연결되는데, 계획에는 없다.

## 2. 기존 결함 판정 (소스 대조)

| ID | 판정 | 보완할 점 |
|---|---|---|
| C1 | 정확, 범위 확대 | 부풀린 측정을 projection 게이트와 deepenLowWater도 소비한다. `:198-199`, `:250-251`의 주석("optional은 source eviction을 유발하지 않음")과 실제 동작이 모순된다 |
| C2 (a)(b)(c) | 정확 | 근본 원인: handle과 view에 `modelInputId`가 섞여 들어간다(`architecture-input.ts:3,7,10,18`). 그래서 같은 의미여도 텍스트가 매 라운드 달라지고, 텍스트 기반 dedup이나 `shown.includes(part)`가 원리적으로 성립할 수 없다. 발급 판정은 `includes(view.text)` 세 곳에서 한다(`round-input.ts:481,547,560`). `:560`은 선택된 view가 아니라 **아무 variant**나 확인한다 |
| C3 | 정확 | `targetMet`(`prediction-loop.ts:606-607`)과 `mandatoryFloorExceedsTarget`(`round-input.ts:598-599`)가 둘 다 설정값과 비교한다. 코드 기본값은 18000이다(46000은 사용자 설정) |
| O1 | 정확 | 미등록 frame이 **3종**이다. `[Architecture reference data…]`, `[Prior assistant continuity note v1]`(`continuity-model-notes.js:20,411`), `[Task continuity data…]`(`continuity-memory.js:315`). 헤더 문자열은 약 10개 파일에 중복돼 있다(아래 X7) |
| A1 | 정확, 범위 확대 | 배열 순서가 최신을 정하는 지점이 ledger 말고도 두 곳 더 있다. `architecture-input.ts:7`의 `evidence.slice(-6)`, `architecture-state.ts:52`의 `changes.at(-1)`이다. 기존 `uniqueLatest`(`tool-exchange-index.js:55-61`)와 `reference-context.ts:478-486`의 동일 배치 중복 제거는 새 경로가 쓰지 않는다 |
| A2 | 정확, 부분 완화 | `configuredEngine:"unreal"`이면서 identity가 비어 있는 경우(`tool-scope.ts:137-138`)에도 같은 문제가 있다. 단일 프로젝트로 기록된 note는 `reconcileStoredNote`(`continuity-model-notes.js:344`)가 일부 막지만, evidence 목록은 분리되지 않는다 |
| N1 | 정확, 빈도 높음 | 엣지 케이스가 아니다. `invalidateUnknown`이 **architecture 비활성 라운드마다**(`architecture-round.ts:28`), 그리고 `returned_target_mismatch`가 지속되는 라운드마다(`:42`) 호출된다. 추가로 `invalidate()`가 availability 문자열을 바꾸면 ref당 3–5자가 늘어 6000자를 넘을 수 있다. null이 `replaceActiveNote` → `mergeArchitecture(null,…)`로 들어가면 일반 note가 지워진다(확인함) |
| N2 | 정확, 범위 확대 | v2만의 문제가 아니다. 한국어 위주 v1 note도 1500자 근처에서 4096바이트를 넘는다(기존 결함) |
| K1 | 정확 | 추가: `build_rag_index.py:103-104`는 manifest catalogHash를 export가 아니라 inventory에서 쓴다 |
| K2 | 정확 | 추가: `evidence-manager.ts:182`는 도구나 capability를 거르지 않는다. trusted 결과에 `matches[]`만 있으면 rag 패턴으로 수용한다 |
| K3 | 정확, 역방향 사례 있음 | 예산 절단으로 `matches=[]`가 되면 `matchCount>0`이어도 `no_match`가 된다(`direct_rag_search.py:223-227`) |
| K4 | 부분 정확 | 1번 총평 참조 |
| P1 | 정확 | `round-input.ts:430`이 `complete`를 넘기지 않는다. 그래서 잘린 목적에서도 관찰 facet만으로 패턴을 최대 2개 선택한다 |

## 3. 계획에 없는 추가 결함

### 입력·압축·출력 (R2/R3에 추가)
- **X1 (P1)** architecture 단독 part(derived 데이터가 없을 때, `design-guidance.ts:301`)는 문제가 겹친다. `[Architecture reference data`로 시작해서 `privateDataParts`에도 `budgetParts` Derived 제외에도 걸리지 않고, `modelInputId` 때문에 `shown.includes(part)`도 맞지 않는다. 결과적으로 **consolidation 전까지 매 라운드 새 사본이 쌓이고**, `supersededChars`를 키워 `data_consolidation`을 강제한다. §2.1의 "이전 잔여 runtime 자료 +4,920자"에 기여할 가능성이 크다. 다음 실측 manifest로 확인이 필요하다.
- **X2** 후보 평가 중에 `guidanceRetention`이 바뀐다(`round-input.ts:460-461,490-510`). 라운드가 pressure로 반환되거나 throw되면 보내지 않은 입력의 retention이 남는다. 두 번째 `selectReferences()`(`:552`)는 첫 호출이 쓴 `kept`를 `reuse`로 읽는다. `kept.tokens`는 쓰기만 하고 읽는 곳이 없다. 계획 §5.2의 원칙은 맞으므로, 이 지점들을 수정 대상으로 명시한다.
- **X3** `:544`의 최종 재조립(pending=false)은 admission ceiling으로 다시 검사하지 않는다. `remainingTokens<0`만 확인한다. 또 optional admission은 mandatory floor 0으로 watermark를 계산하지만(`context-manager.ts:667,690`) low-water는 측정된 floor를 쓴다(`:745`). 두 개의 서로 다른 "low"가 공존한다. R3의 단일 목표 정의에 포함해야 한다.
- **X4** 발급이 취소되면(`issued` undefined) 패치가 이벤트 없이 버려진다(`architecture-round.ts:56`). 이때 모델은 옛 handle이 담긴 view를 계속 보므로 쓸 수 없는 ref를 위해 컨텍스트를 소비한다.
- **X5** 사용자 메시지에 runtime 헤더가 들어 있으면 해당 part가 private에서 빠진다(`context-manager.ts:360-362`). 그러면 echo 필터도 꺼진다. 사용자 인용 보존 요구와 충돌하지 않는 판정 기준(origin)이 필요하다. §5.1의 origin 필드로 해결한다.
- **X6** telemetry가 실제와 다르다.
  - `design_guidance_input.outcome:"included"`는 이후 dedup으로 탈락해도 그대로 남는다.
  - `omittedReasons`는 항상 `shared_admission`이다.
  - `admittedPatternIds`에는 RAG ref가 섞이지만 `requestedPatternIds`는 bundled만 센다.
  - `postCompactionInputTokens`에 optional이 포함된다.
  - `output_recovery_scheduled.partialReportIncludedInInput:true`가 하드코딩돼 있다.
  - `addedTokens`의 base와 candidate가 서로 다른 `deferReferenceData` 상태로 측정된다(`context-manager.ts:519`).
- **X7** 내부 frame 헤더 정의가 하나의 registry에 모여 있지 않다. `internal-data-contract.js`, `internal-data-output.ts`, `context-manager.ts`, `continuity-assistant-evidence.js:88`, `review-register.ts:116,191`, `review-patterns.js:6`, `continuity-model-notes.js:20,459`, `read-ledger.js:553`, `budget-broker.ts:337`에 흩어져 있다. R2의 공통 registry 작업 범위를 이 목록 전체로 정한다.
- **X8 (경미)** sticky retention의 `kept.instruction`에 `ARCHITECTURE_BASELINE`이 붙은 채 남는다. 그래서 scope가 ambiguous로 바뀌어 deferred되거나 finalizing인 라운드에서도 architecture 지침이 계속 실린다(`round-input.ts:438,457,470`). retention key에 architecture 활성 여부가 없기 때문이다.
- **X9 (경미)** measurement cache key에 `contextLength`와 `safetyMarginTokens`가 없다. 실행 도중 모델을 재로드할 때만 영향이 있다.

### 출처·상태·note (R1/R4에 추가)
- **Y1** sourceKey를 파생하는 방식이 둘이다. ledger는 `projectRelativePath || path`(`read-ledger.js:203`)를 쓰고, `architectureSources/Changes`는 `value.path`(`evidence-manager.ts:130,170-171`)를 쓴다. project 필드의 fallback도 다르다. 두 값이 다른 provider에서는 **완료된 write가 기존 read 판단을 무효화하지 못한다.** 실제 provider가 그렇게 반환하는지는 미확인이다. 경로 정규화도 4곳이 서로 다르다(`evidence-identity.js:13-16`, `read-ledger.js:31-33`, `reference-context.ts:87-91`, `continuity-model-notes.js:183-188`). R1은 sourceKey 생성 함수를 하나로 정해야 한다.
- **Y2** 한 라운드 안에서 자기 도구 결과 때문에 `engineIdentityKey`가 바뀌면(`architecture-round.ts:40`) 그 라운드의 모든 patch가 `stale_binding`으로 거절되고 판단 전체가 unknown이 된다. 정상 경로로 다시 current가 될 방법이 없다.
- **Y3** `reconcileStoredNote`의 null은 원인이 셋이다(objective 변경, project 불일치, 크기 초과). 그런데 모두 state close로 처리된다(`architecture-round.ts:18`). N1의 결과형과 같은 방식으로 원인을 나눠야 한다.
- **Y4** note store는 rename이 성공한 뒤에도 prune 오류(Windows EBUSY/EPERM, ENOENT)가 나면 `false`를 반환한다(`continuity-model-notes.js:506-517`). 그래서 저장됐는데 `atomic_write_failed`로 보고된다. rename 전 fsync도 없다.
- **Y5** history에는 reconcile된 note가 저장되지만, window는 reconcile 전 `activeNote`를 유지한다(`note-persistence.ts:59-72`). 클래스 주석(`:16-19`)과 어긋난다. 또 v2 history가 거절되고 v1 window를 채택한 경우 `rejectedCount:0`으로 기록된다.
- **Y6** 읽은 적 없는 키에 `invalidateReturnedSource`를 호출하면 `{invalidated:true}` 항목이 생긴다(`read-ledger.js:145-146`). 이 항목이 128개 상한을 차지해 실제 관찰을 밀어내고, `returnedFactsIncomplete=true`로 만든다.
- **Y7 (잠재)** `capabilityFor(tool)`를 request 없이 호출한다(`architecture-round.ts:46-47`). 그래서 action 기반 도구의 mutation을 놓친다. 지금은 Unreal 전용이라 드러나지 않는다.
- **Y8 (경미)** 복원할 때 decision revision은 1부터 다시 매기지만 tombstone은 옛 번호를 유지한다(`architecture-state.ts:15`). 순서와 telemetry만 영향을 받는다.

### RAG·패턴 (R5에 추가)
- **Z1** lite refresh가 이전 `raw_guidelines.jsonl`을 currency 확인 없이 다시 게시한다(`direct_rag_refresh_transaction.py:66-69`). installer는 `--pattern-export`를 넘기지 않는다(`installer/direct_rag_build_steps.py:37-45`). 그래서 "수집 생략"과 "패턴 0개"가 똑같이 `no_pattern_corpus`로 보인다.
- **Z2** PatternRef의 `mismatch`/`version_matched`를 대입하는 코드가 없다. 그래서 `architecture-state.ts:81`의 mismatch 검사는 죽은 코드다. RAG ref는 bundled catalogHash와 비교하지 않는다.
- **Z3** export 최상위 `baseline` 텍스트가 catalogHash에 포함되지 않는다(JS·Python 모두).
- **Z4** `npm test`의 prebuild가 생성 파일을 다시 쓴다. 그래서 C07 테스트(`pattern-catalog.test.cjs:24-26`)는 항상 통과한다. 실질 검사는 미커밋 CI의 `guidance:check`뿐이다. 정렬 방식도 다르다(JS `localeCompare` vs Python `sorted`). 지금은 ID 정규식 덕분에 결과가 같지만 Python 쪽은 ID를 검증하지 않는다.
- **Z5** `load_pattern_catalog`가 try 밖에서 `KeyError`/`AttributeError`를 던질 수 있다(`architecture_pattern_catalog.py:35,65,76`). 그러면 `ensure_engine_inputs` 전체가 중단된다.
- **Z6** 테스트 공백: raw 본문 변조, chunk read identity, mixed doc_type 상태, plugin의 RAG origin 패턴을 다루는 테스트가 하나도 없다.

## 4. 계획 수정안 (문서에 반영할 변경)

1. **R0에 추가**
   - 현재 미커밋 구현을 기준선 커밋으로 고정한다(codex 브랜치 또는 별도 baseline 브랜치).
   - X1·X2·Y1·Y4·Z1 재현 fixture를 실패 대응표에 추가한다.
   - 메모리 기록대로 Windows에서는 `npm test`가 PATH 길이 때문에 실패한다. 검증 명령을 `node scripts/run-tests.cjs` 직접 실행으로 적는다.
2. **R0.5 핫픽스 신설** (각각 독립적이고 작은 변경이며, 이후 R단계가 다시 쓸 수 있는 형태)
   - O1/X7 일부: 미등록 frame 3종을 기존 `internal-data-contract.js`에 등록하고 output/history sanitizer에 연결한다.
   - N2: 읽기 cap을 writer가 허용하는 최대 envelope 바이트로 맞추고, write 시 바이트를 검사한다.
   - N1: `invalidateUnknown`을 멱등하게 만들고, `mergeArchitecture` null이 `replaceActiveNote`에서 clear되지 않고 이전 note를 보존하게 한다.
   - P1: `complete`와 reason을 `selectArchitectureContext`까지 전달한다.
   - K3: pattern hit 수를 기준으로 판정한다.
   - Y4: prune 오류를 저장 결과에서 분리한다.
3. **R1**: Y1(sourceKey 단일 생성), A1 범위 확대(slice(-6), at(-1)), Y2(라운드 내 engine 변화 처리), Y6(ledger 상한 오염)을 추가한다.
4. **R2**: handle에서 `modelInputId`를 빼고 `(scope, semantic revision)` 기반의 안정 label로 바꾼다. 입력별 발급은 manifest로 한다. X1·X4·X5·X7(전체 registry 이전)·X8을 추가한다.
5. **R3**: C1 세 게이트, X3(단일 low 정의, 최종 재조립 재검사), X2(retention 변경안 반환), X6 telemetry 정정을 추가한다.
6. **R4**: Y3(reconcile 결과형), Y5(window와 history의 같은 reconcile 결과), N2의 v1 한국어 사례를 추가한다.
7. **R5**: K4를 정정한다. RAG origin만 해당하고, syncBinding 과잉 무효화는 축소한다. Z1–Z6을 추가한다.
8. **§8 남은 불확실성**: "X1이 4,920자에 기여한 비중"을 추가한다.


## 검증 메모
- 인용한 file:line은 작성 시점의 codex worktree(미커밋 상태) 기준이다. 리팩토링 중 줄 번호가 바뀔 수 있다.
- R0.5를 구현할 때는 `lmstudio-context-compactor-plugin`에서 `npm run build` 후 `node scripts/run-tests.cjs`를 실행한다(PATH 문제 회피). Python은 `pytest tests/test_architecture_pattern_corpus.py`, 그리고 `npm run guidance:check`를 실행한다.

## 실측 후속 (2026-11-01)

R0.5 설치본으로 실행한 대화 `1793426512873`의 검증 결과는 [실행 검증 문서](Harness_Run_1793426512873_Verification_20261101.md)에 있다.
- 실제 실행에서 C1/C3(압축 직후 optional 재입장)과 C2/X1(runtime data 이전 사본 누적)이 재현됐다.
- 새 항목: R6 byteBudget floor가 provider 최소 envelope보다 작은 결함, mandatory floor 상승(21.3K→30.5K)의 구성 미계측, 엔진 미검증 주장과 보고서 내부 모순의 미표시. 해당 문서 4절의 (a)–(g)를 각 R단계에 반영한다.
