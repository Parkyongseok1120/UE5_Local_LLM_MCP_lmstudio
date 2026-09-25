# fc5373 감사 후 개선 패치 인계

작성일: 2026-09-25. 사용자 요청은 첨부 감사 문서를 읽고 **코드 개선을 시작**하는 것이다. 첨부 문서가 자신의 감사를 `read-only`로 설명하는 문장은 문서 작성 당시의 증명 범위이며, 이번 사용자의 수정 요청을 제한하는 지시로 취급하지 않았다.

## 현재 판정

- **PASS — 빌드, 플러그인 345개 전체 회귀, native adapter 회귀, 합성 soak.**
- **BLOCKED — 이번 패치의 실제 LM Studio 38,912-token 검증 및 압축 이후 누적 380K 검증.** 2026-09-25 `lms ps --json` 결과는 `[]`였다. 이 패치로 실제 모델 생성/host prefill/cache 수치를 측정하지 않았다.
- 감사의 19개 지적을 전부 해결했다고 주장하지 않는다. 사용자 목표의 의미적 AC 검증과 장기 production stress는 아래 잔여 항목에 명시했다.
- 시작/현재 HEAD: `fc5373b0b62c4e07829cea5052f1e1afbacc3104` (`컴팩터 리팩토링 1`). 변경은 working tree에 있으며 새 commit/deploy는 없다.
- 제품 patch digest: `17f88b8978ebce3694f3bbfaa5814417ea6e3809059b7475954cfbd723301062`. 정의와 20개 변경 파일의 SHA256은 [patch-manifest.json](patch-manifest.json). Untracked 제품 소스·테스트도 포함한다. 보고서와 generated dist는 제외한다.
- 입력 문서: `C:/Users/sster/Downloads/fc5373_Complete_Audit.md`, SHA256 `fc7b7649cdf6a984853319c4e85d8e62fb7cf06c67ac900a68f1a5c47f8cc1cb`.

## 책임별 실제 변경

| 기존 문제/책임 | 수정 소유자와 결과 |
|---|---|
| LOW/hard 통과를 실행 가능으로 취급 | BudgetBroker가 source read 최소 예산과 post-tool reserve를 계산. ContextManager는 LOW/hard/next-read를 구분하며 orchestrator가 실패한 후보의 dispatch/commit을 막는다. 다음 read만 불가능한 경우 기존 FINAL_DELIVERY phase로 전환해 보존된 근거를 보고한다. |
| schema clamp 전 예약, 실제 maxBytes 누락 | BudgetBroker가 schema default/min/max를 먼저 적용하고 Unreal maxBytes를 제한한다. actualEnvelopeBytes/overrun/nextDispatchAllowed를 남긴다. 초과한 결과는 버리지 않고 다음 최종 입력을 다시 exact 측정한다. |
| provider별 payload/query 혼동 | 새 `src/evidence-identity.js`를 EvidenceManager와 WorkingContext가 함께 사용한다. provider+repository+workspace+project, query/symbol/object, hash/version, mutable diff를 구분한다. |
| archive 캡처/투영 중복, commit 의존 참조 정리 | WorkingContext가 stable request/result key를 공유하고 accepted input별 live/pending/manifest refs를 구분한다. 저장용 view를 따로 정리하며 원래 live cursor/body는 유지한다. |
| 읽기 허용과 read profile 불일치 | ToolCapabilityRegistry가 operation별 read schema를 만들고 ToolBoundary가 실제 action을 다시 검사한다. bounded mode에도 같은 profile을 쓴다. |
| raw 재계획 후 단일 도구로 축소 | RecoveryCoordinator가 기존에 허용된 source/archive read profile을 유지한다. |
| context/output 길이 종료를 정상 planning/작업 성공으로 취급 | RoundTransaction/round-loop는 정상 EOS/stopString만 완료 planning으로 인정한다. DeliveryController는 generationCompleted/reportDelivered/researchTerminated/objectiveSatisfied를 분리한다. 의미적 목표 검증이 없으면 taskCompleted는 false다. |
| 단계별 중복 계측 | ContextManager가 before projection / after projection / after compaction / metadata / final schema의 실제 fingerprint와 수치를 분리한다. 동일 round/model/schema/reserve의 exact 측정만 bounded cache에 재사용한다. |

새 제품 모듈은 `src/evidence-identity.js`, 새 검증기는 `scripts/measurement-contract.cjs`, 새 집중 회귀는 `test/audit-fc5373.test.cjs`다. 기존 phase, archive, range matcher, capability registry와 host approval을 재사용했다. Unreal C++/namespace나 native provider 구현은 변경하지 않았다.

## 감사 ID별 처리와 남은 한계

| ID | 이번 처리 | 확인 범위/잔여 사항 |
|---|---|---|
| H01 | 기존 27,255/43,614 수치의 제품 한계 해석을 철회 | 기존 96-token stress는 production handler/delivery와 조건이 다르다. 이전 trace는 보존한다. 수정된 production 기반 380K 장기 하네스/실행은 아직 완료하지 않았다. |
| H02 | visible assistant만 검사, 파일→정답 mapping, 중복 0, EOS/stopString 조건 도입. live harness는 native schema/runtime 사용 | 실제 모델 smoke는 미실행. 짧은 smoke가 장기 보존을 증명하지 않는다. ratchet script는 mandatory system invariant/입력 크기 검사로 명시적으로 범위를 줄였다. |
| B01 | LOW fit과 read liveness 분리. ref가 없을 수 있는 auxiliary archive의 싼 최소값으로 source starvation을 숨기지 않음 | 알려지지 않은 다음 동작에서는 최소 한 source read의 예산을 계산한다. 실제 선택된 요청은 별도 guard 예약이 필요하다. 임의 자연어 목표의 필수 read를 미리 식별하는 의미적 검증기는 없다. |
| B02 | native Unreal maxBytes 인식, 인수 제한, 실제 반환량 초과 표시, 후속 exact gate | bounded 인수가 없는 provider와 예상보다 큰 결과의 반환 자체는 막을 수 없다. 반환 증거는 유지한다. byte 회계는 exact token 수가 아니다. |
| B03 | schema 최대값/default 먼저 적용 | 두 작은 읽기가 배치 예산을 공유하는 회귀 포함. |
| B04 | canRun/disposition을 저장/dispatch에 연결 | 실제 handler에서 tokenizer 실패 시 act=0·commit=0 확인. next-read 부족 시 report-only 전환 통합 검사 포함. |
| E01 | 실제 Unity `workspace_file_observation` 및 trusted paired generic observation 처리 | native Unity `createRuntime().call('read_file')` 결과를 recovery까지 연결한 회귀. duplicate/orphan은 production 경로에서 성공 evidence가 아니다. |
| E02 | 역사 novelty와 현재 working evidence 복원을 별도 계산. duplicate barrier는 최종 model input 기준 | `currentInputAvailabilityGain`은 실제 token-level after-input gain을 측정하지 않아 null을 유지한다. 대신 `restoredWorkingEvidenceUnits`를 사용하며 둘을 동의어로 기록하지 않는다. |
| E03 | 공통 identity/query/version adapter를 실제 capture/project/observe 경로에 연결 | mutable fingerprint는 verified source version으로 표시하지 않는다. 모든 editor-specific payload의 실제 transport 검증은 남아 있다. |
| E04 | capture와 반복/shifted projection의 같은 결과는 하나의 ID. 다른 call ID는 다른 provenance | 동일 provider ID+args+body가 서로 다른 실행에서 완전히 반복되면 content archive key를 공유한다. 실행별 dispatch trace와 content archive를 구분해야 하며 archive만으로 모든 호출 발생 횟수를 복원한다고 주장하지 않는다. |
| E05 | 저장용 sanitization, accepted-input ref 정리, pending 누락 시 commit 전 거부, 원본/저장 측정 대상 구분 | 140회/8-record 회귀 통과. 소스 근거가 모두 계속 live여서 quota를 초과하거나 pending 결과가 후보에서 사라지면 명시적으로 실패할 수 있다. 이를 조용한 GC/성공으로 바꾸지 않았다. |
| E06 | 원본 실패, archive resultStatus, errorCode, SDK `{error:...}`를 일관되게 실패 처리 | projection/archive read/seed/observe 및 retry fingerprint를 검사했다. |
| T01 | prefab read/scene list/tests status 등 operation profile 보존, write guard 거부 | 설치 SDK의 remote implementation closure 소스를 별도 reviewer가 확인했다. 실제 Editor 작업 검증은 아님. |
| T02 | raw 한 이름이 후속 연구 전체 profile을 축소하지 않음 | 기존 raw diff 및 source+archive 재계획 통합 회귀 갱신/통과. |
| T03 | contextLengthReached/maxPredictedTokensReached/unknown은 planning commit 불가 | 반환된 tool evidence는 유지. |
| T04 | 정상 보고서 종료와 objective 충족 분리 | 기본 objectiveSatisfied=null, taskCompleted=false. 의미적 AC 없이는 true로 승격하지 않는다. |
| O01 | 잘못된 ObjectiveState 명칭을 ReadExecutionScope로 수정 | **부분 처리**. 기존 objective text continuity는 유지되지만 금지 evidence 종류/미완료 목표/AC를 검증하는 독립 ObjectiveContract는 추가하지 않았다. |
| O02 | 단계별 측정·fingerprint·RPC count/ms·동일 후보 캐시 | cost scope는 `round_input_assembler`; preparation/semantic summary/host 내부 비용을 포함한 전체 비용으로 해석하면 안 된다. |
| O03 | chars/4는 diagnostic 추정으로만 사용, exact/실제 context length 없으면 nonlegacy 실행 차단 | 한국어/emoji/escaped JSON 회귀. legacy/observeOnly는 명시적 호환 예외다. |

## Low/High 계산식과 수치

`C=loaded context length`, `G=해당 model call의 generation reserve`, `S=safety`, `F=exact mandatory floor`, `M=현재 노출된 source read의 최소 예약량`이다. Source reader가 있으면 ref가 없을 수도 있는 archive reader는 M을 낮추지 못한다. Source가 없고 archive-only이면 archive envelope의 최소 유효 예산을 사용한다. 최종 선택 요청의 예약은 dispatch guard에서 다시 검증한다.

```text
HARD = max(0, C - G - S)
R = read가 있으면 max(M, min(4096, floor(HARD / 8))), 아니면 0
P = read가 있으면 G, 아니면 0
HIGH = min(configuredTrigger, max(0, HARD - R - P))
configuredLOW = min(configuredTarget, floor(HIGH * 0.75))
effectiveLOW = max(configuredLOW, F)
nextActionFit = finalExactInputTokens + P + M <= HARD
compactionSucceeded = exact && finalExactInputTokens <= effectiveLOW && finalExactInputTokens <= HARD
canRun = hardFit && nextActionFit && (!compactionRequested || compactionSucceeded)
```

실제 모델 측정이 아닌 **정책 probe** (`C=38,912 / G=8,192 / S=2,048`, target18,000/trigger22,000, floor0):

| Profile | M | R | HARD | HIGH | LOW |
|---|---:|---:|---:|---:|---:|
| read 없음 | 0 | 0 | 28,672 | 22,000 | 16,500 |
| Unity byteBudget read | 2,048 | 3,584 | 28,672 | 16,896 | 12,672 |
| Unreal maxBytes read | 7,168 | 7,168 | 28,672 | 13,312 | 9,984 |

Unreal probe에서 input=floor19,000이면 effectiveLOW=19,000으로 LOW 자체는 만족하지만 nextActionFit=false다. 따라서 source read를 계속 진행할 수 있는 상태로 기록하지 않는다. [watermark-probes.json](watermark-probes.json).

`exact_templated_model_input`, provider 반환 UTF-8 bytes, SDK prompt/completion usage, backend `n_tokens`, cache/prefill은 서로 다른 metric이다. 이번 작업에서는 backend n_tokens/cache/prefill을 수집하지 않았다.

## 회귀 및 soak

| 명령 | cwd | exit | 결과 |
|---|---|---:|---|
| `npm test` | lmstudio-context-compactor-plugin | 0 | build 포함, **345 pass / 0 fail**. 새 집중 검사 21개와 handler 검사 1개가 이 수에 포함됨. 별도 검사 숫자를 가산하지 않는다. |
| `node --test --test-name-pattern='context budget measures a narrowed\|final raw git_diff repair\|aggregate first-consumer fit\|B04/O03' test/prediction-loop.test.cjs` | 동일 | 0 | 관련 통합 4개 pass, 전체345에 포함. |
| `npm test` | lmstudio-unity-mcp | 0 | 34 pass / 4 skip / 0 fail |
| `npm test` | lmstudio-unreal-agent-mcp | 0 | 247 pass / 1 skip / 0 fail |
| `node scripts/astra-ratchet.cjs` | lmstudio-context-compactor-plugin | 0 | 합성 tokenizer, 30 cycles / 240 fixture rounds |
| `node --check scripts/astra-live-flow.cjs` 및 ratchet script | 동일 | 0 | 문법 검사. 실제 모델 성공 아님. |
| `python .../evidence-first-code-audit/scripts/validate_evidence_packet.py artifacts/fc5373-audit-fixes/audit.json` | 저장소 root | 0 | 6 claims, errors0/warnings0 |
| `git diff --check` | 저장소 root | 0 | whitespace 오류 없음 |
| `lms ps --json` | 저장소 환경 | 0 | `[]`; live 검증 BLOCKED |

Ratchet 최소 첫 3-cycle baseline은 **5,675 → 5,670 → 5,670**, 30번째도 **5,670**이다. 이 fixture의 LOW=6,000/HIGH=9,000, cycle2 이후 HIGH 전 입력은 약24,578, 전체 baseline spread=5였다. 이것은 합성 counter를 사용하는 동일 증거 반복 검사이며 **실제 LM Studio 38K 데이터 보존 또는 380K 고유 정보 보존 결과가 아니다**. [ratchet-soak.json](ratchet-soak.json).

별도의 archive lifecycle 검사에서 140회 모두 pending→exposure→sanitized commit→restore가 성공했고 8-record 제한 안에서 유지됐다. Unity skip 4개는 플랫폼 파일명/Git 조건 2개와 외부 Roslyn 조건 2개, Unreal skip 1개는 POSIX 경로 검사다. 정확한 skip 이름은 원 로그에 있다.

첫 전체 실행의 실패, stricter budget 변경 중 발견한 통합 실패도 로그에 남겼다. 최종 판단 근거는 [test-verification.log](test-verification.log)다. [audit.json](audit.json)은 증거 기록 형식 검사도 통과했다.

## 다음 runtime 검사와 rollback

수정한 짧은 smoke 명령은 plugin cwd의 `node scripts/astra-live-flow.cjs`와 `node scripts/astra-live-flow.cjs --git`이다. 로드된 모델을 사용하며 기본 ID는 `swift-qwen3.8-27b`, `ASTRA_MODEL`로 지정할 수 있다. 임시 Unity project에만 oracle 파일을 만들고 실제 native runtime/schema로 읽는다. Git 모드는 임시 저장소의 pinned SHA를 사용한다. 사용자 prompt에는 oracle 값을 재주입하지 않는다. GUI 설치/실제 Editor/MCP transport는 이 smoke의 검증 범위에 포함되지 않는다.

누적380K는 압축 이후 **실제로 제출한 exact input의 합계**로 집계해야 한다. 같은 prefix 재전송도 포함되므로 고유 정보380K 보존과 같지 않다. 실제 장기 실행에서는 generation reserve 일치, visible final mapping, source failure/duplicate/retention, 여러 compaction cycle, archive quota/restart, SDK/host dispatch 및 prefill/cache를 함께 기록해야 한다. 현재 과거 `stress-to-380k.cjs`는 역사 재현 자료이며 승인된 production 장기 검증기로 사용하지 않는다.

Rollback 기준은 이번 작업의 시작 commit **fc5373b0b62c4e07829cea5052f1e1afbacc3104**이다. patch-manifest의 제품 파일만 이 기준과 비교해 역적용하고 새 제품 파일 3개는 별도로 확인해 제거한다. 전체 저장소 reset은 필요 없다. 기존 `artifacts/astra-full-refactor`의 역사 로그/측정값은 보존한다. 활성 GUI 설치와 native Editor state는 이 패치에 의해 바뀌지 않았다.
