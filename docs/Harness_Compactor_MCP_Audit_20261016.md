# 하네스·컴팩터·MCP 도구 점검 보고서 (2026-10-16)

대상 브랜치 `Develop` (HEAD `be34cda`). **조사만 수행했고 코드는 수정하지 않았습니다.**

## 0. 범위·방법·근거 등급

| 대상 | 경로 |
|---|---|
| 컴팩터·하네스 | `lmstudio-context-compactor-plugin/src` (약 14,000줄) |
| 파일·실행 MCP | `lmstudio-unreal-agent-mcp/src`, `shared-tool-core` |
| 검색 MCP | `scripts/direct_rag_*.py`, `scripts/unreal_rag_direct.py` |

- 소스 정독과 함께, 저장소에 커밋된 실제 실행 로그(`artifacts/**/live-gui-events.json`, `events.json`, `turn-*-telemetry.json` 등)의 telemetry를 집계해 근거로 사용했습니다.
- 각 항목의 근거 등급은 다음과 같습니다.
  - **[확인]** 코드 경로와 실행 로그(또는 직접 실행한 확인 스크립트)로 둘 다 확인
  - **[코드]** 코드 경로로 성립을 확인했지만 실제 재현은 하지 않음
  - **[가설]** LM Studio 호스트 동작에 대한 가정이 필요해서 검증이 필요함
- 테스트 스위트는 실행하지 않았습니다. Unity MCP(`lmstudio-unity-mcp`)는 정밀 점검 범위에서 제외했습니다.

---

## 1. 요약 — 우선순위 Top 12

| # | 심각도 | 영역 | 한 줄 요약 | 근거 |
|---|---|---|---|---|
| C1 | 치명 | 재개 | "계속해줘", "계속해서 진행해", "이어서 해줘"를 **이어하기로 인식하지 못해** 목표가 바뀐 것으로 처리되고, 노트·미해결 항목이 폐기됨 | [확인] |
| C5 | 치명 | 유지력 | 워터마크가 절대값이라 컨텍스트가 커져도 22K에서 압축됨. 도구 스키마가 하한선의 60~70%를 차지해 실제 작업 공간은 2~4K 토큰 | [확인] |
| C6 | 치명 | 도구 호출 | 공유 배치 예산이 **바이트를 토큰으로** 계산하고 출력 예약을 두 번 빼서, 병렬 읽기가 "Shared read-result budget unavailable"로 거부됨 | [확인] |
| C4 | 높음 | 대화 손실 | 하이브리드 압축 후보가 모두 `recentCompleteTurns=0`이라, 압축이 일어나면 **이전 턴의 사용자·어시스턴트 원문이 전부** 짧은 체크포인트로 바뀜. 산문은 아카이브되지 않아 되돌릴 수 없음 | [확인] |
| C3 | 높음 | 재개 | 중단 후 재개 턴에서 압축이 미소비 결과를 버리면 `CONTEXT_PENDING_EVIDENCE_OMITTED` 예외로 **턴 전체가 실패** | [코드] |
| C2 | 높음 | 재개 | 사용자가 중단(abort)하면 continuity 노트를 저장하는 단계가 건너뛰어짐 | [코드] |
| P1 | 높음 | 성능 | semantic handoff: 로그 285건 중 **채택 0건**. 실제 호출의 중앙값이 30.0초로 타임아웃까지 소모 | [확인] |
| P2 | 높음 | 성능 | 동적 데이터(노트·작업진행·체크포인트)를 프롬프트 **앞부분**에 넣어 KV 캐시 접두부 재사용이 깨짐. prefill 중앙값 10.5초, p90 32초 | [확인]/[가설] |
| M1 | 높음 | MCP | agent 서버의 기본 응답 상한이 256,000자라 budget 필드가 없는 도구는 컨텍스트 전체보다 큰 결과를 낼 수 있음 | [코드] |
| C7 | 중간 | 재개 | 창(window) 복원이 플러그인이 재구성한 transcript 해시에 의존해 취약함. 실패 원인은 `unavailable` 하나로 뭉개짐. 경계 마커 하나만 어긋나도 그 채팅은 **영구히** 내구 복원이 비활성화됨 | [확인]/[가설] |
| C8 | 중간 | 재개 | 아카이브 TTL 24시간이 window 참조를 보호하지 못해 **다음 날 이어서 하면** 창 복원이 실패함 | [코드] |
| C9 | 중간 | 수명 | 아카이브 디렉터리 잠금이 경합 시 대기 없이 즉시 실패함. Windows PID 재사용 시 고아 잠금 파일이 **영구 잠금**이 됨 | [코드] |

---

## 2. 사용자가 보고한 증상과 원인 매핑

### 2.1 "압축 과정에서 기존 대화를 손실"
→ **C4**(이전 턴 전체 제거) + **C5**(작업 공간이 너무 작아 압축이 자주, 그리고 가장 공격적인 후보로 일어남) + **C11**(합성 메시지를 assistant 발화로 주입) + **P1**(의미 요약이 한 번도 성공하지 못해 결정사항이 보존되지 않음).

실행 로그의 `selectedCandidate` 분포(압축이 일어난 경우):

| 후보 | 건수 | 의미 |
|---|---|---|
| `mandatory_floor` | 45 | 최신 교환 1개와 필수 체크포인트만 남김 |
| `checkpoint_budget_0.25` | 21 | 체크포인트를 1/4(약 5,500자)로 축소 |
| `complete_exchange_cap_2` | 12 | 현재 턴 메시지 2개만 남김 |
| `complete_exchange_cap_4` / `_16` / `0.125` | 2 / 1 / 2 | |

→ 압축이 일어날 때마다 **거의 항상 가장 공격적인 후보**가 선택됩니다.

### 2.2 "공유 세션의 남은 토큰이 모자라 도구 호출이 불가능"
→ **C6**(배치 예산의 과대 차감) + **C5**(하한선과 도구 스키마) + **M1**(서버 응답 상한 불일치).
로그 근거: `validationState: denied_batch_budget` 35건, `retain_result_remeasure_before_next_model_call`(예약 초과) 19건, `blocked_next_action` 4건, `direct_context_budget_rescue` 6건, `context_execution_blocked` 2건.

### 2.3 "대화·생각이 끊긴 뒤 '계속' 지시 시 제대로 진행 못함"
→ **C1**(이어하기 문구 미인식, 가장 유력) + **C2**(중단 시 노트 미저장) + **C3**(재개 턴 예외 실패) + **C7/C8**(창 복원 실패 시 원본 기록으로 조용히 대체) + **C10**(생각 단계 컷오프 재시도가 같은 상한을 그대로 사용).

---

## 3. 컴팩터 유지력·연속성 버그 (상세)

### C1 [치명][확인] 이어하기 문구 인식이 좁은 정규식에 의존

- 위치: `lmstudio-context-compactor-plugin/src/continuity-text.js:53` `looksElliptical()`
- 직접 실행해 확인한 결과(현재 코드):

| 입력 | 이어하기로 인식? |
|---|---|
| `계속`, `계속해`, `계속 진행해`, `계속하세요`, `진행해줘`, `응 계속해` | ✅ |
| **`계속해줘`**, **`계속 진행해줘`**, **`계속해서 진행해`**, **`계속해서 진행해줘`** | ❌ |
| **`이어서 해줘`**, **`이어서 진행`**, **`마저 해줘`**, **`다시 진행해`** | ❌ |
| `continue please`, `go ahead` | ❌ |

- 인식에 실패하면 이어지는 연쇄 효과:
  1. `buildObjectiveContinuity()`(`continuity-objectives.js:182~`)가 **"계속해줘" 자체를 새 활성 목표로** 정합니다. 원래 목표는 `archived_completion_unverified / superseded_after_prior_compaction`으로 보관되고, 체크포인트에도 "대체됨"으로 기록됩니다.
  2. `objectiveFingerprint`가 바뀌어 `reconcileStoredNote()`(`continuity-model-notes.js:311-312`)가 직전 노트(decisions·rejectedHypotheses·openQuestions)를 **폐기**합니다.
  3. `renderTaskProgress()`(`continuity-memory.js:307-309`)가 `sameObjective=false`로 판단해 미해결 항목을 `historicalUnresolvedItems`로 내립니다.
  4. `olderContinuationAnchor`와 `continuationAntecedent`가 채워지지 않아, 모델이 "무엇을 계속할지" 알 수 없게 됩니다.
- 함께 있는 문제: `taskSelection()`(`continuity-objectives.js:59`)은 `^(이제\s|다음은\s|새 작업|now|next)`로 시작하면 선택 작업을 `superseded`로 바꿉니다. 그래서 **"이제 계속 진행해줘"** 같은 문장은 오히려 작업 선택을 무효화합니다.
- 권장: 짧은 메시지(예: 40자 이하)이면서 새 대상(경로·심볼·명사구)이 없으면 이어하기로 보는 쪽으로 판정을 바꿉니다. 즉 **기본값을 "이전 목표 유지"로** 둡니다. 최소한 `(해|해줘|해서|하세요|진행(해)?(줘)?)` 같은 활용형과 `이어서/마저/다시/continue/go ahead`를 추가합니다. 목표 교체 판정은 하나의 모듈이 담당해야 합니다(현재는 `looksElliptical`, `numberedReference`, `taskSelection` 정규식이 분산되어 있음).

### C2 [높음][코드] 중단(abort) 시 continuity 노트 저장 누락

- `prediction-loop.ts:1016`은 `if (ctl.abortSignal.aborted) throw ...`로 루프를 예외로 빠져나갑니다.
- 노트 영속화(`prediction-loop.ts:1250-1268`, `noteStore.write(nextHistoryKey, ...)`)는 **try 블록 안, 루프 다음**에 있어서 중단·예외 시에는 실행되지 않습니다. 창 봉인(`seal`)만 finally에서 수행됩니다.
- 결과: 중단된 턴에서 모델이 갱신한 노트는 노트 스토어에 남지 않습니다. 다음 턴은 `workingContext.note`(창 복원이 성공했을 때만 존재)에만 의존하므로, 창 복원이 실패하면(C7·C8) 노트가 사라집니다.
- 권장: 노트 영속화를 finally의 seal 직전으로 옮깁니다. 또는 노트를 창(window) 한 곳에만 저장하도록 SSOT로 통합합니다(→ S4).

### C3 [높음][코드] 재개 턴에서 `CONTEXT_PENDING_EVIDENCE_OMITTED`로 턴 전체 실패

- 시나리오:
  1. 턴 N에서 읽기 도구 결과가 반환된 직후 사용자가 중단합니다. `captureReturned`가 결과를 `returnedRefs`(미소비)로 등록하고, `seal`이 이를 `pendingRefs`로 창에 저장합니다(`working-context.js:#writeWindow`).
  2. 턴 N+1("계속")에서 `restore()`가 `returnedRefs`를 그대로 복원합니다.
  3. 입력이 high-water를 넘으면 `enforceLowWater` 후보가 `boundedCurrentTurnIndexes()`(`direct-compaction-core.js:163`)를 사용합니다. 이 함수는 **최신 사용자 메시지 이후**만 보존하므로, 이전 턴의 미소비 결과가 빠집니다.
  4. `prediction-loop.ts:385` `captureExposure(modelInput, ..., false)` → `reconcileRefs(requirePending=true)` → `working-context.js:222`에서 **throw**가 발생합니다. 잡는 곳이 없어 실행 전체가 실패합니다.
- 계약 불일치: 후보 선택기(ContextManager)는 pending 제약을 모르고, 사후 검증(WorkingContext)만 압니다. 검증에 실패했을 때 다른 후보로 넘어가는 경로가 없습니다.
- 권장: pending 결과를 후보 선택의 **필수 보존 대상**으로 전달합니다. 또는 위반 시 해당 결과를 projection으로 대체한 후보로 재시도합니다. 예외로 턴을 실패시키면 안 됩니다.

### C4 [높음][확인] 하이브리드 압축이 이전 턴 전체를 버림 (all-or-nothing)

- `ContextManager.enforceLowWater()`(`context-manager.ts:501-513`)의 후보는 전부 `buildCompactedHistory(history, 0, ...)`, 즉 `recentCompleteTurns=0`에 `maxCurrentTurnMessages=cap`입니다. 레거시 경로의 `selectSoftCompaction()`과 달리 "최근 1~2턴 원문 유지" 후보가 **없습니다**.
- `boundedCurrentTurnIndexes()`는 최신 사용자 메시지 **이후**만 대상으로 삼기 때문에, 첫 후보(cap 128)부터 이미 이전 턴의 사용자 요청과 최종 답변 원문을 모두 제거합니다.
- 제거된 산문이 체크포인트에 남는 양(`direct-compaction-core.js`):
  - 직전 최종 답변: `previousTurnFinalResponseEvidence`는 **`{present, messageIndex}`만 저장하고 본문은 없음**(:127-136)
  - `lastAssistantUpdate.text` 800자, `recentRawTail` 4개 × 800자에서 압박 시 300자로 축소(:487-509)
  - 이전 사용자 요청 최대 3개 × 2,000자(:138-144). 체크포인트 예산 단계에서는 이마저 삭제(:497-500)
  - 체크포인트 예산 후보는 22,000자의 1/4 또는 1/8(2,750자)까지 축소(`context-manager.ts:505-507`)
- 아카이브(`evidence-archive.js`)는 **도구 결과만** 저장합니다. 사용자·어시스턴트 산문은 저장되지 않아 `evidence_first_read_context`로도 복구할 수 없습니다.
- 창은 압축된 replacement를 영속화하므로(`#writeWindow`), 다음 턴은 "체크포인트의 체크포인트"에서 출발합니다. 따라서 **손실이 턴마다 누적**됩니다.
- 권장:
  1. 후보 앞단에 `recentCompleteTurns=2→1` 후보를 추가합니다(레거시의 soft 단계와 동일).
  2. 직전 최종 답변 본문(최소 head/tail 2~4K자)을 체크포인트 필수 항목으로 둡니다.
  3. 제거되는 산문 메시지도 아카이브에 넣어 재수화(rehydration)할 수 있게 합니다.

### C5 [치명][확인] 워터마크 설계: 절대값 상한 + 도구 스키마 고정비

`BudgetBroker.watermarks()`(`budget-broker.ts:22-49`)를 기본 설정(출력 8192, 안전 2048, target 18000, trigger 22000, 최소 읽기 ≈3072)으로 계산하면 다음과 같습니다.

| 컨텍스트 | hard 상한 | high-water(압축 시작) | low-water(압축 목표) | 다음 동작 가능 최대 입력 |
|---|---|---|---|---|
| 32,768 | 22,528 | 11,264 | **8,448** | 11,264 |
| 38,912(기본 fallback) | 28,672 | 16,896 | 12,672 | 17,408 |
| 65,536 | 55,296 | **22,000** | **16,500** | 44,032 |
| 131,072 | 120,832 | **22,000** | **16,500** | 109,568 |

- **컨텍스트가 128K여도 22K에서 압축을 시작해 16.5K로 줄입니다.** `workingInputTriggerTokens`/`TargetTokens`가 절대값이고 컨텍스트에 비례하지 않습니다(`config-defaults.ts:4`). 큰 컨텍스트를 로드한 사용자도 불필요하게 대화를 잃습니다.
- 실측 로그: 도구 스키마 토큰 `finalToolSchemaTokens`가 **7,501~10,406**(최빈 8,492), 필수 하한 `mandatoryFloorTokens`가 **13,920~16,361**, `effectiveLowWaterTokens` 최빈값이 16,128입니다. `mandatory_floor_exception`(하한이 목표보다 큼)은 85건입니다.
  → 하한선의 60~70%가 도구 스키마이고, 대화·증거에 쓸 수 있는 공간은 **약 2~4K 토큰**입니다. 결과 한두 개(8KB 읽기 ≈ 2.5K 토큰)만 들어와도 다시 high-water를 넘어 압축 → 최신 교환만 남음 → 모델이 다시 읽음(정체 감지) → 압축이 반복되는 **스래싱**이 구조적으로 발생합니다.
- 권장:
  1. trigger/target을 "컨텍스트 대비 비율"과 "절대값" 중 큰 쪽으로 정합니다(예: trigger = max(22000, 0.6·hard)).
  2. 도구 스키마를 축소합니다(설명 압축, 라운드별 노출 프로파일 — M7).
  3. low-water를 "하한 + 최소 작업 공간(예: 6K)"으로 보장합니다.

### C6 [치명][확인] 공유 배치 예산 과대 차감 → 도구 호출 거부

`BatchReservation`/`reserveReadResult`(`budget-broker.ts`):

1. **바이트를 토큰으로 계산**: `receive()`가 `entry.tokens = bytes + 128`(:92)이고, `byteBudget` 계약의 multiplier가 1(:128)이라 8,192바이트 읽기를 **9,216토큰**으로 예약합니다. 실제로는 코드·영문 기준 약 2.3~2.7K 토큰이고, 한글은 3바이트/자라 더 과대 계산됩니다. **약 3~4배 과대 차감**입니다.
2. **출력 예약 이중 차감**: `hardInputCeiling = ctx − safety − outputReserve`(:26)에 더해 `requiredPostToolOverhead = outputReserve`(:32)를 또 빼서, 38,912 컨텍스트에서 16,384가 출력용으로 묶입니다. 배치 용량은 `hard − input − outputReserve`(`beginBatch`, :50)입니다.
3. **budget 필드가 없는 도구는 고정 3,072 예약**(:164): `unreal_rag_search`(detailLevel `full` = 최대 80,000자), `read_unreal_logs`(필드명 `maxBytes`인데 native 목록에 없음), `list_directory` 등입니다. 실제 결과가 더 크면 `overrun` → `exceeded`가 되고, 같은 배치의 **이후 모든 호출이 `returned_result_overrun`으로 거부**됩니다(:65-70).
4. 반대로 **변경·장기 실행 도구(write/build/static_validate/run_command)는 예약 대상이 아닙니다**(`tool-boundary.ts:155`의 observation 분기만 예약). 그래서 수만 자의 빌드 출력이 그대로 들어와 다음 라운드를 hard 상한 밖으로 밀어냅니다.

- 예: 입력 14K, 컨텍스트 38,912 → 배치 용량 = 28,672 − 14,000 − 8,192 ≈ 6.5K "토큰". 첫 `read_file`이 약 5.4KB로 깎이고, **두 번째 병렬 읽기는 거부**됩니다.
- 거부 메시지("Split the read batch…")가 도구 결과로 들어가 모델이 같은 요청을 재시도하고, 이것이 정체 감지와 스래싱으로 이어집니다.
- 권장: 바이트→토큰 환산 계수를 사용합니다(측정값 기반, 예: 바이트/3.0, 보수적으로 /2.5). post-tool 오버헤드는 "도구 호출 요청 생성분"(예: 1~2K)만 잡고, 서버가 결과 크기를 보장하는 도구에는 budget 인자를 전달합니다(M1). 변경 도구 결과도 투영(projection) 대상에 포함합니다.

### C7 [중간][확인/가설] 창 복원·경계 마커의 취약성

1. **복원 실패 원인이 사라짐**: `WorkingContext.restore()`의 바깥 catch(`working-context.js:717`)가 파일 없음(ENOENT), JSON 손상, 역직렬화 오류를 모두 `"unavailable"` 하나로 반환합니다. 실행 로그에서는 `restored_remeasure_required` 2건, `unavailable` 10건, `no_manifest` 2건으로 대부분 복원되지 않았는데, 원인을 구분할 수 없습니다.
2. **해시 기준이 호스트가 아니라 플러그인의 재구성**: 창의 `sourcePrefixHash`는 `VisibleHistoryRecorder.snapshot()`(`prediction-ui.ts:22-73`)이 content block마다 메시지 1개로 **추정한** transcript입니다. LM Studio가 블록을 병합하거나 다르게 재수화하면 다음 턴의 `pullHistory()` 직렬화와 달라져 `source_prefix_mismatch`가 납니다. 노트 스토어 키(`historyKey`)도 같은 방식이라 같은 위험이 있습니다. [가설 — 호스트 재수화 형식 확인 필요]
3. **경계 마커의 영구 비활성화**: `WorkingContextBoundary.restore()`(`working-context-boundary.ts:68-76`)는 `fallbackReason ||=`로 **한 번이라도** edited/stale/new_fork가 나오면 이후 마커가 정상이어도 scope를 null로 둡니다. 이후 `capture()`는 새 conversation UUID를 발급하므로, 그 채팅은 이후 모든 턴에서 `durable=false`(세션 한정)가 됩니다. 과거 메시지 하나만 편집해도 내구 연속성이 영구히 꺼집니다.
4. **반대 극단 — 치명적 예외**: 서명 불일치(`:52` `signature_invalid`)와 대화 ID 충돌(`:66`)은 **throw**합니다. `~/.lmstudio/unreal-context-compactor/hybrid-boundary-v1/reference-key`가 재설치·정리·다른 PC 이동 등으로 바뀌면, 해당 채팅의 **모든 예측이 실패**합니다(선택적 캐시의 무결성 실패가 기능 전체를 막음).
- 권장: 실패 사유 코드를 세분화합니다(`no_window_file`, `parse_failed`, `prefix_mismatch@index` 등). 접두 해시는 다음 턴의 `pullHistory()`를 기준으로 사후 검증하거나, 첫 불일치 지점까지만 부분 복원합니다. 경계 마커는 "가장 최근의 연속 유효 구간"을 기준으로 scope를 복구하고, 서명 오류는 degrade(마커 무시)로 처리합니다.

### C8 [중간][코드] 24시간 TTL이 window 참조를 보호하지 못함

- `put()` 주석(`evidence-archive.js:531`)은 "manifest가 참조하는 기록은 TTL 이후에도 보호"라고 하지만, `load()`(`:404`)는 TTL이 지나면 무조건 `expired`를 반환합니다.
- `restore()`가 `refsValid(body.refs)`에서 실패해 `archive_refs_unavailable`이 되고, **다음 날 "계속"하면 압축된 창 전체를 잃고** 원본 LM Studio 기록에서 다시 압축합니다.
- 권장: window·lease가 참조하는 기록은 `load()`에서 TTL 예외로 처리하거나, 복원 시 TTL을 갱신(touch)합니다.

### C9 [중간][코드] 아카이브 디렉터리 잠금의 수명 관리

- `withDirectoryLock()`(`evidence-archive.js:89-127`):
  - 다른 경합자가 `choosing`이거나 더 낮은 ticket이면 **대기·재시도 없이 즉시 `archive_lock_unavailable`을 throw**합니다. 이 경우 `put()`이 `archive_lock_unavailable`, `restore()`가 `archive_lock_unavailable`이 되어 투영·복원이 실패합니다.
  - 생존 판정이 `process.kill(pid, 0)`뿐입니다. 프로세스가 action 도중 강제 종료되어 `lock-*.json`이 남고, **Windows에서 그 PID가 다른 프로세스에 재사용**되면 고아 잠금이 "살아 있는 경합자"로 계속 판정됩니다. 그러면 해당 디렉터리의 모든 잠금이 영구히 실패합니다. `lease-*.json`(`leaseFacts`)도 같아서 pin이 영구히 남아 quota를 압박합니다.
- 대조: `lmstudio-unreal-agent-mcp/src/write-locks.js`는 프로세스 시작 시각(identity)까지 비교해 PID 재사용을 방어합니다. **같은 프로젝트 안에 잠금 정책이 둘**입니다(→ S5).
- 권장: 잠금 파일에 프로세스 시작 identity와 타임스탬프 기반 stale 판정을 넣고, 짧은 백오프 재시도를 추가합니다.

### C10 [중간][코드] 생각(reasoning) 단계 컷오프 처리

- `maxOutputReserve`(기본 8,192)가 reasoning 토큰을 포함한 전체 생성 상한입니다. 긴 사고형 모델은 구조적으로 상한에 걸립니다.
- `decideReasoningRecovery()`(`reasoning-recovery.ts`)는 **같은 도구와 같은 출력 상한**으로 1회 재시도합니다. 입력도 거의 같아서(지시문 1개 추가) 같은 지점에서 다시 끊길 가능성이 높습니다. 재시도에도 실패하면 자동 진행을 멈춥니다.
- 사용자가 "계속"을 보내면 C1·C2·C7이 겹쳐 맥락이 약해진 상태에서 시작합니다.
- 권장: 재시도 시 reasoning 예산을 분리하거나(가능하면 모델 옵션), 출력 상한을 늘리고 입력을 줄인 후보로 재시도합니다. 중단 지점을 "미완료 사고 요약"으로 짧게 남기는 것도 검토합니다.

### C11 [중간][코드] 합성 데이터를 assistant 발화로 주입

- `assistant` 역할로 들어가는 합성 메시지:
  - 노트·reference·taskData(`context-manager.ts:250-252`)
  - 체크포인트 assistant/changeData(`:154-155`)
  - 미완료 호출 기록(`execution-state.ts` `interruptionMessage`)
  - 예산 피드백(`prediction-loop.ts:732`)
  - 정체 관찰문(`:1002-1005`)
- 영향:
  1. 모델이 런타임 사실을 **자기가 한 말**로 오인합니다.
  2. system 바로 뒤 assistant, assistant 연속처럼 엄격한 chat template에서 문제가 될 수 있는 순서가 만들어집니다.
  3. 예산 피드백과 정체 관찰문은 `workingHistory`에 append되어 **창과 함께 영속**되고 다음 턴까지 따라갑니다.
- 권장: 런타임 사실은 system(또는 템플릿이 지원하는 tool/developer 역할)의 **끝부분**에 한 블록으로 모으고, `workingHistory`에는 넣지 않습니다(P2와 함께 해결).

---

## 4. 하네스 성능 병목

### P1 [높음][확인] semantic handoff는 비용만 들고 성공하지 못함
- 로그 집계: `semantic_handoff` 이벤트 **285건 모두 `accepted:false`**. 사유는 `failure_cooldown` 201, `routine_projection_without_compaction` 67, `invalid_finish_reason` 10, `timeout` 5, `length` 4.
- 실제 모델 호출 19건의 `elapsedMs` 중앙값 **30,029ms**, p90 30,187ms. 기본 `semanticSummarySeconds=30`이라 **매번 타임아웃 근처까지 차단**됩니다.
- 별도 프롬프트(`semantic-handoff.ts`)로 `act()`를 호출하므로, 단일 슬롯 백엔드에서는 본 대화의 KV 캐시가 밀려나 다음 라운드가 **전체 재-prefill**됩니다.
- 사고형 모델에서 reasoning을 끄지 않아 1,024 토큰 출력 상한(`length`)이나 무효 종료로 끝나는 것으로 보입니다.
- 권장: 기본값을 off로 바꾸거나, 결정적 체크포인트에 결정사항을 직접 기록하는 방식으로 대체합니다. 유지한다면 비사고 모드·짧은 타임아웃·백그라운드 실행을 적용합니다.

### P2 [높음][확인/가설] 프롬프트 접두부 변동으로 KV 캐시 재사용 실패
- `composeModelHistory()`(`context-manager.ts:243-255`)는 system 뒤, 즉 **대화 앞부분**에 노트, reference, `renderTaskProgress()` 결과를 넣습니다. 모델이 노트 footer를 갱신하거나 미완료 호출 목록이 바뀔 때마다 **그 뒤 대화 전체의 KV 캐시가 무효화**됩니다. 압축이 일어나면 system 안의 체크포인트도 바뀝니다.
- 로그: `modelPrefillMs`(첫 토큰까지) 353건, 중앙값 **10.5초**, p90 **32.4초**, 최대 62.2초. 16~20K 입력에서 증분 prefill이라면 나오기 어려운 값이라 접두부 재사용 실패를 강하게 시사합니다(하드웨어 조건은 확인 필요).
- 권장: 정적 요소(system 지시, 도구 스키마)를 앞에, 변동 요소(노트, 작업 진행, 가용성 메타데이터)는 **마지막 사용자 메시지 직전·직후**로 옮깁니다. 압축은 경계 단위로 드물게 수행합니다(C5 해결로 빈도 감소).

### P3 [중간][확인] 토큰 측정 호출 과다
- `createInputAssembler()`의 `assemble` 한 번에 본 측정 1회와 **telemetry 전용 측정 최대 4회**(`historyMeasurement`, `projectionMeasurement`, `compactionMeasurement`, `metadataMeasurement` — `context-manager.ts:314-322`)를 합니다. 각각 `applyPromptTemplate` + `countTokens`(전체 프롬프트) RPC이고, `showDebugInfo`와 무관하게 항상 수행됩니다.
- `enforceLowWater`는 후보 최대 12개를 각각 조립·측정합니다. 가이던스, 재시도 후보, 노트 재조립도 추가 측정을 합니다. 캐시는 라운드 단위라 라운드 간 재사용이 없고, 캐시 키 계산마다 `serialize(history)`를 해싱합니다.
- 로그: 라운드당 측정 호출 중앙값 4, p90 25, **최대 149**. 측정 시간 중앙값 258ms, p90 730ms, 최대 3.1초.
- 권장: telemetry 측정은 `showDebugInfo`일 때만 하거나 1회로 통합합니다. 후보 탐색은 이분/증분 추정 후 최종 1회만 정확 측정합니다.

### P4 [중간][코드] 핫패스의 동기 파일 I/O
- `EvidenceArchive.put()`은 잠금 하에서 `entries()`(디렉터리의 모든 `ev_*.json`을 읽고 파싱, 최대 128개)를 **2회**, `leaseFacts()`(모든 lease 파싱), `windowRefs()`, `atomicWrite` 2회(레코드와 lease)를 수행합니다. 결과 1건당 수 MB를 동기로 읽을 수 있습니다.
- `WorkingContext.hasArchivedEvidence` getter는 refs가 비어 있으면 모든 레코드를 `load`합니다. 이 getter는 `minimumReadBudget()`을 통해 **라운드당 여러 번** 호출됩니다(`round-input.ts:49-50`).
- `WorkingContextBoundary.key()`는 마커마다 `mkdirSync`, `lstat`, `writeFileSync` 시도, `readFileSync`를 수행합니다. 이것은 예측마다 사용자 메시지 수만큼 일어나고, 첨부 경로에서는 `restore()`가 2회 호출됩니다(`prediction-loop.ts:70,73`).
- 모두 플러그인 이벤트 루프를 막아 스트리밍 UI 갱신을 지연시킵니다.
- 권장: 레코드 메타데이터 인덱스(단일 manifest)를 메모리에 캐시하고, 키는 프로세스 수명 동안 캐시합니다.

### P5 [중간][코드] 비제한 연구 루프
- 기본(`auditCompletionMode=off`)에서는 `while (true)`가 `continueAfterTools`인 동안 계속됩니다. 라운드·시간 상한이 없고, 정체 감지 기본값도 `warn`입니다(`direct-config.ts`). C5의 스래싱과 결합하면 같은 읽기를 반복하는 장기 루프가 될 수 있습니다.
- 권장: 기본 상한(예: 40라운드 또는 정체 2회 시 pause)과 사용자 표시를 둡니다.

### P6 [낮음][코드] 반복 복사·재계산
- 라운드마다 `Chat.from(workingHistory)`를 3~4회 복사하고, `normalizeHistory`를 반복하며, `renderTaskProgress()`가 조립할 때마다 objective continuity를 다시 계산합니다(O(n)). 시작 시 `inputAvailabilityMode=observe`(기본)이면 전체 기록으로 `core.buildCheckpoint()`를 1회 수행합니다(`prediction-loop.ts:172`). 단독으로는 작지만 P3와 곱해집니다.

---

## 5. MCP 도구 문제

### M1 [높음][코드] 응답 크기 계약 불일치 (서버 ↔ 컴팩터)
- `unreal-agent`: `maxResponseChars` 기본 **256,000자**(`direct-runtime-context.js:41`). `byteBudget` 필드가 있는 도구는 `read_file`, `read_file_range`, `read_symbol`, `search_files`, `git_*`뿐입니다(실측). 나머지는 컨텍스트(약 39K 토큰)보다 큰 결과를 그대로 반환할 수 있습니다(`list_directory`, `read_unreal_logs`(자체 `maxBytes`), `build_unreal_project`, `static_validate_project`, `run_command`, `write_file` 등).
- `unreal-rag`: `detailLevel=full`이면 `max_tool_chars` 80,000(`direct_rag_limits.py`). 컴팩터가 줄일 수 있는 budget 인자가 없습니다.
- 컴팩터는 이 도구들에 3,072를 예약하거나(읽기) 아예 예약하지 않으므로(변경·장기 실행) C6의 overrun과 다음 라운드 hard 상한 초과로 이어집니다.
- 권장: 모든 도구에 공통 `byteBudget`(또는 `maxChars`) 계약을 두고, 기본 응답 상한을 컨텍스트 친화적으로 낮춥니다(예: 16~24K자 + 페이징). 빌드·검증 결과는 요약과 로그 파일 참조 형태로 바꿉니다.

### M2 [중간][코드] 응답 직렬화의 토큰 낭비
- 두 서버 모두 **pretty JSON**을 씁니다. agent는 `boundedJson()`과 `payloadFits()`에서 `JSON.stringify(payload, null, 2)`(`direct-response.js`, `direct-runtime-context.js:105`), RAG는 `json.dumps(..., indent=2)`(`direct_rag_result.py:108,121,128`)입니다. 들여쓰기와 개행 때문에 결과당 약 15~25%의 토큰이 낭비됩니다.
- agent는 `content`(text)와 `structuredContent`에 **같은 payload를 두 번** 싣습니다. LM Studio가 둘 다 모델 입력에 넣는지는 확인이 필요하며[가설], 그렇다면 결과 토큰이 2배가 됩니다.
- 권장: 모델용 text는 compact JSON으로 바꾸고, structuredContent를 생략할지는 호스트 동작을 확인한 뒤 정합니다.

### M3 [중간][코드] `search_files`의 성능과 취소 불가
- `searchFiles(args)`(`direct-read-capabilities.js:102-176`):
  - 디렉터리 순회가 항목마다 순차 `await`(stat, `assertReadChildContained`)입니다. 기본 `maxFiles=5000`, 파일당 최대 2MB를 **순차로 전부 읽습니다**.
  - `requestContext.signal`을 받지 않아 **취소가 전달되지 않습니다**. LM Studio가 예측을 중단해도 서버는 계속 작업합니다.
  - 사용자 정규식(`regex=true`)에 시간 제한이 없어 ReDoS 패턴이 **Node 서버 전체**를 멈출 수 있습니다.
  - 예산 초과 시 결과를 하나씩 pop하며 매번 전체 payload를 pretty-stringify합니다(최대 1,000회).
- 권장: signal 전달, 동시성 제한 병렬 I/O, 정규식 실행 시간 제한(또는 RE2), 결과 크기 누적 계산.

### M4 [중간][코드] RAG 서버의 직렬 처리
- `DirectRagServer.run()`(`direct_rag_server.py`)은 큐에서 요청을 꺼내 **하나씩 동기 처리**합니다. `unreal_rag_refresh`(재색인) 같은 긴 작업이 도는 동안 검색·심볼 조회가 모두 대기합니다. 컴팩터가 허용하는 병렬 읽기 배치도 서버에서 직렬화됩니다. 클라이언트 타임아웃은 420초입니다(`config/cline_mcp_settings.template.json`).
- 권장: 읽기 요청은 읽기 전용 DB 연결로 스레드 풀에서 처리하고, 변경 작업만 직렬화합니다.

### M5 [낮음][코드] git 실행 제약
- `shared-tool-core/git.js:38`: `timeout 10s`, `maxBuffer 4MiB`. 큰 UE 저장소에서 `git log`/`diff`가 초과하면 페이징 없이 `git_output_too_large`나 `git_timeout`으로 실패합니다.

### M6 [낮음][코드] 쓰기 잠금의 동기 프로세스 생성
- `write-locks.js:48-118`: Windows에서 프로세스 identity를 확인하려고 `spawnSync(powershell Get-Process)`를 실행하고(자기 프로세스는 1회 캐시), stale 회수 시 `spawnSync(python write-lock-reclaim-bridge.py)`를 실행합니다. 경합이나 첫 잠금 시 수백 ms~1초 이상 **서버 이벤트 루프 전체를 차단**합니다.

### M7 [중간][확인] 도구 스키마 고정비
- 실측 스키마 크기: agent 26개 도구 **24,357자**, RAG 8개 **7,047자**, 로컬 아카이브 도구 약 1.6K자. 로그 기준 약 **7.5K~10.4K 토큰**입니다(C5의 주원인).
- 큰 항목: `apply_edit_bundle` 2,474자, `replace_in_file` 2,126자, `unreal_rag_search` 3,128자, `git_log` 1,588자, `git_changed_files` 1,538자.
- 권장: 설명을 압축하고, "읽기 단계에서는 변경 도구 숨김" 같은 단계별 노출 프로파일을 둡니다(이미 `readProfile()` 기반 구조가 있음).

### M8 [낮음][코드] 계약 문자열 중복
- `direct-server.js:74`의 오류 메시지 "byteBudget must be an integer from 2048 to 65536."는 카탈로그 스키마(`direct-tool-catalog.js:7`)의 값을 하드코딩한 것입니다.

---

## 6. 계약·수명·SOLID·SSOT

| ID | 원칙 | 내용 | 위치 |
|---|---|---|---|
| S1 | SRP | `createPredictionLoopHandler`가 약 1,300줄. 상태 기계, UI 스트리밍, telemetry 객체 구성, 예산, 복구 판단, 노트·창 영속화를 한 클로저에서 처리. C2 같은 수명 버그의 직접 원인 | `prediction-loop.ts` |
| S2 | ISP | `prepareRoundInput`의 옵션 30개 이상, 반환 20개 이상. 호출자와 피호출자가 서로의 내부 상태를 공유 | `round-input.ts:19-38` |
| S3 | SSOT | 설정 기본값이 `direct-config.ts`의 리터럴과 `execution-config.ts`의 fallback에 **이중 정의**(6000/3000/8192/2048/38912/2/24/22000/1200/3/100/12/70/4096/90 등). `config-defaults.ts`로 일원화된 것은 guidance와 context 일부뿐 | `direct-config.ts`, `execution-config.ts` |
| S4 | SSOT | 프롬프트 전처리기는 모드 기본값 fallback이 `"legacy"`인데, 핸들러는 `CONTEXT_DEFAULTS.mode="hybrid"`. 값이 비어 있으면 전처리기는 마커를 쓰지 않고 핸들러만 hybrid로 동작해 내구 복원이 조용히 꺼짐 | `working-context-boundary.ts:106` vs `execution-contracts.ts:118` |
| S5 | SSOT | **도구 분류 3중 정의**: 컴팩터 `UNREAL_OBSERVATION_TOOLS`/`UNREAL_RAG_OBSERVATION_TOOLS`, 서버 `TOOL_EFFECTS`, RAG `direct_rag_contract.py`. 여기에 `readResultContract()`가 플러그인 ID·도구명·기본값(4096/8192/32768)·배수(1/2/3/6)를 하드코딩. 서버 카탈로그가 바뀌면 컴팩터 예산이 조용히 틀어짐 | `tool-capability-registry.ts:30-70`, `budget-broker.ts:113-134`, `direct-tool-catalog.js:10-` |
| S6 | SSOT | **노트 이중 영속화**: `ContinuityNoteStore`(historyKey)와 `window.note`가 키·수명·조정 규칙이 서로 다름. 조정 로직이 핸들러에 흩어짐 | `prediction-loop.ts:185-202,1250-1268` |
| S7 | SSOT | **잠금 정책 3종**: 아카이브 bakery 잠금(PID만), agent 쓰기 잠금(PID와 시작 identity, sqlite bridge), 경계 키 파일. stale 판정 기준이 서로 다름(C9) | `evidence-archive.js:89`, `write-locks.js`, `working-context-boundary.ts:24` |
| S8 | SSOT | 아카이브 읽기 상한 8192를 `read()`/`readByHandle()`에 하드코딩. `ARCHIVE_READ_CONTRACT.maximumEnvelopeChars`와 중복 | `evidence-archive.js:455,555` |
| S9 | 계약 | 후보 선택(ContextManager)과 사후 검증(WorkingContext)의 불변식이 다름. pending 보존 제약을 선택기가 모름(C3) | `context-manager.ts:477`, `working-context.js:215-222` |
| S10 | 계약 | TTL 보호 주석과 실제 `load()` 동작이 모순(C8) | `evidence-archive.js:404,531` |
| S11 | 수명 | 실패 정책이 비일관적. 같은 "선택적 캐시" 계층에서 어떤 실패는 모두 삼키고(`restore`/`put`/`load` catch-all → `unavailable`/`archive_write_failed`), 어떤 실패는 기능 전체를 중단(경계 서명 throw, `CONTEXT_PENDING_EVIDENCE_OMITTED`) | C3, C7 |
| S12 | 수명 | 중단 경로에서 노트는 저장되지 않고 창만 봉인됨(C2). 정상·예외 경로의 종료 처리가 비대칭 | `prediction-loop.ts:1250,1272-1297` |
| S13 | LSP/계약 | 합성 런타임 사실을 `assistant` 메시지로 표현해 역할의 의미 계약을 위반(C11) | §3 C11 |
| S14 | OCP | 이어하기·작업 선택 판정이 분산된 한국어·영어 정규식 화이트리스트에 의존. 표현을 추가하려면 여러 모듈을 수정해야 함(C1) | `continuity-text.js:53`, `continuity-objectives.js:14-59` |

**양호한 점**(유지 권장): 측정 기반 실행 허가(정확 토큰 측정 없이는 실행하지 않음), `RoundTransaction`으로 미완 산문과 반환된 증거를 분리한 것, 도구 가드와 프로젝트 바인딩, agent 쓰기 잠금의 PID 재사용 방어, 전송 단계의 cursor 보존(초과 시 성공 payload를 자르지 않고 재시도 가능한 오류로 반환), 풍부한 telemetry.

---

## 7. 권장 수정 순서

| 단계 | 항목 | 기대 효과 | 난이도 |
|---|---|---|---|
| 1 | **C1** 이어하기 판정 확대·기본값 반전 | "계속" 실패의 직접 원인 제거 | 낮음 |
| 2 | **C6** 바이트→토큰 환산, 출력 예약 단일화 | 병렬 읽기 거부 대부분 해소 | 낮음~중간 |
| 3 | **C5** 워터마크 비례화 + **M7** 스키마 축소 | 압축 빈도·강도 감소, 작업 공간 확보 | 중간 |
| 4 | **C4** 최근 턴 보존 후보 추가, 직전 답변 본문 보존, 산문 아카이브 | 대화 손실 감소 | 중간 |
| 5 | **C2/C3/S12** 중단 경로 정리(finally에서 노트 저장, pending 보존을 선택기로 이동) | 재개 턴 실패 제거 | 중간 |
| 6 | **P1** semantic handoff 기본 off 또는 비사고 모드, **P2** 변동 데이터 뒤쪽 배치 | 라운드당 수십 초 절감 | 중간 |
| 7 | **C7/C8/C9** 복원 사유 세분화, TTL 보호, 잠금 identity·재시도 | 장기·다음 날 연속성 | 중간 |
| 8 | **M1~M4** 서버 응답 계약 통일, 직렬화 compact, search 취소·병렬화, RAG 읽기 병렬화 | 도구 안정성·지연 | 중간 |
| 9 | **S3/S5/S6** 설정·도구 분류·노트 SSOT 일원화(`config/stable_tool_manifest.json`을 단일 원천으로 생성) | 회귀 방지 | 중간~높음 |
| 10 | **S1/S2** 핸들러 분해(RoundPreparer / RoundExecutor / TerminalPersistence) | 유지보수성 | 높음 |

검증 제안: 1~5단계마다 기존 `test/` 스위트에 다음 회귀 테스트를 추가합니다. (a) 표 C1의 문구 전체에 대한 이어하기 판정, (b) 중단 → "계속" 재개에서 pending 결과가 있는 상태로 압축되는 시나리오, (c) 38,912 / 65,536 컨텍스트에서 읽기 2개 병렬 배치 허용 여부. 그다음 live 로그로 `selectedCandidate` 분포, `denied_batch_budget` 건수, `modelPrefillMs`를 이전·이후로 비교합니다.

---

## 부록 A. 로그 집계 방법

- 대상: `artifacts/**/*.json`(라이브 GUI 이벤트·telemetry). 정규식으로 `"event"`, `"selectedCandidate"`, `"disposition"`, `"validationState"`, `"finalToolSchemaTokens"`, `"mandatoryFloorTokens"`, `"measurementCost"`, `"modelPrefillMs"`, `semantic_handoff`의 `accepted`/`reason`/`elapsedMs`를 집계했습니다.
- 표본은 개발 중 수행한 검증 실행 기록이라 일반 사용 분포와 다를 수 있습니다. 수치는 경향의 근거로만 사용하세요.

## 부록 B. C1 확인에 사용한 스크립트

```bash
cd lmstudio-context-compactor-plugin
node -e 'const {looksElliptical}=require("./src/continuity-text.js");for(const t of ["계속해줘","계속 진행해줘","계속해서 진행해","이어서 해줘","마저 해줘","다시 진행해","continue please","go ahead"])console.log(t,looksElliptical(t))'
```
