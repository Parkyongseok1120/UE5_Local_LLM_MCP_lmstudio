# 대화 1793426512873 실행 검증 — 도구 호출·사고·압축·결과물

- 작성일: 2026-11-01, Asia/Seoul.
- 대상 실행: LM Studio 대화 `1793426512873`, Develop `b548e3e` 설치본(loaded = installed `ef-build-v2-54d5cd9c…`, revision 133).
- 확인 수준: 실행 수치는 대화 JSON의 debug 이벤트로 집계(RuntimeVerified). 보고서 주장은 프로젝트·엔진 소스 대조(SourceVerified). 재현 테스트와 새 모델 실행은 하지 않았다.
- 원본 대화와 게임 소스는 저장소에 복사하지 않는다. 이 문서는 수치와 위치만 기록한다.
- 관련 문서: [계획 재조사 결과](Harness_Compactor_Contract_Refactor_Plan_Review_20261031.md).

## Context
- 사용자 요청: 새로 설치한 빌드(Develop `b548e3e`)로 실행한 LM Studio 대화 `1793426512873`이 정상적인 도구 호출, 사고 과정, 압축 과정을 거쳐 맞는 결과물을 냈는지 확인한다.
- 대화 내용: 두 번째 턴에서 "캐릭터·무기·체력·RPC 계약, SSOT/SOLID/수명관리/UE 매크로 오류 찾아서 보고"를 요청했다. 대상은 `UE5_Latency-Resilient-Multiplayer-Combat`이고, 소스는 `8c12c6a`(2026-10-30)로 실행 시점 이후 변경이 없다.
- 확인 방법:
  - 대화 JSON의 debug 이벤트 615개와 도구 요청/결과 90쌍을 라운드별로 집계했다.
  - 보고서 주장 7개와 "비이슈" 6개를 프로젝트 소스와 UE 5.7 엔진 소스로 대조했다.
  - 모두 읽기만 했다.

## 1. 실행 경로 (정상)
- **빌드:** loaded = installed = `ef-build-v2-54d5cd9c…`(revision 133)로, 새 설치본이 실제로 로드됐다. 모델은 `swift-1.5-qwen3.8-27b`, context는 84,224이다.
- **도구 호출:**
  - 52라운드 동안 90건을 호출했고, 모두 읽기 전용이었다(list/read/read_range/search).
  - SDK 요청 실패, raw tool text, 미실행 호출은 없었다.
  - R6에서 4개 파일을 한 번에 읽다가 공유 예산 부족으로 2건이 거부됐다(`held_reservations_insufficient`). R7–R8에서 범위를 나눠 다시 읽어 정상 회복했다.
- **재읽기:**
  - 43개 파일을 81회 읽었다.
  - 같은 hash를 다시 읽은 약 25건은 ledger에 모두 `restoreRequiredReads`로 분류됐다. 압축으로 본문이 빠진 뒤의 복원이고 `redundantRereads`는 0이다. 설계대로 동작했지만 비용이 크다.
- **압축:**
  - 압축은 6회(R12/20/25/30/39/44) 일어났고, 모두 `final_exact_low_water`이다. 압축 직후 측정값은 42.6–47.5K로 effective low 47,719 이하였다.
  - hard ceiling 73,984를 넘은 적은 없다. 측정 fingerprint와 tool surface는 52/52 일치했다.
  - 압축으로 low를 맞춘 직후 guidance가 `sticky_readmitted`로 3.2–6K 토큰 다시 입장해서, 최종 입력은 48–53.6K로 low를 넘었다(감사 계획의 C1/C3와 실측 일치).
  - `postCompactionInputTokens`에 optional이 포함되고, `targetMet`은 null이다.
  - `runtimeDataSupersededChars`는 R45에서 8,925까지 누적됐다.
  - architecture view는 25라운드에서 `omitted_by_shared_admission`이었다.
- **사고:** 총 104K자, 52블록이다. 마지막 라운드는 16K자를 쓰고 `eosFound`로 정상 종료했다. 반복 루프나 내부 frame echo는 없었다(`suppressedInternalProtocol` false).
- **종료 상태:**
  - `completed`, 보고서 전달이 완료됐다(4,451자). note 4건을 저장했고, window seal도 정상이다.
  - 비용은 prompt 2.82M 토큰, 약 40분이다.

## 1-1. 압축 직후 말고도 누적 증가가 있었나 — 있음 (3가지)
라운드별 수치(토큰): floor는 mandatory floor, rtData는 runtime data block, sup은 superseded 문자 수다.

| 압축 시점 | R12 | R20 | R25 | R30 | R39 | R44 |
|---|---|---|---|---|---|---|
| mandatory floor | 21,349 | 22,379 | 21,137 | 24,475 | 26,560 | **30,540** |
| 압축 직후 history | 31,534 | 34,415 | 35,754 | 30,869 | 35,597 | 36,200 |
| 압축 직후 메시지 수 | 21 | 26 | 26 | 26 | 28 | 30 |

1. **mandatory floor가 실행 내내 상승했다(21.3K → 30.5K, 약 +9K).**
   - tool schema는 5,689로 고정이고, 현재 runtime data는 3.6–4.5K로 거의 그대로다(ledger가 22→41개 파일로 늘어 9.3K→13.3K자, 약 +1.2K 토큰).
   - 나머지 약 +8K는 압축할 수 없는 history 쪽에서 늘었다. 고정 상한 35,789자의 pinned working set, 남겨 둔 요청/결과 골격, 직전 교환이 여기에 들어간다.
   - `conversation_prose_archived`는 R12/R20/R25 이후 한 번도 일어나지 않았고, 압축 직후 메시지 수도 21→30으로 늘었다.
   - 현재 telemetry에는 floor 구성 분해가 없어서 항목별 비중은 **측정 불가**다(감사 계획 §8의 입력 manifest 필요성과 같은 문제).
   - 영향: 압축 한 번으로 확보되는 여유가 점점 줄어든다. reclaimRatio가 0.25에서 0.16으로 떨어졌다.
2. **압축 사이 구간에서 runtime data가 톱니형으로 쌓였다.**
   - 매 라운드 이전 사본이 superseded로 남는다(R12→R15에서 rtData 3.6K→11.7K 토큰, sup 0→13,846자. R30→R33, R39→R42도 같은 패턴).
   - rewrite나 consolidation 때만 0으로 돌아가며, 정리 직전에는 runtime data의 절반 이상이 이전 사본이다.
   - 감사 계획 C2/X1(현재 view와 누적분의 교체 규칙 혼합)이 실제 실행에서 재현된 것이다.
3. **guidance가 진동했다.**
   - high-water에 가까워지면 `budget_omitted`로 0이 되고(R18/19, 23/24, 28/29, 36, 42/43, 51), 압축 직후 `sticky_readmitted`로 3.2–6K가 다시 들어온다.
   - 압축으로 얻은 공간을 optional이 먼저 차지하기 때문에, 다음 압축이 그만큼 앞당겨진다(C1).
- hard ceiling은 끝까지 넘지 않았다(최대 63,584 < 73,984). 위 3가지는 넘침이 아니라 **공간 효율 저하**다.

## 1-2. 취소된 도구 호출 (R6) — 의도된 거부 1건과 하네스 결함 1건
R6에서 4개 파일을 한 번에 읽으려 했다. 이때 입력이 이미 51K라 batch 예산은 10,032였다.

| 호출 | 결과 | 판정 |
|---|---|---|
| CombatPlayerController.h | 정상 | — |
| CombatPlayerController.cpp | 요청 17,295 → 8,180 bytes로 축소, 1–155행만 반환 | 정상(R7에서 156–238 이어 읽기) |
| CombatGameState.h | 요청 6,144 → host가 **2,050**으로 축소 → provider가 `OUTPUT_LIMIT_EXCEEDED`(메타데이터만으로 2,155 필요) | **하네스 결함**: host 최소값(2,048, reserve floor 1,844 토큰)이 provider의 실제 최소 envelope보다 작아서 실패가 확정된 호출을 보냈다 |
| CombatGameState.cpp | dispatch 전 거부 `held_reservations_insufficient`("0 of 10032 remain") → UI에 "도구 실행 거부(canceled)" 표시 | 의도된 예산 거부다. 다음 라운드 재요청 안내대로 R8에서 회복했다 |

- 비용: 낭비된 호출 2건과 추가 라운드 1–2회. 결과물 정확도에는 영향이 없다.
- 개선 후보:
  - (e) floor로 축소된 byteBudget이 provider가 밝힌 최소값(경로 길이에 따라 다른 메타데이터 크기)보다 작으면 dispatch하지 않고 같은 거부 경로로 보낸다. 또는 floor 계산에 provider envelope 최소값을 반영한다.
  - (f) 예산 거부를 UI에 "canceled"가 아니라 "다음 라운드로 미룸"으로 표시한다.

## 2. 결과물 정확도 (부분적으로 틀림)

| # | 보고서 주장 | 판정 | 근거 |
|---|---|---|---|
| 1 | SetScenario 부분 변경 + bEnabled 이중 SSOT | 부분 정확 | 이중 SSOT는 사실이다. 실패 경로는 revision 0의 무효 PktLag일 때만 생기는 좁은 경우다 |
| 2 | InitNewPlayer 시점에 PlayerState가 없어 첫 스폰이 팀 스폰이 아님 | **틀림** | UE 5.7 `GameModeBase.cpp:721`(SpawnPlayerController)→`PlayerController.cpp:1080`(InitPlayerState)→`:731` InitNewPlayer 순서라 PS가 이미 존재한다 |
| 3 | OutOfOrderTime 허용 오차 없음 | 사실이나 과소평가 | offset이 매 샘플마다 통째로 교체되고(`TimeSync.cpp:59`), `:245` FireRate 검사가 더 자주 거부한다 |
| 4 | 트랜시언트 DataAsset | 부분 정확, 줄 번호 틀림 | 실제 위치는 `.cpp:40`이고, 누락 시 쓰는 정당한 fallback이다 |
| 5 | TimeSync `SetIsReplicatedByDefault`는 죽은 코드, 제거 권장 | **틀림·유해** | Server/Client RPC(`.h:23-26`)가 있어 필수다. 보고서의 비이슈 표(ClientReceiveTime=Client RPC)와도 자기모순이다 |
| 6 | Move/Look만 시나리오 입력 차단 안 됨 | 오해 소지 | `CombatScenarioComponent.cpp:352-353`의 SetIgnoreMove/LookInput으로 실제로는 막힌다 |
| 7 | 벽에 ApplyPointDamage | 정확, 영향 낮음 | — |

- 비이슈 6개 중 5개는 정확하다. AcceptSample은 클라이언트가 보낸 `Response`를 그대로 통계에 반영한다.
- "UE 매크로 오용 없음"과 "DOREPLIFETIME 짝 일치"는 정확하다.
- 보고서가 놓친 문제:
  - 지연이 RTT ≥ ClockSyncInterval이면 시간 동기 응답이 전부 stale이 되어 발사가 막힐 수 있다(`TimeSync.cpp:53`).
  - 시나리오 통계가 클라이언트 보고값이다(`CombatScenarioComponent.cpp:190`).
  - `OnRep_Experiment`가 기본 `FPacketSimulationSettings`로 PktLoss 등 기존 설정을 지운다(`CombatGameState.cpp:93-95`).
  - StartPass가 SetExperiment 결과를 무시한다(`:135`).
  - 예측 가능한 spread seed가 있다.
  - float 타임스탬프를 double 월드 시간과 비교한다.
- 표현상 문제: "(round 34 확인)"처럼 내부 라운드 번호가 노출됐고, 한자 "校正"이 섞였다. 인용 코드에 주석을 덧붙여 `suggested_code_scope_notice`가 인용을 "제안 코드 1개"로 분류했다.

## 3. 하네스 관점 원인
- **엔진 주장 검증 공백:**
  - 모델은 엔진 소스, RAG, symbol을 한 번도 조회하지 않고 엔진 수명 순서에 기대는 주장(#2)을 P2로 냈다.
  - `engine_claim_check`는 callspace fact 하나만 21회 `unrated`로 기록했고 사용자 보고에는 표시하지 않았다. GameMode Login/InitPlayerState 순서는 fact catalog에 없다.
- **자기모순 미검출:** 같은 보고서의 #5("복제 불필요")와 비이슈 표("Client RPC")가 충돌하는데 감지하지 못했다.
- **압축 직후 optional 재입장:** 감사 계획 R3(C1/C3)이 그대로 재현됐다.

## 4. 후속 하네스 개선 항목
이번 문서는 기록만 한다. 각 항목은 감사 계획의 해당 R단계에 넣는다.

- (a) 엔진 동작에 기대는 P1/P2 주장에 엔진 근거가 없으면 보고에 "엔진 미검증"을 표시한다. `engine_claim_check` gap을 사용자 보고까지 연결한다. (R6)
- (b) 엔진 fact catalog에 `AGameModeBase::Login`→`InitPlayerState`→`InitNewPlayer` 순서와 "RPC가 있는 component는 replicated여야 함"을 추가한다. (R5/R6)
  - **반영(2026-11-01):** engine fact `ue.gameframework.login-playerstate-order`(UE 5.7/5.8 근거, claimMarkers 포함)와 review policy `policy.claims.replication-removal`을 `unreal_contracts@7`에 추가했다. component RPC의 복제 필요성은 엔진 소스 한 줄로 확정되지 않아 engine fact가 아니라 리뷰 정책(RPC·Replicated 속성 확인 후 판단)으로 기록했다.
- (c) 보고서 내부 모순(제거 권장 대상이 다른 항목의 근거와 충돌) 검사를 리뷰 마무리 단계 후보로 둔다. (R6)
- (d) 이 실행의 R12–R45 수치를 R3 압축 순서 수정(C1/C3)의 실측 재현 근거로 쓴다. (R3)
- (e) floor로 축소된 byteBudget이 provider 최소 envelope보다 작으면 dispatch하지 않는다. 또는 floor 계산에 그 최소값을 반영한다(1-2절). (R3)
  - **반영(2026-11-01):** BudgetBroker가 실행 중 provider별로 받은 read 결과의 메타데이터 크기(본문 제외 + provider 예약 256 bytes)와 provider가 밝힌 `OUTPUT_LIMIT_EXCEEDED` 최소값을 기억해 byteBudget 최소값으로 쓴다. 이 실행의 최대값은 2,513 bytes로 R6에 필요했던 2,155를 덮으므로, 같은 호출은 dispatch 전에 예산 부족으로 미뤄진다.
- (f) 예산 거부를 UI에 "canceled"가 아니라 "다음 라운드로 미룸"으로 표시한다(1-2절).
  - **반영(2026-11-01):** 공유 읽기 예산 거부(`held_reservations_insufficient`, `remaining_capacity_insufficient`)는 `done` 상태와 "도구 실행을 다음 라운드로 미룸" 문구로 표시한다. 범위·프로필·사용자 거부는 기존처럼 `canceled`다.
- (g) mandatory floor 구성 분해 telemetry(pinned, 골격, 직전 교환, runtime)를 추가하고, prose archive가 R25 이후 멈춘 원인을 조사한다(1-1절). (R3/§8)

## 검증
- 이 문서의 수치는 대화 JSON에서 Python으로 집계했다(읽기 전용).
- 주장 판정은 프로젝트 `8c12c6a` 소스와 `${UNREAL_ENGINE_ROOT} (UE 5.7)` 엔진 소스의 해당 줄을 직접 확인한 결과다.
