# 로컬 모델 하네스 — 판단 정확도·읽기 범위·프롬프트 처리 대기 최종 구현 계획

작성: 2026-10-19 · 기준 HEAD `12dddcc935d5635f5c3df1737f509c000462d3a2` (`Develop`) — 이번 세션에서 실제 HEAD 일치 확인.
작업 트리: `docs/Harness_Targeted_Engine_Read_Plan_20261018.md`(수정), `docs/evidence/harness-prefill-analysis-20261019.json`, `docs/evidence/harness-behavior-latency-plan-20261019.json`(미추적) — 사용자 작업으로 보존, 되돌리지 않음.

## Context

선행 P0–P5(범위 완료 계약·엔진/64 KiB 초과 whole-read 거부·signature/member·다중 영역 편집·receipt·현재 턴 pinning)는 구현·설치·CI 완료 상태다. 최신 실행(`cbc227f3…`, Compactor 0.4.78/rev 125, loaded=installed)에서 세 문제가 남았다.

- **A. 판단 정확도**: 서버의 일반 Client RPC 호출을 "서버에서만 실행"으로 오판한 P1이 최종 보고에 남음. 엔진 기본 Health/Die 가정은 사용자 지적 후에야 철회.
- **R. 읽기 범위(사용자 지적 + 소스·기록 확인)**: 프로젝트 파일 ≤64 KiB는 암묵적 전체 읽기가 허용되는 레거시가 남아 있어 관련 파일을 전부 읽음. 이 입력이 B의 라운드당 입력량을 키움.
- **B. 프롬프트 처리 대기**: 서버 완료 10건에서 prompt eval이 모델 처리 시간의 74.3%(14분 59.5초 / 447,693 토큰).

제품 코드 변경·설치·모델 재로드·커밋은 이번 단계에 포함하지 않는다.

**문서 상태**: 2026-10-19 승인된 최종 실행 계획. 이전 계획 [Harness_Targeted_Engine_Read_Plan_20261018.md](Harness_Targeted_Engine_Read_Plan_20261018.md)의 E0–E7은 §2의 분류를 따른다. 근거: [prefill 파생 분석](evidence/harness-prefill-analysis-20261019.json), [판단·지연 근거 패킷](evidence/harness-behavior-latency-plan-20261019.json). 원본 대화·서버 로그는 배포하지 않는다.

---

## 1. 현재 상태표

### 1.1 완료된 기능 (재구현 금지)
범위 완료 계약(`nextStartLine` 생략), **엔진 파일과 64 KiB 초과 파일**의 암묵적 whole-read 거부, search contextLines/include, read_symbol declaration/member, engine_local signature/full, 편집 실패 후보 범위+hash 제안, `edits[]`, hash/snapshot/receipt 연속 수정, 현재 턴 pinning, repeat receipt, 관련 가이던스. Unreal MCP 0.3.27 / Compactor 0.4.78 rev 125.

### 1.2 확인한 문제 (근거 수준 표시)

| ID | 사실 | 수준 | 근거 |
|---|---|---|---|
| Q1 | 최종 보고에 잘못된 Client RPC P1 잔존, Multicast/저수준 전송 제안 | RuntimeVerified(기록) | 구조화 대화, 분석 JSON `analysis.qualityGate` |
| Q2 | 엔진 Health/Die 가정은 사용자 반박 후 철회 | RuntimeVerified(기록) | 동일 |
| Q3 | 보고 전달됨(`eosFound`, reportDelivered/Completed=true), taskCompleted=false, objectiveSatisfied=null | RuntimeVerified(기록) | `sdkSummary.outcome` |
| R1 | 직전 감사 턴(대화 메시지 3): `read_file` 암묵적 전체 읽기 **15회**(프로젝트 소스 전부, 결과 약 95k자) | RuntimeVerified(로컬 대화 JSON 선택 버전) | 이번 검토의 집계 |
| R2 | 분석 대상 턴(메시지 5 = `cbc227f3`): `read_file_range` **14회**를 1행→EOF 연속 분할로 호출(예: Character.cpp 1–160, 161–300, 301–420; 결과 약 83k자) = 범위 도구로 같은 파일 전부 재읽기 | RuntimeVerified(동일) | 동일 |
| P1 | 10건 prompt eval 74.3%, 요청당 39,959–52,020 처리 토큰, 중앙값 90.60초 | RuntimeVerified(서버 로그) | `backendSummary` |
| P2 | 추정 join 기준 라운드 2–6은 SDK 입력−서버 처리 = **정확히 8,969로 일정**, 라운드 7–11은 0이며 각각 직전 cache entry 회수 | RuntimeVerified(추정 join) | `inferredBackendRoundJoins`, `backendTasks` |
| P3 | 라운드 9: 4,805토큰 생성 115.94초 (반박 가설 재검토) | RuntimeVerified | `backendTasks[7]` |

### 1.3 이번 검토에서 새로 확인한 소스 근거

**F3 — 64 KiB 이하 프로젝트 파일의 암묵적 전체 읽기 레거시 (SourceVerified + R1 사용 기록).**
- [direct-read-capabilities.js:157-158](../lmstudio-unreal-agent-mcp/src/direct-read-capabilities.js:157): 창 인자가 없을 때 거부 조건은 `installed_engine` 또는 `size > wholeReadMaxBytes`뿐.
- [direct-runtime-context.js:41](../lmstudio-unreal-agent-mcp/src/direct-runtime-context.js:41): `wholeReadMaxBytes` 기본 64 KiB(`DIRECT_WHOLE_READ_MAX_BYTES`).
- [direct-tool-catalog.js:204](../lmstudio-unreal-agent-mcp/src/direct-tool-catalog.js:204) 도구 설명이 "whole project ≤64 KiB"를 **모델에게 안내**함.
- [failure-guidance.test.js:287](../lmstudio-unreal-agent-mcp/test/failure-guidance.test.js:287) 테스트 제목이 "small files still read whole"을 계약으로 고정.
- 게임 프로젝트 소스는 모두 64 KiB 이하라 이 경로로 전부 읽힘(R1).

**F4 — 범위 도구를 이용한 전체 순회 (R2).** `read_file_range`에는 순회 감지가 없다. 정상 계약(필요하면 이어 읽기 허용)이므로 버그가 아니라 **행동 문제**다. F3만 고치면 R2 형태로 옮겨갈 수 있다는 근거다.

**F1 — 가변 데이터가 현재 턴의 모든 tool exchange보다 앞에 놓인다 (SourceVerified, RuntimeVerified 아님).**
- [context-manager.ts:262-270](../lmstudio-context-compactor-plugin/src/context-manager.ts:262): `runtimeParts`(note, refs, referenceData, taskData, runtimeFacts)를 **마지막 user 메시지 앞**에 삽입.
- 이 대화는 다중 턴이며 마지막 user 메시지는 메시지 4("Die 없어…")다. 따라서 data slot은 이전 턴 이력 뒤, **이번 실행의 모든 tool exchange 앞**에 있다.
- [continuity-model-notes.js:413](../lmstudio-context-compactor-plugin/src/continuity-model-notes.js:413) `renderAvailableRefs`는 완료된 호출마다 행이 늘어남(최대 16행/2,400자) → note 본문이 absent여도 **매 라운드 변경**. `runtimeFacts`도 budget/projection/stagnation으로 변경([prediction-loop.ts:446,803,1061,1087](../lmstudio-context-compactor-plugin/src/prediction-loop.ts:803)).
- 237줄 주석의 설계 의도("cached system prefix 안정")는 이전 턴까지만 보장하고, 같은 턴 tool loop의 재사용은 보장하지 않는다.
- P2의 일정한 8,969가 data slot 직전까지의 길이(system+tools+이전 턴 이력)와 같은지는 **미측정**이다. tool schema가 6,335토큰이므로 이전 턴 이력이 많이 압축됐다면 일치할 수 있다.
- 기존 테스트 [compaction-retention.test.cjs:80](../lmstudio-context-compactor-plugin/test/compaction-retention.test.cjs:80)은 마지막 user 뒤에 tool exchange가 이어지는 경우를 검사하지 않음.

**F2 — 정확도 가이던스는 이미 있다.** [unreal.md:33](model-guidance/unreal.md:33)(지정자·호출 주체·owning connection, Client RPC 범위 확대 금지), unreal.md:37(ReplicatedUsing/OnRep), [unreal-networking.md:21](model-guidance/unreal-networking.md:21). Client RPC callspace의 `engine_fact`는 `config/unreal_contracts.json`에 없음. **이번 실행 입력에 들어갔는지는 미확인.**

### 1.4 원인 후보 (미확정)
- B-H0 (F3/F4): 소스 전체 읽기로 tool result가 라운드당 입력의 큰 몫을 차지함 → 처리 토큰 증가, projection 유발.
- B-H1 (F1): 같은 턴 안에서 data slot이 바뀌어 현재 턴 prefix 재사용이 끊김.
- B-H2: 라운드 7–11의 재사용 0 — cache entry 회수 / slot 선택(parallel 4, similarity 0.21–0.42) / projection / system 변경 중 무엇인지 모름.
- B-H3: hybrid(qwen35) checkpoint 위치가 prefix가 보존돼도 재사용 길이를 제한할 수 있음.
- B-H4: 불필요한 조사 라운드와 긴 추론(P3).
- A-H1 가이던스 미전달 / A-H2 전달됐으나 기억 우선 / A-H3 형식 검증 통과를 사실 확인으로 오해.

### 1.5 아직 모르는 것
data slot 직전까지의 토큰 수, 인접 라운드의 첫 변경 위치, 라운드 7–11의 원인, runtime/llama.cpp 버전, 실제 K/V 할당 형식, prompt cache 용량, checkpoint 생성·복원 위치, 이번 실행의 가이던스 선택 결과, 라운드 0–1의 서버 수치, R2 순회를 고른 이유(지침·이전 턴 압축·모델 성향).

---

## 2. 기존 계획 재분류

| 항목 | 분류 | 이유 |
|---|---|---|
| E0 기준 고정 | **유지(축소)** | HEAD·identity는 확인됨. Swift 1.5 metadata, S1–S10 fixture만 남음 |
| E1 0건 응답 다음 검색 | **조건부** | 최신 실행에서 owner 오류가 관찰되지 않음. §5 평가에서 R1–R3가 재현될 때만 |
| E2 선언+구현 다음 읽기 | **조건부** | P2로 상당 부분 해결. 잘린 발췌 뒤 전체 재읽기가 재현될 때만 |
| E3 압축 안내 문구 | **조건부** | projection 3회 있었으나 재읽기 원인 증거 없음(S5는 가설) |
| E4-1~10 가이던스·계약 정리 | **대부분 제외** | F2: 규칙 이미 있음. 같은 경고 추가 금지 |
| E4-11/12 네트워크·RepNotify | **유지 → W-A** | 전달 여부 확인 후 조건부 보완 |
| E5 정확도·지연 비교 | **유지(재구성)** | §5·§6으로 대체 |
| E6 회귀 명령 | **유지** | §7로 통합 |
| P1 whole-read 제한 | **완료 + 보완 필요(신규 W-R1)** | 엔진·64 KiB 초과만 다룸. 64 KiB 이하 소스 레거시 남음(F3) |
| E7-1 관측 보완 | **유지(축소)** | 필드 대부분 존재([prediction-loop.ts:480-519](../lmstudio-context-compactor-plugin/src/prediction-loop.ts:480)). data slot 직전 토큰·인접 prefix·backend join 키만 부족 |
| E7-2 replay A–F | **유지, 순서 변경** | C(가변 데이터 위치)를 우선 |
| E7-3-1 조사 라운드 축소 | **유지** | W-R·W-A와 같은 경로 |
| E7-3-2 data slot 위치 | **유지, 조건부 수정 후보** | F1 |
| E7-3-3 working target | **조건부** | W-R1·W-P2 이후에도 처리량이 클 때만 |
| E7-3-4 runtime/cache | **조건부** | 하네스 수정 뒤에도 재처리가 남을 때만 |
| E7-3-5 측정 API 축소 | **제외(현재)** | 실행−모델 시간 차 9.851초 < 1% |
| S1–S10, R14–R23 | **이력** | 서술 기반. 회귀 사례로만 유지 |
| R32–R43 | **유지** | §5·§6 판정 기준 |
| §8.1 9개 우선 파일 | **대체** | §4의 소유자 목록으로 교체 |

---

## 3. 우선순위와 의존 관계

```
W0  기준 고정 (읽기 전용)
 ├─ W-R1 소스 암묵적 전체 읽기 제거 (LM Studio 불필요, 소유자 명확)  ← 첫 제품 변경
 ├─ W-P0 F1 결정적 확인 (모델 불필요)
 ├─ W-A0 가이던스 전달 여부 확인 (기록)
 └─ W-A1 정확도 정답 세트 작성 (fixture)
        ↓ (모두 끝나면 LM Studio 순차 세션)
 W-P1 통제 replay A·B·C(+D·E·F)    W-A2 실모델 정확도 baseline/W-R1 후보 (§5)
   ├─ C 재현 → W-P2 data slot 위치
   ├─ A·B에서 재사용 실패 → W-P4 runtime 조건
   └─ 7–11 원인 → W-P3/W-P4
 W-A2: R2 순회 지속 → W-R2 / 가이던스 미전달 → W-A3a / 전달·오판 → W-A3b 또는 "모델 한계"
 W-E 공동 채택 평가 (AB/BA)
```

**첫 번째로 할 일**
- W-R1: 사용자 결정(소스는 위치 확인 우선)과 F3 근거로 범위가 확정됐다. replay 결과와 무관하다.
- W-P0: 동시에 모델 없이 F1을 재현한다.

**결과에 따른 분기**
- W-R1 후 실모델에서 R2 형태 순회가 나타남 → W-R2 착수.
- W-P1 C에서 끝-배치가 처리 토큰을 줄임 → W-P2.
- 동일 입력 A에서도 재사용 실패 → 하네스로 해결 불가. W-P4로 이동하고 문구 수정 금지.

**제품 코드 변경이 필요 없는 조건**
- W-R2: W-R1 후 순회가 재현되지 않으면 착수하지 않음.
- W-P2: C에서 차이가 없으면 data slot을 바꾸지 않음.
- 정확도: 가이던스가 입력에 들어갔는데도 오판하고 engine_fact 후보도 효과가 없으면 "모델 한계"로 기록.
- E1/E2/E3: 해당 회귀가 재현되지 않으면 착수하지 않음.

---

## 4. 작업별 구현 명세

### W0. 기준 고정 (읽기 전용)
- HEAD/dirty 확인 완료. 측정 전 runtime·llama.cpp 버전, load log의 실제 K/V 형식, prompt cache 한도, parallel/checkpoint 설정을 기록하고 없으면 unknown.
- baseline 실행 중 해당 checkout을 수정하지 않음.

### W-R1. 소스 파일 암묵적 전체 읽기 제거 — 사용자 결정 반영
- **증상**: 관련 파일을 전부 읽어 입력이 커짐(R1). 확인 수준: SourceVerified + RuntimeVerified(사용 기록). 판단: Bug(레거시 계약).
- **소유자**: Unreal Node MCP `readFile` ([direct-read-capabilities.js:143](../lmstudio-unreal-agent-mcp/src/direct-read-capabilities.js:143)). 도구 설명 정본: [direct-tool-catalog.js:204](../lmstudio-unreal-agent-mcp/src/direct-tool-catalog.js:204). 한도 정본: [direct-runtime-context.js:41](../lmstudio-unreal-agent-mcp/src/direct-runtime-context.js:41).
- **최소 변경**
  1. 창 인자가 없는 `read_file`에서 소스 확장자이면 크기와 무관하게 기존 `WHOLE_READ_TOO_LARGE` 거부 경로를 그대로 사용한다(새 errorCode가 필요하면 기존 소비자를 확인한 뒤 결정. 기본은 재사용하고 메시지만 "source file" 사유로 분기). 대안(search_files contextLines / read_symbol declaration / read_file_range)은 기존 형식 그대로.
  2. 소스 판정은 [write-guards.js:19](../lmstudio-unreal-agent-mcp/src/write-guards.js:19)의 `isSourceLikeExt`/`SOURCE_EXTENSIONS`를 재사용. 확장자 목록을 6번째로 새로 만들지 않는다(현재 `automation-source-parser`, `mutation-semantic-guard`, `direct-search-capability`, `engine-source-access`, `write-guards`에 각각 존재). `.inl` 누락 여부는 필요 시 write-guards 정본에서 결정하고 쓰기 정책 영향을 확인한다.
  3. 소스가 아닌 작은 파일(.ini/.json/.uproject/.uplugin 등)은 작은 임계값만 암묵적 전체 읽기 허용. `wholeReadMaxBytes` 기본값을 64 KiB에서 8 KiB로 낮추고 환경변수 재정의는 유지한다(정확한 값은 테스트에서 고정).
  4. 도구 설명에서 "whole project ≤64 KiB"를 제거하고 "source: locate first (search/read_symbol/range); explicit byte window for recovery" 수준으로 바꾼다. tool schema 크기 상한 내에서.
- **보존 계약**: 명시적 `offsetBytes`/`maxBytes` 창(긴 한 줄 복구 경로), 엔진 파일 거부, NOT_FOUND 복구, snapshot/hash 등록, dedupe/repeat receipt, `read_file_range` 범위 완료 계약, 쓰기 권한(읽기 hash는 쓰기 허가가 아님).
- **위험과 대응**: 모델이 `maxBytes=65536`을 일상적으로 붙여 우회할 수 있음 → trace에서 명시적 창 사용률을 측정하고, 남용이 재현되면 W-R2에서 다룸(이번에는 막지 않음). 거부 응답이 추가 라운드를 1회 만들 수 있음 → §6에서 라운드 수와 함께 측정.
- **직접 소비자**: Compactor tool contract 생성물(설명이 생성물에 들어가면 `toolcontract:check`), trace의 whole/window 구분([trace_lmstudio_run.py](../scripts/trace_lmstudio_run.py) `read_shape`), Unity MCP는 별도 provider라 범위 밖(공유 코드 변경 시만 회귀).
- **테스트**: `failure-guidance.test.js:287`의 "small files still read whole" 계약을 "소스는 거부, 작은 비소스는 허용, 명시적 창 허용"으로 갱신하고 406줄 사례도 함께 확인. 경계값(8 KiB ±1), 대소문자 확장자, 확장자 없는 파일.
- **버전**: `VERSIONING.md`에 따라 Unreal Node MCP 패치/마이너 증가(공개 동작 변경).
- **기각·롤백**: 실모델에서 필수 근거 coverage 저하나 전체 시간 악화가 나타나면 임계값만 되돌리는 것이 아니라 결과를 기록하고 보류.

### W-R2. 범위 순회 대응 — 조건: W-R1 후 R2 형태 재현
- 정상 계약이므로 거부하지 않는다. 먼저 trace에서 "같은 파일·같은 버전에 대해 1행부터 연속 범위로 EOF 도달" 비율을 기존 `lines_read`/중복 지표([trace_lmstudio_run.py](../scripts/trace_lmstudio_run.py) `_mark_complete_line_range`, `_mark_rereads`)로 측정한다.
- 재현되면 기존 소유자 하나만 고른다: (a) `read_file_range` 응답의 기존 `suggestion`에서 다음 연속 범위 대신 search/read_symbol을 우선 제안하거나, (b) 기존 core 가이던스 한 줄 정리. 새 순회 차단기·카운터를 만들지 않는다.

### W-P0. F1 결정적 확인 — StaticVerified 목표
- 소유자: `composeModelHistory` ([context-manager.ts:215](../lmstudio-context-compactor-plugin/src/context-manager.ts:215)). 소비자: `createInputAssembler`(330), [round-input.ts:54](../lmstudio-context-compactor-plugin/src/round-input.ts:54).
- `compaction-retention.test.cjs`에 현재 동작을 기록하는 관찰용 사례 추가: system → 이전 턴 → user → (assistant tool request → tool result)×N, 라운드 k/k+1의 refs 차이에서 첫 변경 메시지 index를 확인.
- 토큰 대조: LM Studio 세션에서 data slot 직전까지의 Chat을 `measureContext`로 1회 측정해 8,969와 비교.
- 기각: 첫 변경이 현재 턴 exchange 앞이 아니면 F1을 후보에서 내림.

### W-P1. 통제 replay — RuntimeVerified 목표
- 소유자: 기존 [eval-audit-pressure.cjs](../lmstudio-context-compactor-plugin/scripts/eval-audit-pressure.cjs)(`createPredictionLoopHandler` + fixture read tool)와 `eval-hybrid-context.cjs`. 새 benchmark framework 금지. 새 fixture 모듈은 `audit-pressure-fixture.cjs` 패턴을 따르고 `test/` 밖에 둔다(`run-tests.cjs`가 `test/*.test.(c)js`를 자동 포함).
- 측정: 서버 로그 prompt eval 토큰·ms(1차), SDK first-token 대기(별도 이름), 생성 ms. `modelInputId` ↔ 서버 task는 시각·출력 토큰으로 추정 join.
- 변형(한 번에 하나): A 동일 입력 재전송 → B 끝에 완결 exchange만 추가 → C 현재 위치 vs 끝 위치에서 refs/facts만 변경 → D projection 전/후 → E template의 과거 reasoning 처리 → F 확인된 runtime 설정 1개.
- 고정: 모델·template·tool schema fingerprint·source hash·생성 한도·K/V·parallel·MTP. GPU 동시 사용 금지. 원본 prompt는 로컬 격리, 공유 근거에는 hash·수치만.

### W-P2. data slot 위치 수정 — 조건: W-P1 C 재현
- 소유자: `composeModelHistory` 단일 함수. 직접 소비자: `createInputAssembler`, `round-input.ts:54`, note/refs 렌더러.
- 최소 변경(replay로 하나 선택):
  - C-a: 마지막 user 뒤에 완결 tool exchange가 있으면 runtimeParts를 마지막 완결 exchange 뒤(tail)로. exchange가 없으면 현재 위치 유지(기존 테스트 계약 보존).
  - C-b: 턴 시작 시점 값은 현재 위치에 고정, 이후 바뀐 부분만 tail로.
- 보존 계약: system 안정성, assistant 역할(system/user 승격 금지), tool request/result 쌍 분리 금지, 최신 사용자 요청 우선순위, note 무효화, source history 비영속(테스트 93줄), exact-fit 측정, report continuation.
- 위험: tail에 연속 assistant 메시지가 올 때 template 동작(E로 사전 확인), hybrid checkpoint 위치(B-H3).
- 영향: Compactor src 변경 → `npm run build`의 prebuild generator가 `generated-runtime-identity.ts` 갱신, Compactor 버전 증가. 공개 도구 계약 영향 없음.
- 기각: 처리 토큰 감소 없음, 또는 §5 정확도·보고 계약 회귀.

### W-P3. working target 비교 — 조건: W-R1·W-P2 후에도 라운드당 처리 토큰이 큼
- 40k/56k vs 32k, 24k. 16k는 mandatory floor(현재 11,881–19,296)가 허용할 때만. 재처리량·재읽기·정답을 함께 봄.

### W-P4. runtime/cache 조건 — 조건: A 또는 B에서 재사용 실패, 또는 7–11 패턴 재현
- 한 항목씩: prompt cache 용량, parallel(slot 분산), checkpoint 수. K/V 양자화·Flash Attention 기본 권고 반복 금지. 변경 전 현재 load 설정을 저장하고 복구 가능하게 준비.

### W-A0. 가이던스 전달 여부 확인
- 소유자: [design-guidance.ts:165](../lmstudio-context-compactor-plugin/src/design-guidance.ts:165) 후보 구성, round-input.ts:260-301 선택 telemetry. 라운드별 선택 문서·토큰을 기록에서 확인하고, 없으면 W-A2에서 기록.

### W-A1. 정확도 정답 세트 — §5

### W-A3. 정확도 보완 (조건부, 하나만)
- a) 미전달: 기존 선택 경로 조건만 조정(새 주입 계층 금지).
- b) 전달됐는데 오판: `config/unreal_contracts.json`에 engine_fact 1건(Client RPC callspace — 근거: 설치 UE 5.7 `Actor.cpp` `AActor::GetFunctionCallspace` 5412–5614, 공식 RPC 표)을 추가하고 generator로 파생. 버전·출처·토큰 비용 검증. 효과 없으면 철회하고 "모델 한계" 기록.
- 금지: 매 라운드 엔진 원문·장문 경고 주입, system 경고 반복, 키워드 규칙, 게임 API 하드코딩.

### 보류(재현 시에만): E1(`engine_header_evidence.py`, `direct_rag_engine_local.py`), E2(`engine_definition_evidence.py`), E3(`resume-facts.ts`, `execution-instructions.ts`).

---

## 5. 정확도 평가 명세

**fixture**: 실제 게임을 수정하지 않는 synthetic UE C++ 소스를 eval runner의 fixture read tool로 제공. 정상(N)·결함(D) 쌍.

| 쌍 | 정상 N | 결함 D | 정답 근거 |
|---|---|---|---|
| 1 Client RPC | 서버가 원격 client 소유 Pawn의 `ClientConfirmHit()` 호출 | 서버 소유 AI Actor(소유 연결 없음)에서 같은 호출로 클라이언트 UI 갱신 기대 | UE 5.7 RPC 표, `GetFunctionCallspace` |
| 2 wrapper vs `_Implementation` | 일반 wrapper 호출 | 서버가 `ClientX_Implementation()` 직접 호출 → 서버 로컬만 실행 | UHT 규칙(unreal.md:112), RPC 표 |
| 3 Server RPC 소유 | 소유 client가 Server RPC 호출 | 비소유 Actor에서 Server RPC 호출(드롭) | RPC 표 |
| 4 RepNotify | `DeathCount` → 공용 빈 `OnRep_KillCount`, 소비자 없음 | OnRep body가 다른 필드 갱신 / 필요한 갱신 누락 | 속성 복제 문서 |
| 5 상태 사본 | GameMode 단일 writer → PlayerState 복제 사본 | 서버·클라 양쪽 writer로 불일치 | 소스 writer 경로 |
| 6 Health/Die | 프로젝트 정의 `Health`/`Die()` 존재 | 엔진 기본 Health/Die 가정(없음, `InternalTakePointDamage`는 Damage 반환만) | `Actor.cpp:3484-3487` |
| 7 형식 vs 사실 | 근거 패킷 `structure_valid; facts_not_verified` 뒤 반대 근거 제시 | — | 판정 유지 여부 |
| 8 보고 vs 정확성 | reportDelivered=true와 사실 정확성 별도 채점 | — | outcome 필드 |

**판정**
- 라벨: `defect` / `not_defect` / `needs_runtime_proof` + severity.
- False positive: N에 결함·P0/P1 단정. 결함 누락: D 미검출.
- 소스로 판정 가능한 N에 `needs_runtime_proof`만 붙이면 부분 점수(전부 unknown 처리로 FP를 낮추는 방식 차단).
- 자기 교정: 사용자 반박 없이 반대 근거 확인 후 철회했는지, 철회 후 재등장했는지.
- 읽기 범위 지표(W-R1 평가 겸용): 판정에 필요한 함수 범위 대비 실제 읽은 줄, 암묵적 전체 읽기 수, 명시적 창 사용 수, 1행→EOF 순회 수.
- 통과 기준(제안): N 6쌍 FP 0, D 검출 ≥ 5/6, 쌍 7에서 P1 유지 0. "baseline보다 나쁘지 않음"만으로 통과 불가(baseline에 잘못된 P1 존재).
- dedicated/listen, Blueprint RepNotify 차이는 해당 버전 근거가 있을 때만 별도 사례로.

---

## 6. 성능 실험 명세

- **고정**: 모델 ID·가중치 양자화·context 84,224·K/V(Q8/Q5_1, load log로 확인)·parallel·MTP·checkpoint·template·tool schema fingerprint·source hash·생성 한도·하드웨어.
- **한 번에 바꾸는 변수**: W-R1, W-P1 변형 1개, 또는 W-P2/W-P3/W-P4 후보 1개. W-R1과 W-P2를 같은 비교에 섞지 않는다(W-R1 → 측정 → W-P2 → 측정).
- **측정 경계**: 서버 prompt eval ms·처리 토큰(1차), SDK first-token 대기(별도), 생성 ms, 라운드 수, tool result 합계 문자/토큰, 전체 작업 시간, 첫 유용한 보고 시간. 측정 API 시간은 cost 필드로 별도.
- **warm/cold·순서**: 재로드 직후 cold 1회는 분리. 이후 AB/BA 교차 최소 3회, 채택 후보 5회. 새 세션/이어지는 세션 구분. source catalog cache와 inference cache 구분.
- **목표(제안, 미검증)**: 누적 서버 prompt eval 중앙값 25%↓, 전체 시간 중앙값 10%↓. 참고 계산(보장 아님): 라운드 2–6의 입력 증가분은 라운드당 약 2–4k 토큰이므로, 현재 턴 prefix가 모두 재사용되면 처리량은 증가분+data slot 수준이 됨.
- **불분명할 때**: 범위가 겹치거나 join이 애매하면 "미확정"으로 기록하고 표본을 늘리거나 변형을 더 쪼갬. backend timing이 없으면 미측정으로 두고 SDK 대기로 대체하지 않음.
- **기각**: 처리 토큰이 줄어도 §5 정확도 하락·필수 근거 누락·편집 오류·전체 시간 악화 중 하나라도 있으면 기각.

---

## 7. 후속 구현 검사 목록

| 검사 | 명령 | 증명하는 것 | 증명하지 못하는 것 |
|---|---|---|---|
| W-R1 focused | `node --test lmstudio-unreal-agent-mcp/test/failure-guidance.test.js` 및 bounded-read/engine-source-read 관련 테스트 | 소스 거부·비소스 허용·명시적 창·대안 형식 계약 | 모델이 좁은 읽기를 선택하는지 |
| Unreal MCP 전체 | `node lmstudio-unreal-agent-mcp/test/run-tests.js` | 읽기·편집·snapshot 회귀 없음 | 성능 |
| Compactor focused | `npm.cmd --prefix lmstudio-context-compactor-plugin run build`(prebuild가 generator 실행 → diff 확인) 후 플러그인 디렉터리에서 `node --test test/compaction-retention.test.cjs test/continuity-model-notes.test.js test/input-availability.test.js test/harness-stability.test.cjs` | data slot 순서·refs·역할·비영속 계약 | 서버 cache 재사용, 모델 행동 |
| Compactor suite | `npm.cmd --prefix lmstudio-context-compactor-plugin test` | 입력 조립·종료·보고 계약 회귀 없음 | 성능 |
| 생성물 | `guidance:check`, `toolcontract:check`, `unitycontract:check`, `identity:check` (순차) | 정본→generator→파생물 drift 없음 | 가이던스 준수 |
| Python | `python -X utf8 -m pytest -q` (trace 변경 시 `tests/test_trace_lmstudio_run.py` 먼저) | 계약·trace 파서 | 실제 엔진 MCP |
| Unity | 공유 코드(shared-tool-core) 변경 시에만 Unity Node 회귀 | 공유 계약 회귀 없음 | — |
| CI | 새 테스트가 기존 runner에 자동 포함되는지, fixture 모듈이 test로 오인되지 않는지 | 환경 재현성 | 정확도·속도 |
| 실모델 | §5·§6 runner, AB/BA | 행동·처리 토큰 변화 | 다른 감사 작업·다른 모델로의 일반화 |

공유 dist/build/generator는 순차 실행. 통과한 검사를 이유 없이 반복하지 않음. 문구 assertion 통과를 행동 개선으로 보고하지 않음.

---

## 8. 최종 완료 조건과 남은 한계

**완료 조건**
- [ ] W-R1 적용: 소스 암묵적 전체 읽기 거부, 비소스 작은 임계값, 도구 설명 갱신, 기존 확장자 정본 재사용, Unreal MCP 테스트·버전 갱신.
- [ ] W-P0: F1 재현 여부와 data slot 직전 토큰 vs 8,969 대조 기록.
- [ ] W-P1: A·B·C 최소 3회 AB/BA, 서버 처리 토큰 기준 결과(추정 join 표시).
- [ ] W-P2: C 재현 시에만 적용. focused·suite·생성물 검사 통과, 버전·identity 갱신.
- [ ] W-A0/A2: 가이던스 전달 여부와 §5 baseline·후보 결과.
- [ ] W-R2·W-A3: 조건 충족 시 하나씩, 아니면 미착수 사유 기록.
- [ ] §5 통과 기준과 §6 목표를 같은 표본에서 함께 보고하고, 미달은 미달로 보고.
- [ ] 각 변경의 소유자·보존 계약·단일 writer·생성물 경로 기록. 커밋·설치 없음.

**남은 한계**
- 서버·SDK 공유 request ID가 없어 join은 계속 추정.
- `cachedPrefixTokens`는 SDK에서 unknown, runtime checkpoint 동작은 로그로만 추정.
- W-R1은 전체 읽기를 막을 뿐이며, 모델이 무엇을 읽을지 고르는 판단(R2 순회 포함)은 실모델 평가로만 확인 가능.
- synthetic fixture는 실제 엔진 MCP·멀티플레이 런타임 검증이 아님.
- 한 모델(Swift 1.5 / qwen35 Q4_K_S) 결과를 다른 모델에 일반화하지 않음.

## 9. 실행 결과 (2026-10-19, 미커밋)

### 9.1 적용한 변경

| 작업 | 변경 | 소유자 |
|---|---|---|
| W-R1 | 창 없는 `read_file`은 C/C++/C# 소스를 크기와 무관하게 거부하고, 비소스는 8 KiB 이하만 통째로 읽음. 도구 설명에서 "≤64 KiB" 제거. 확장자 정본은 `write-guards.js`의 `isSourceLikeExt` 재사용 | Unreal MCP `direct-read-capabilities.js`, `direct-runtime-context.js`, `direct-tool-catalog.js` (0.3.28) |
| W-R2 | 완료된 소스 범위 뒤에 줄이 남으면 `afterRange: { owed: false, locateNext: [search_files, read_symbol] }`. 대안 목록은 거부 응답과 같은 `locateAlternatives` 하나에서 생성 | `direct-read-capabilities.js` |
| W-P2 | 가변 데이터(refs·runtime facts·task·note)를 **append-only 블록**으로 바꿈. 한 번 보낸 블록은 같은 자리에 두고, 새로 바뀐 부분만 최신 교환 앞에 추가. 입력 끝에는 두지 않음(template이 끝의 assistant 메시지를 이어 쓸 답변으로 처리). 보낸 블록은 실행 범위 `shownDataBlocks`에 두고 라운드 입력이 확정되면 기록 | `composeModelHistory`, `createInputAssembler`, `prepareRoundInput`, `prediction-loop` |
| W-A3a | 주제 분류: "네트워크"·RPC·RepNotify 인식, 3개 이상 주제는 앞의 2개 사용, 후속 요청은 직전 요청의 명시 주제를 이어받음. 지침은 한 실행 안에서 고정(sticky). 반환 소스의 RPC·복제 지정자로 multiplayer 보조 주제를 고르고, 주제가 바뀌면 한 번만 재선택 | `guidance-topic-selection.ts`, `reference-context.ts`, `round-input.ts`, `context-manager.ts` (0.4.79 / rev 126) |
| W-A3b | 엔진 사실 `ue.net.client-rpc-callspace`(UE 5.8 `Actor.cpp` `AActor::GetFunctionCallspace` 5606·5627·5664) 추가, 계약 revision 3→4 | `config/unreal_contracts.json` → generator → `unreal-networking.md#engine-defaults` |
| 회귀 정리 | W-R1에 맞춰 recovery 평가 시나리오와 e2e 테스트가 명시적 창을 쓰도록 수정, 설치 버전 기대값 2.1.28, 평가 사례 세트 계약 revision @4 | 각 테스트·config |

### 9.2 성능 실측 (W-P1) — [근거](evidence/harness-prefix-replay-20261019.json)

같은 합성 7라운드 도구 루프(라운드당 결과 약 4.4k 토큰), Swift 1.5 / qwen35 Q4_K_S / context 84,224 / parallel 4, AB/BA 교차.

| 배치 | 서버 처리 토큰 중앙값 | 서버 prompt eval 중앙값 |
|---|---:|---:|
| A 기존(최신 user 앞) | 126,818 | 132.6초 |
| B 데이터 위치만 이동 | 125,238 | 130.7초 |
| P 끝에만 추가(데이터 없음) | 30,986 | 34.3초 |
| **C 최종 구현(append-only 블록)** | **31,697 (−75%)** | **35.0초 (−74%)** |

- 확인된 사실(RuntimeVerified, 통제 replay): 이 runtime은 **이전 입력이 다음 입력의 정확한 앞부분일 때만** prefix를 재사용한다. 데이터를 옮기거나 다시 쓰면 거의 전부를 재처리한다. 계획의 B-H1·B-H3이 함께 확인됐다.
- 실제 도구 루프(정확도 평가 실행)에서도 라운드 SDK 입력 수천 토큰 중 서버 신규 처리는 수십~수천 토큰으로 관찰됐다(정성 확인).
- §6 목표(누적 prompt eval 25%↓)는 통제 replay에서 충족. end-to-end 감사의 전체 시간 10%↓는 아직 미측정.
- W-P3(working target)·W-P4(runtime 설정)는 조건(재처리량이 여전히 큼, 동일 입력 재사용 실패)이 성립하지 않아 착수하지 않았다.

### 9.3 정확도 실측 (W-A2) — [근거](evidence/harness-accuracy-eval-20261019.json)

§5 정상·결함 13사례 × 지침 off/auto. 회차마다 하네스 상태가 달랐으므로 같은 설정의 반복 표본이 아니다.

| 회차 | 조건 | off | auto |
|---|---|---:|---:|
| 1 | core 지침만(0라운드 선택 고정) | 11/13 | 11/13 |
| 2 | + 네트워크 증거 신호·재선택 | 13/13 | 11/13 |
| 3 | + 이전 턴 엔진 조회(5.8) | 10/13 | 11/13 |

- 원래 문제(정상 server→owning client Client RPC를 결함 판정)가 재현됐다: off 2/3, auto 1/3.
- auto는 §5 통과 기준을 한 번도 충족하지 못했다. 지침 전달이 정확도를 확실히 개선한다는 근거는 없다. 계획에 따라 **모델 한계로 기록**한다.
- `engine-defaults`(새 Client RPC 사실 포함)는 실제 루프에서 한 번도 들어가지 않았다. 이 섹션은 버전이 확인돼야 들어가는데, 이전 턴의 엔진 관찰은 현재 실행에 적용되지 않는다. 따라서 W-A3b 사실의 효과는 **미검증**이다.

### 9.4 검사 결과

| 검사 | 결과 |
|---|---|
| Compactor `run-tests.cjs` | 842/842 통과 |
| Compactor generator `--check` 4종 | 통과 |
| Unreal MCP `run-tests.js` | 334 통과 / 2 skip / 실패 0 |
| Python 전체 | 병합 전 HEAD에서도 같은 설치 환경 실패 17건(`npm.CMD run patch:sdk` 하위 셸 node 미검출) 외 통과 |

### 9.5 남은 작업과 한계

- 커밋·설치·재로드는 하지 않았다. 설치된 Compactor/MCP는 아직 이전 버전이다.
- 버전 확인 엔진 섹션이 실제 세션에서 들어가려면, 실행 안에서 `engine_local` 조회나 정적 검증으로 엔진 identity가 관찰돼야 한다. 이전 턴 관찰을 이어받을지는 별도 설계 결정이다(엔진 교체 위험).
- 정확도는 표본이 작고 회차 간 변동이 크다. 같은 설정의 반복 평가(최소 3회)가 필요하다.
- end-to-end 감사 작업의 전체 시간 비교는 하지 않았다.

## 계획 작성 중 수행한 확인 (사실)
HEAD·작업 트리, 기존 계획 MD 전체, 분석 JSON(summary/analysis/joins/tasks/load config), 로컬 대화 JSON 선택 버전의 도구 호출 집계(메시지 3·5, 실행 ID 위치), `composeModelHistory`·`renderAvailableRefs`·`renderTaskProgress`·runtimeFacts writer·`round-loop.ts:281`·라운드 관측 필드·기존 배치 테스트, `readFile` whole-read 분기·기본 한도·도구 설명·관련 테스트·확장자 정본 후보, RPC/RepNotify 가이던스, eval runner·test runner 구조. 제품 코드 수정·테스트 실행·모델 호출은 하지 않음.
