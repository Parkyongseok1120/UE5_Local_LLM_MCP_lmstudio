# Runtime 계약 오류 수정 및 구조 감사

최종 상태: **380K 누적량 PASS / 전체 동작 FAIL**. 설치된 GUI에서 첫 압축 이후 483,846 exact input tokens까지 측정했다. 기존 pending-evidence 예외는 재발하지 않았으나 baseline 상승과 recovery 횟수 제한에 의한 미완료 종료가 남았다. live 테스트 중 새로 드러난 정책 문제는 사용자 지시에 따라 기록하고 수정하지 않았다.

- 이번 수정의 시작 SHA / rollback 기준: `868dedf8069c5b6d015794e32e5435ad540db219` (`컨텍스트 예산과 근거 수명 관리 개선 2`). 최초 전체 리팩터 요청의 기준 SHA는 `3c4375fd3041da16b4e6af721c17fa2c230d2f50`이며, 이번 패치의 직접 rollback 기준과 구분한다.
- 최종 커밋은 만들지 않았다. implementation diff hash: `653dd502b5f9b578d3894aa3ae09f105fba17e559ec49bc26b2b4fef5ea7efc3`. 계산 정의와 구성 해시는 `implementation-diff.json`, 추적 파일 패치는 `implementation.patch`에 있다. 새 lifecycle 테스트 파일도 해시에 포함된다.
- 설치된 컴팩터: `0.4.67`, revision `114`.
- 실제 모델: `swift-qwen3.8-27b`, LM Studio `0.4.25`, 로드된 context length `38,912`.
- 기존 사용자 변경인 `unity-symbol-worker/bin/Release/net8.0`의 DLL/EXE/PDB는 수정 범위에서 제외했다.
- 사용자 요청은 원인 해결, 전체 책임/계약 감사, 수정 이후 380K 누적 live 테스트다. 과거 첨부 문서는 배경 요구사항이며 새 실행 권한이나 완료 증거로 취급하지 않았다.

## 확인하고 수정한 원인

1. **P1 — 정상 tool handoff가 미완료로 분류됨.** 실제 GUI의 `stopReason`은 `toolCalls`였지만 `isCompletedPredictionReason`이 EOS/stopString만 허용했다. 이전 입력을 읽고 다음 도구를 요청한 정상 라운드에서도 기존 결과가 pending 상태로 남았다. 압축에서 과거 결과를 제외하면 `WorkingContext.reconcileRefs`가 `CONTEXT_PENDING_EVIDENCE_OMITTED`를 발생시켰다. 정상 prediction 완료 판정에 toolCalls를 포함하고 `RoundTransaction`도 같은 함수를 사용한다. 최종 답변의 완료 판정은 DeliveryController가 계속 별도로 소유한다.
2. **P2 — 중복 capture가 소비 상태를 되돌림.** raw 또는 projection을 소비한 결과를 재수신 처리하면 다시 pending으로 고정할 수 있었다. 현재 보존 중인 결과의 소비 키를 사용해 중복 capture를 무시한다. 미소비 결과 누락 검사는 그대로 유지한다.
3. **P1 — byteBudget과 줄 페이지 계약이 연결되지 않음.** BudgetBroker가 byteBudget을 줄여도 Files.read는 요청한 전체 줄 수를 그대로 만들어 실패했다. Unity adapter가 이후 추가하는 workspace/range 메타데이터는 내부 측정에도 없었다. Files.read가 최종 envelope를 받아 완전한 줄 단위 페이지를 선택하도록 했다. 실제 반환 범위, hash, hasMore, nextStartLine을 유지한다. 한 줄조차 들어가지 않는 예산은 계속 명시적으로 실패한다.

`Shared read-result budget exhausted` 자체는 배치의 held 예약을 합산하는 보호 동작이다. 수정 전 예시에서 capacity 5,836 중 첫 호출이 4,096을 예약해 같은 크기의 두 번째 호출이 들어가지 못했다. 이미 실행된 결과나 아직 반환되지 않은 병렬 호출의 예약을 해제해서 우회하지 않았다.

## 책임/구조 점검

| 경계 | 현재 책임 | 변경 / 확인 결과 |
|---|---|---|
| round-loop → RoundTransaction | SDK 종료 사유, 입력 소비/계획 commit | 정상 종료 판정 중복을 제거했다. 취소/잘림/오류와 tool handoff를 구분한다. |
| EvidenceManager → WorkingContext | 관측 분류, archive/ref 및 첫 소비 상태 | 새 결과는 다음 소비자까지 고정한다. 실제 누락 guard는 유지한다. |
| BudgetBroker → ToolBoundary | 모델/반환 예산, dispatch 허용 | byte 추정과 exact templated input을 구분한다. 초과 결과를 버리지 않고 다음 모델 호출 전에 재측정한다. |
| Files → Unity adapter | 원문 페이지, 엔진/워크스페이스 응답 envelope | 엔진 정보는 adapter가 제공하고 Files는 전체 응답 바이트를 검사한다. 범위/해시/continuation을 보존한다. |
| Recovery → Delivery | 제한된 조사 복구, 최종/부분 보고 | 도구 호출을 최종 보고 성공으로 승격하지 않는다. |

**전체 구조가 충분히 단순하거나 모든 책임 분리가 끝났다고 판정하지 않는다.** PredictionLoop는 1,480줄 / 81,470 bytes, ContextManager는 820줄이다. PredictionLoop에 regular/emergency compaction 호출과 recovery candidate 조립·측정·예산 clamp가 남아 있다. ContextManager의 prepareWorkingInput과 enforceLowWater도 서로 다른 후보 탐색을 실행한다. 파일 크기 상한을 통과하는 테스트는 책임 분리의 증명이 아니다.

라이브 추가 관측:

- full tool catalogue에서 다음 읽기 예산 부족(`blocked_next_action`) 시 조회가 끝나기 전에 FINAL_DELIVERY로 전환됐다. 이후 출력의 raw tool intent를 통해 TOOL_REPLAN/READ_RECOVERY로 되돌아갔다.
- 초기 압축 후 입력은 `19,351 → 20,274 → 21,244 → 22,063`으로 증가했고 같은 실행에서 최대 23,507까지 상승했다. mandatory floor와 effective Low-water도 함께 증가했다. 최종 19회 측정은 아래에 기록한다.
- floorCandidate는 `maxCurrentTurnMessages: 0`이어도 생성된 continuity checkpoint와 최신 교환을 포함한다. 따라서 선택적 과거 요약까지 필수 floor로 계산될 수 있다. `finalExact <= effectiveLow`만 확인하고 baseline 안정성을 인증해서는 안 된다.
- LM Studio 로그의 `Received channelSend for unknown channel` 경고가 계속 관측된다. 기존 한 라운드 뒤 abort 방식과의 관계는 추가 증명이 필요하며, 치명적 예외와 구분해 기록한다.

## 변경 모듈

- `lmstudio-context-compactor-plugin/src/round-loop.ts`
- `lmstudio-context-compactor-plugin/src/execution-state.ts`
- `lmstudio-context-compactor-plugin/src/working-context.js`
- `shared-tool-core/files.js`
- `lmstudio-unity-mcp/src/server.js`, `src/catalog.js`
- 독립 lifecycle/페이지 테스트와 production handler 반복 압축 통합 테스트.
- version/manifest/lock/README 및 installer version 일치 검사.

PredictionLoop에는 오류를 덮는 recovery branch를 추가하지 않았다.

## 예산 계산 및 측정 의미

`C = 실제 로드 context`, `G = 이번 호출 generation reserve`, `S = safety margin`.

- `hardInputCeiling = max(0, C - G - S)`
- 읽기 노출 시 `expectedResult = max(minimumRead, min(4096, floor(hardInputCeiling / 8)))`
- 읽기 노출 시 `postToolOverhead = G`, 도구 없는 보고 시 0.
- `High = min(configuredTrigger, max(0, hardInputCeiling - expectedResult - postToolOverhead))`
- `configuredLow = min(configuredTarget, floor(High * 0.75))`
- `effectiveLow = max(configuredLow, measuredMandatoryFloor)`
- `nextActionFit = exactInput + postToolOverhead + minimumRead <= hardInputCeiling`

초기 GUI 값은 C=38,912, G=8,192, S=2,048, hard ceiling=28,672, expected result=3,584, High=16,896, configured Low=12,672이다. 첫 exact input/floor=14,643으로 floor 예외가 적용됐다. READ_RECOVERY는 G=4,096으로 바뀌므로 같은 설정에서도 다른 물 높이가 산출된다.

380K는 **첫 실제 압축부터 완료된 실제 모델 호출에 전송한 exact templated input의 합**으로 센다. backend `promptTokensCount`, 생성 토큰, semantic summary 호출은 별도 기록한다. prefill 시간(ms)을 토큰으로 환산하거나 cache/slot n_tokens를 모델 입력으로 간주하지 않는다.

## 검증 기록

| 명령 | exit | 결과 |
|---|---:|---|
| 수정 전 `node --test .../test/tool-round-lifecycle.test.cjs` | 1 | 정상 toolCalls 및 재고정 결함 재현 (`red-lifecycle.log`) |
| 수정 전 Unity 페이지 회귀 | 1 | response_budget_exceeded 재현 |
| compactor `npm test` | 0 | 349/349 PASS |
| Unity MCP `npm test` | 0 | 35 PASS, 4 SKIP |
| Unreal MCP `npm test` | 0 | 247 PASS, 1 SKIP |
| revision 변경 후 production handler 반복 압축 테스트 | 0 | PASS |
| evidence packet validator | 0 | 5 claims, errors/warnings 없음 |
| `lms dev --install -y` | 0 | revision 114 설치; 실제 GUI identity로 활성 확인 |
| `git diff --check` | 0 | PASS |
| 최초 전체 `python -m pytest -q` | 1 | 1,048 PASS / 19 SKIP / 기존 버전 기대값 불일치 1 FAIL (`test-python.log`) |
| 해당 installer 버전 테스트 재실행 | 0 | 1 PASS (`test-python-version-rerun.log`) |
| 최종 전체 `python -m pytest -q` | 0 | **1,049 PASS / 19 SKIP**, 156.73초 (`test-python-final.log`) |
| `npm run build` (0.4.67) | 0 | TypeScript build PASS (`build.log`) |
| 설치 revision identity 회귀 | 0 | 1 PASS (`test-identity.log`) |
| live 수집기 / 원문 비교 검사 | 0 | 계측 파일 생성과 원문 비교 완료; 제품 성공을 의미하는 exit code가 아님 |

정확한 명령, working directory, exit code는 `test-results.json`에도 남겼다. SKIP을 PASS에 합산하지 않았다. native Unity Editor 작업·Unreal Editor 작업의 live 검증은 이번 파일 조회 스트레스 범위에 포함되지 않는다.

## 실제 GUI 380K 스트레스 결과

새 GUI 채팅에 manifest 근거 기록과 packages-lock.json 전체 순차 조회(20줄, byteBudget 4,096, 도구 한 개씩)를 지시했다. 사용자의 요청대로 기존에 로드된 `swift-qwen3.8-27b` / context **38,912**를 그대로 사용했다. 컴팩터 revision 114와 Unity MCP만 재시작했고 모델을 교체하거나 컨텍스트 길이를 변경하지 않았다.

- 실제 채팅: `REDACTED_HOME\.lmstudio\conversations\1790316107098.conversation.json`.
- 최초 execution: `59236d0d-cc58-4216-98ac-0787dfb639f3`.
- 280/680줄에서 부분 보고로 멈춘 뒤 **정확히 “계속해”를 1회** GUI로 전송했다.
- 재개 execution: `b7cbcab6-c3d3-4f3d-b3fa-1d9da9031802`.
- 재개 후 520/680줄에서 같은 recovery 12회 제한으로 다시 부분 보고 종료했다. GUI의 중단 상태와 모델 idle을 확인했다. 추가 continue를 보내지 않았다.
- 모델은 종료 당시 `read_file startLine=521`을 raw tool text로 출력했지만 도구는 실행되지 않았다. 해당 텍스트를 성공한 조회로 세지 않았다.

| 측정 항목 | 최종값 / 판정 |
|---|---|
| 첫 압축 이전을 포함한 research/report exact input 합 | 589,354 |
| 첫 실제 압축부터의 누적 exact input | **483,846 — PASS** |
| 최초 380K 통과 지점 | 384,633 (`milestone-380k.json`) |
| 완료된 research/report 모델 호출 | 32회 |
| 실제 적용한 compaction을 포함한 모델 호출 | 19회 |
| 압축 후 exact input <= 해당 effective Low-water | 19/19 — 수치 조건 PASS |
| 동일 단계의 안정적 LOW 복귀 | FAIL — 아래 baseline 변화 및 floor 정책 참조 |
| 기존 CONTEXT_PENDING_EVIDENCE_OMITTED 예외 | 이번 live 실행 로그에서 0회 |
| 파일 도구 오류 | 0회 |
| 반환된 파일 페이지 검증 | 27개 모두 내용/해시/범위/continuation 원문 일치 |
| lock 파일 조회 범위 | 1–520 연속, 26페이지; 전체 680줄 중 160줄 미조회 |
| 아카이브 조회 | 1회, 281–300줄의 과거 근거 일부를 재조회; 원본 버전/범위 길이/읽기 전용 표기 일치 |
| 자동 조회 완주 및 요구된 최종 사실 보고 | **FAIL** |
| 초반 사실의 최종 답변 보존 | **BLOCKED** — 정상 최종 보고가 없어 의미 수준 검증 불가 |

`live-verification.json`의 원문 oracle은 검사기에만 제공했으며 모델에게 정답으로 재주입하지 않았다. 첫 페이지부터 이어진 범위와 해시는 검증했지만 이것을 모델의 사실 이해·장기 기억 전체 검증으로 확대하지 않는다. 아카이브 응답의 `coverageState=partial`, `fullRawProvided=false`, `sourcePageHasMore=true`를 유지하며 파일 전체를 복원했다고 주장하지 않는다.

### Ratchet: 3회를 넘는 19회 실제 재측정

첫 실행의 압축 후 exact input:

`19,351 → 20,274 → 21,244 → 22,063 → 21,162 → 21,956 → 22,571 → 23,303 → 23,507`

사용자가 요청한 1회 continue 이후:

`17,242 → 18,546 → 19,453 → 20,361 → 21,271 → 20,121 → 20,782 → 21,542 → 22,254 → 22,389`

모든 값은 해당 라운드의 effectiveLow 및 mandatoryFloor와 같다. 최초 실행에서는 READ_RECOVERY 단계 안에서 19,351→23,507로 4,156토큰 상승했다. 중간 회수가 있지만 안정된 LOW로 반복 복귀한 패턴을 입증하지 못했다. 새 사용자 턴에서 17,242로 감소한 현상은 자동 compaction cycle 성공과 구분한다. checkpoint 문자 수 상한이 있으므로 무한 증가를 증명한 것은 아니며, 장기 포화 상태도 이번 결과만으로 단정하지 않는다.

BudgetBroker는 중앙 계산을 수행하지만 ContextManager가 선택적 과거 요약을 포함한 후보 전체를 mandatory floor로 넘길 수 있다. 그 결과 `finalExact <= effectiveLow`가 통과해도 다음 읽기 headroom과 안정적 baseline이 확보됐다는 뜻이 아니다. 다음 개선은 floor의 필수 구성과 선택적 요약 예산을 구분하고, 실제 exact 측정으로 후보를 승인하는 책임을 ContextManager에 모으는 방향이어야 한다.

### 토큰 지표 구분

- 이번 누적값은 **실제로 호출된 최종 templated input**의 합이다. 미실행 후보의 countTokens, 단순 GUI 채팅 크기, backend cache/slot 토큰을 합산하지 않았다.
- 32개 research/report 호출 모두 `final exact input == backend promptTokensCount`였다.
- GUI는 종료 당시 대화 크기 `47,719/38,912`를 표시했지만, 실제 마지막 tool-free report input은 **12,246**이었다. GUI 표시값을 최종 모델 입력과 혼용하면 안 된다.
- 과거 사용자 예시의 26K/40K가 어떤 원시 로그 필드였는지는 이번 실행에서 역으로 단정하지 않았다.
- cache/prefill token count 및 backend slot `n_tokens`는 이 SDK 계측에서 알 수 없다. prefill **시간(ms)**만 수집했다. 미확인 항목은 BLOCKED/unknown으로 남기며 exact input으로 환산하지 않는다.
- semantic summary는 별도 6회 호출됐다. 모두 약 30초 후 `userStopped`/`invalid_finish_reason`, accepted=false였다. 코드의 30초 AbortSignal timeout 및 실제 elapsed/finish reason에 부합한다. 모델 사용량을 정확히 얻지 못한 이 호출들은 483,846에 포함하지 않았다. 따라서 380K 달성은 이 부가 호출에 의존하지 않는다.

| 실제 단계 | C | G | S | hard ceiling | expected result | post-tool reserve | High | configured Low |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 초기 전체 catalogue 조사 | 38,912 | 8,192 | 2,048 | 28,672 | 3,584 | 8,192 | 16,896 | 12,672 |
| 좁힌 READ_RECOVERY | 38,912 | 4,096 | 2,048 | 32,768 | 4,096 | 4,096 | 22,000 | 16,500 |

effective Low는 위 configured Low와 measured floor 중 큰 값이다. 첫 입력의 exact/floor는 14,643이었다. 전체 라운드별 값은 `live-rounds.csv`와 `live-gui.json`에 있다.

## 남은 구조 및 runtime risk

1. **P1, RuntimeVerified:** 일반 연속 조회에서 `auditResearchRounds`가 recovery 페이지 수 제한으로 적용되어 실제 hasMore=true여도 종료된다. 완료 여부를 DeliveryController가 false로 유지하는 방어는 정상이나, 작업 완주 정책은 미충족이다.
2. **P1, RuntimeVerified:** checkpoint를 포함한 mandatory floor 상승이 effective Low 상승으로 흡수된다. 현재 exact-low 검사만으로 ratchet 제거를 인증할 수 없다.
3. **P2, SourceVerified:** PredictionLoop의 입력 압축·복구 후보 생성·예산 조정 정책이 ContextManager/RecoveryCoordinator와 나뉘어 남아 있다. 1,480줄 orchestrator를 충분히 단순하다고 판정하지 않는다.
4. **Runtime 관측:** 의미 요약 6회가 모두 미채택됐다. 모델과 현재 30초 제한의 조합에서 의미 요약 경로의 효용은 검증되지 않았다. 실패 시 deterministic evidence 경로는 이어졌다.
5. **Runtime 관측 / 원인 미확정:** `Received channelSend for unknown channel` 경고가 계속 발생했다. SDK가 추가 로그를 억제하므로 총 횟수를 단정하지 않는다. `live-runtime-errors.log`에 보존했다. 실제 반환 페이지 손실은 발견하지 못했으나 모든 SDK channel의 무손실을 증명한 것은 아니다.

이번 수정은 정상 tool handoff의 입력 소비 계약과 파일 페이지 반환 계약을 해결했다. **전체 리팩터 완료 / 380K 무중단 정상 동작 / 완전한 사실 보존을 선언하지 않는다.**

## 아티팩트

- `before-errors.log`, `before-runtime-events.json`: 수정 전 실제 예외와 라운드 계측.
- `audit.json`: 근거, 반대 근거, 검증 수준과 책임 매핑.
- `test-*.log`: 자동 회귀 결과.
- `live-gui.json`, `live-gui-events.json`: 새 GUI 실행의 계측, 원문 페이지, 범위/해시, 결과. receipt/cursor는 결과 파일에서 제외한다.
- `collect-live.py`: 읽기 전용 계측 수집기. 모델 호출이나 GUI 조작을 하지 않는다.

## Rollback

시작 SHA의 수정 대상 소스로 되돌리고 컴팩터를 재설치한 뒤 해당 플러그인과 Unity MCP만 재시작한다. 사용자의 별도 작업인 UnitySymbolWorker 바이너리나 프로젝트/채팅 자료를 함께 되돌리지 않는다. 이 문서 작성 시점에 커밋/푸시는 하지 않았다.
