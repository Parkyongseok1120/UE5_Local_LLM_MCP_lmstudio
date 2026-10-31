# 실행 1793194960145 분석 및 수정 계획

- 작성: 2026-10-28, Asia/Seoul. 범위: 실제 실행 분석과 구현 계획. 이번 작업에서는 제품 코드·설치본·사용자 프로젝트를 수정하지 않는다.
- 기준: `codex/harness-output-contract-fixes`, HEAD `2b13b38c5644b9072e04c488e642205b46cbc804` 및 현재 미커밋 아키텍처 기능 변경. HEAD만 체크아웃하면 이번 분석 대상과 달라진다.
- 실행 제품: compactor 0.4.86 / revision 133. 로그의 loadedBuildId와 installedBuildId가 `ef-build-v2-1aac031f14475d8566bd5dc952c420eb857c92c86bc925e74c763564bfe2666c`로 일치한다.
- 원본은 사용자 로컬에 유지한다. 이 문서의 `<local-diagnostics>/harness-run-1793194960145`는 로컬 진단 폴더를 의미한다. 게임 소스·대화 본문·연결 설정을 저장소 fixture로 복사하지 않는다.
- 근거 패킷: [harness-run-1793194960145-audit-plan.json](evidence/harness-run-1793194960145-audit-plan.json). 기존 [아키텍처 기능 명세](Harness_Architecture_Awareness_SOL_Spec_20261028.md)와 [구현 기록](Harness_Architecture_Awareness_Implementation_20261028.md)에 대한 보완 계획이다.

## 1. 판단과 실제 영향

이번 실행의 주된 실패는 재조회가 아니다. 새 아키텍처 참고 프로토콜을 모델이 반복 생성했는데, 기존 출력·복원 필터가 이를 내부 데이터로 인식하지 못했다. 하네스는 이 텍스트를 보고서 본문으로 취급하여 이어쓰기 입력에 다시 넣었다. 이어쓰기 역시 출력 한도를 소모하고 종료했다.

아키텍처 참고 카드의 선택과 실제 전달도 달랐다. 7개 `architecture_context` 이벤트 모두 요청한 패턴이 있었으나 `admittedPatternIds=[]`였다. 따라서 이 실행으로 새 구조 검토 기능이 실질적으로 개선됐다고 판단할 수 없다. 기존 네트워크 지침과 구조 baseline은 별개이며, 전체 지침이 전혀 없었다는 뜻은 아니다.

| 항목 | 관찰값 | 해석 범위 |
|---|---:|---|
| 검토 요청의 모델 라운드 | 11 | research 10 + output recovery 1 |
| 도구 호출 | 25 | 최초 프로젝트 위치 질문의 1회는 제외 |
| 프로젝트 본문 읽기 | 16개 파일, 1,466줄 | 엔진 본문 읽기는 0회 |
| 도구 오류 / 재조회 줄 / 중복 전달 | 각각 0 | 이전의 재조회 문제가 이번 실패의 주원인이라는 근거 없음 |
| 실행 시간 | 793.9초 | 약 13분 14초; 입력 처리 265.5초, 생성 521.0초 |
| 누적 prompt / 생성 토큰 | 439,698 / 27,823 | prompt 값은 각 라운드 합이며 동시 컨텍스트 크기가 아님 |
| 마지막 두 라운드 생성 | 8,192 + 8,192 | 총 생성 토큰의 약 58.9%; 둘 다 한도 종료 |
| 저장된 표시 블록 step162 / step173 | 11,960 / 21,230자 | 내부 아키텍처 형식을 반복; 원시 fragment 사용량과 표시 블록 길이는 다른 지표 |
| 출력의 헤더 반복 간격 | 2,392자 | 현재 일반 반복 검사 최대 블록 2,000자보다 큼 |
| 구조 패턴 실제 전달 | 7개 이벤트 모두 0개 | 카드가 선택됐다는 로그만으로 전달을 주장할 수 없음 |
| 모델 note | 11개 라운드 모두 absent | 유효한 v2 판단이 저장 단계에서 유실됐다는 증거는 없음 |
| 최종 상태 | truncated, taskCompleted=false | 완료를 거짓으로 승인한 것은 아니지만 reportPresent/reportDelivered는 true |

정상 보호도 확인된다. 복구는 1회에서 끝났고 도구 오류·무단 쓰기·재조회는 관찰되지 않았다. 종료 시 window seal과 release가 성공했고 cleanupFailures는 비어 있다. 세 번의 system 변경에는 엔진 적용성 변경과 단계 전환이 포함되므로 전부 불필요한 prefix 변경으로 집계하지 않는다.

## 2. 확인한 문제

### F1 — 내부 프로토콜 생산자와 소비자의 계약이 분리됨: P1 / Bug / RuntimeVerified

`architecture-input.ts:25`는 새 `[Architecture reference data …]` 형식과 v2 footer 사용 설명을 만든다. 반면 `internal-data-contract.js:5–6,73`의 공유 식별·과거 정리 규칙은 Derived 형식만 알고 있다. `internal-data-output.ts:59–75,198–202`도 새 형식을 처리하지 않는다. `context-manager.ts:360–362`는 문자열 시작으로 private part를 판별하므로 독립 architecture 블록은 등록되지 않으며, Derived와 합친 문자열에 포함돼도 architecture 하위 블록의 분류는 생기지 않는다.

경로: renderer → `round-input.ts:434–440` 문자열 결합 → composer private parts → stream/output filter → 사용자 표시. 실제 step162/173의 출력과 독립 재현이 일치한다. 합성 기존 Derived 165자는 전부 제거되지만, renderer가 실제 만든 architecture 1,634자는 전부 통과한다. modelInputId와 handle을 바꾼 1,635자도 전부 통과한다. 결합 블록에서는 Derived만 제거된다.

실제 출력의 modelInputId는 해당 라운드와 다르고 일부 handle은 임의 값이다. 따라서 정확한 입력을 그대로 복사한 유출만으로 설명하지 않는다. **내부 프로토콜을 변형해 생성한 출력도 허용되는 결함**이다. 단순한 exact string 삭제로 고치면 재발한다. 모델이 처음 반복을 시작한 심리적·확률적 원인은 이 소스 재현만으로 확정하지 않는다.

### F2 — 잘못된 출력을 보고서로 재주입하고 보존함: P1 / Bug / RuntimeVerified

`delivery-controller.ts:305–360`은 도구 요청이 없고 보이는 문자열이 있으면 출력 한도 위치를 `visible_report`로 분류한다. `prediction-loop.ts:1545–1554`는 그 본문을 보고서 prefix로 보관하고 다음 입력에 append한다. 이번 실행 step166은 `continue_report`, `partialReportIncludedInInput=true`를 기록했고, step177/180은 다시 한도 종료했다.

F1의 누락이 이 경로에 잘못된 입력을 공급한다. 동시에 소비자가 일반 문자열만 받아 provenance와 필터의 분류 결과를 강제하지 않는 계약도 보완해야 한다. `includePriorReport`는 이미 전달된 정상 보고서를 보존하려는 보호지만, 현재는 내부 텍스트도 reportPresent/reportDelivered로 승격한다. taskCompleted=false 보호는 유지된다.

과거 정리도 같은 누락을 갖는다. `working-context.js:558`과 `continuity-assistant-evidence.js:20–21`은 공유 sanitizer를 사용하나 그 sanitizer가 새 형식을 모른다. 독립 재현에서 실제 표시 블록은 각각 11,960/21,230자 그대로 통과했고 `retainVisibleResponse`도 반복 헤더 5개/9개를 보존했다. **다음 실제 사용자 턴에서 이 텍스트가 복원됐는지는 미실측**이다. 저장/복원 경로의 통과 재현과 다음 턴 오염 실측을 구별한다.

### F3 — 내부 프레임 반복을 일반 짧은 문자열 반복기로만 감시함: P2 / Bug / TestVerified

`prediction-stream.ts:104`의 일반 반복기는 최대 2,000자 블록을 검사한다. 이번 2,392자 반복은 3회 조건을 넘겨도 검출되지 않는다. 독립 재현은 실제 블록을 16자씩 공급했으며 두 표시 블록 모두 false였다. 기존 반복 감지 자체가 없는 것은 아니다. 또한 `prediction-loop.ts:721–727`은 recover 설정에서도 visible 반복에는 경고만 유지한다. 따라서 감지 상한만 늘려도 이번 반복의 중단까지 해결되지는 않는다. 범용 검사 상한 확대의 처리 비용과 일반 코드/표 오탐을 피하면서, 알려진 내부 프레임의 무진행 신호와 그 중단 정책을 기존 detector/loop에 함께 연결해야 한다.

### F4 — 후보 공동 배분의 설계 격차와 카드 생략 사유 누락: P2 / Bug(계측), 설계 보완(배분) / RuntimeVerified

`design-guidance.ts:165` 이후는 최대 절 수 안에서 기존 지침 pack을 먼저 만든다. 그 뒤 `round-input.ts:435`는 `6 - candidate.ids.length`만큼만 패턴을 허용한다. 이 실행에서는 지침 ID가 6개인 후보가 유지되었고 상세 패턴은 0개였다. 요청은 component/event → asset/component → definition/asset 등으로 바뀌었지만 실제 전달은 계속 0개였다.

`round-input.ts:561–564`는 architecture view가 들어갔는지만 보고 omittedReasons를 빈 배열로 만들 수 있다. 카드가 0개인데 카드별 탈락 원인이 없다. sharedReferenceAddedTokens는 전체 참고 자료 증가량이므로 architecture 고유 비용으로 읽으면 안 된다. 기존 명세에서 카드는 optional/최대 2개이므로 0개라는 사실 자체가 계약 위반인 것은 아니다. P2 결함은 탈락 사유의 계측 누락이며, 배분은 관련 카드와 선택 보완 절을 공동 비교하지 않는 설계 격차다. `design-guidance.ts:265–267`은 supplement도 minimum에 포함하므로 minimum이라는 변수명만으로 필수 안전 계약을 판정해서는 안 된다. 6개 상한과 공용 allowance를 지킨 것은 정상이며, 단순 상한 증대나 필수 엔진 계약 제거를 해법으로 삼지 않는다.

### F5 — 구조 단서의 수명이 현재 입력 본문의 가용성에 묶임: P2 / Ambiguous / SourceVerified — 현재 구조 확인, 실패 인과 미확정

`evidence-manager.ts:124–145`는 현재 history에 실제 본문이 있는 trusted read에서만 architectureSources를 만들고, `architecture-observation.ts:4`는 이 본문으로 매번 lexical facets를 다시 만든다. 압축 직전 round6에서 12개였던 facet 수가 projection 뒤 round7에서 2개로 줄고, requested pattern도 definition/asset에서 component/event로 바뀌었다.

본문이 없을 때 지금 본문을 읽었다고 주장하지 않는 보호는 맞다. 다만 **본문 가용성**, **이미 관찰한 단서**, **소스 버전 유효성**, **모델이 채택한 판단**은 서로 다른 수명이다. 현재 optional v2 note가 없으면 이전 구조 단서를 유지할 독립적인 경량 projection이 없다. 이 사실만으로 모델의 최종 판단 실패를 전부 설명할 수는 없으므로 이 항목의 인과 판단은 Ambiguous로 남긴다. 단서 보존 보완에는 먼저 압축 전후 production 재현을 추가한다.

### F6 — 실제 모델의 거부·복사·반복 행동을 통합 테스트가 다루지 않음: 검증 공백

`prediction-loop.test.cjs:101`의 7개 architecture replay는 정상 footer·변경·취소·Off·note disabled·malformed·v1 경로를 검사한다. 중요한 소유권 보호지만 fake model이 의도대로 답하는 전제가 강하다. 기존 `:6815`의 transformed-reference 회귀는 Derived 형식을 사용하므로 새 architecture 프로토콜을 보호하지 못했다. 현재 전체 테스트 통과 기록은 이번 실패를 반박하지 못한다.

추가 판단 제한: 마지막 정상 도구 결과는 Actor.cpp의 TakeDamage 위치 검색이다. 모델은 본문을 읽겠다고 했지만 read_file을 실행하지 않았다. 실제 본문 검증이 필요한 엔진 의미나 게임 결함을 이 실행에서 확정했다고 취급하지 않는다. RAG 결과/위치 검색을 엔진 구현 검증으로 승격하지 않는다.

## 3. 책임과 연결 구조

새 중앙 관리자나 별도 에이전트 루프는 만들지 않는다. 기존 소유자의 데이터 계약을 강화한다.

| 소유자 | 맡을 일 | 맡지 않을 일 |
|---|---|---|
| internal-data-contract | host 내부 형식의 종류·버전·경계·식별과 공통 분류 | 구조 판단, 예산 승인, 도구 실행 |
| architecture-input / reference-rendering | 공유 계약으로 typed reference part 생성 | 출력 숨김 규칙 별도 정의, 저장·복구 결정 |
| modelNotes / continuity-model-notes | v1/v2 note 문법·파싱·수락 규칙 | reference 본문 속 문장을 명령으로 승인 |
| design-guidance / architecture-policy | 필수 dependency closure와 선택 후보·탈락 사유 | 별도 token allowance, 자동 RAG 호출 |
| ContextManager / BudgetBroker | 후보 전체를 실제 입력으로 측정하고 최종 입장 결정 | 패턴 선택, 모델 판단 승인 |
| PredictionStreamRenderer | 한 분류 결과로 GUI·history·capture용 정제 출력 제공 | 완료·재시도 정책 결정 |
| GenerationRepetitionDetector | 반복/무진행 신호 제공 | 직접 새 라운드 시작, 도구 취소 권한 별도 소유 |
| DeliveryController / ExecutionState | 정제된 본문만 보고서로 인정, 복구/종료·시도 상한 | raw 문자열에서 내부 프로토콜 재판별 |
| ReadLedger / EvidenceManager | 직접 반환 관찰의 버전·범위·가용성 및 경량 파생 단서 | 구조 대안의 선택/승인, 숨은 파일 새로 읽기 |
| ArchitectureState | 실행 범위의 모델 판단·의존성·무효화 | 관찰 원본 SSOT, 파일 조회, 저장소 쓰기 |
| NotePersistence / WorkingContext | 기존 단일 저장 경로와 정제된 창의 저장·복원·seal | 임시 참조를 권한으로 재발급 |

연결: trusted tool result → ReadLedger 관찰 → EvidenceManager projection → policy/candidate 구성 → ContextManager 실제 입장 → 해당 modelInputId의 reference 발급 → 모델 생성 → 공통 output 분류 → 정제 GUI/history와 DeliveryController → 기존 종료/저장 경로.

SOLID 관점에서 renderer의 문자열 포맷을 소비자가 각자 추측하는 의존성을 없앤다. 확장은 계약 종류를 등록하는 것으로 표현하고, 모든 소비자를 임의 문자열 검색으로 개별 패치하지 않는다. 그렇다고 일반 목적 plugin framework를 새로 만들지는 않는다. 종류별 작은 판별기와 동일한 결과 형식을 현재 공통 모듈 안에 둔다.

## 4. 구현 순서와 작업 단위

### S0. 실패 재현을 먼저 고정

1. 합성 소스와 실제 renderer 출력으로 exact/변형/중간 절단/반복/mixed-output fixture를 만든다. 개인정보와 게임 코드를 넣지 않는다.
2. stream on/off 두 경로와 실제 productionHandler를 사용한다. known Derived 대조군은 계속 통과해야 한다.
3. 현재 코드는 architecture echo 차단, report continuation 거부, 복원 정제에서 실패해야 한다. 카드 전달 fixture는 양보 가능한 optional 절이 있고 카드 1개가 실제 한도에 들어가는 6절 경쟁으로 한정한다. 모든 실측 라운드에서 무조건 카드가 들어가야 한다는 기대값을 만들지 않는다. 단순 현재 구현과 동일한 기대값 테스트로 대체하지 않는다.
4. 저장된 로컬 `independent-replay.cjs`/`independent-replay-results.json`은 수치 대조용으로 사용한다. raw fragment와 표시 블록을 혼동하지 않는다.

### S1. 내부 reference 계약과 출력/복원 경계 통합 — F1, F2 우선

수정 중심: `internal-data-contract.js`, `architecture-input.ts`, `reference-rendering.ts`, `design-guidance.ts`, `round-input.ts`, `context-manager.ts`, `internal-data-output.ts`, `prediction-stream.ts`, `continuity-assistant-evidence.js`, `working-context.js`.

- 문자열 하나에 여러 프로토콜을 합쳐 identity가 사라지는 경계를 바꾼다. 내부 표현은 kind/version/text/host-origin을 가진 reference part 배열로 유지하고, 최종 모델 입력 직전에만 직렬화한다. 기존 string 인자는 한 호환 adapter에서 정규화하며 두 표현을 가변 SSOT로 유지하지 않는다.
- 실제 입장한 part 목록에서 출력 필터의 protocol capability를 만든다. kind+schema로 변형 출력도 분류하고, handle/hash 내용은 데이터로 취급한다. 임의 handle을 도구 권한·편집 권한으로 해석하지 않는다.
- 새 형식은 시작/끝 경계를 명시한다. 기존 설치본이 만든 architecture 형식은 legacy reader에서만 해석한다. serializer와 consumer가 공유하는 계약 fixture를 둔다.
- v2 footer의 긴 사용 설명을 architecture 데이터 본문에 반복 삽입하지 않는다. 문법 설명은 기존 modelNotes 소유자로 옮기고, 데이터에는 schema capability와 이번 입력의 발급 참조만 둔다. 적용성/입장 조건이 같을 때 system instruction이 매번 바뀌지 않게 한다. note disabled/Off에서는 요청하지 않는다.
- GUI, history, delivery capture, persistable, retainVisibleResponse가 공통 판별기를 사용한다. parser state는 generation별로 생성하고 close/abort 후 폐기한다. 과거 정리는 연속 assistant text 조각 사이에서만 상태를 이어가며 user/tool 레코드를 넘지 않는다.
- quoted/fenced/indented 예시와 사용자 입력의 동일 문구는 보존한다. 제목 한 줄만으로 임의 사용자 텍스트를 삭제하지 않는다. legacy 정리는 검증된 host prefix/window라는 복원 문맥과 schema 근거를 함께 받되, 검증된 assistant transcript라는 사실만으로 host-origin을 부여하지 않는다. 사용자가 요청한 프로토콜 설명/예시와 실제 host 주입에 연계된 변형 echo를 구별한다. 출처나 형식이 불명확한 자료는 리터럴로 남긴다.
- 중간 절단 처리와 최대 buffer 한도를 명시한다. 인식된 내부 프레임이 중단됐다고 그 뒤의 정상 보고 문단까지 삼키지 않는 fixture를 둔다.

수락: internal-only 출력은 GUI 보고 본문·working history·durable response에 0자. mixed 출력은 정상 보고 문단만 동일하게 남는다. 기존 Derived/ledger/budget/reference/footer 계약과 사용자 인용은 유지된다.

### S2. 보고서·복구 계약과 반복 중단 연결 — F2, F3

수정 중심: `prediction-stream.ts`, `execution-contracts.ts`, `delivery-controller.ts`, `prediction-loop.ts`, 관련 output/recovery 테스트.

- 기존 captured.presentation을 확장해 정제 본문과 content disposition을 함께 전달한다. 최소 구별은 report prose / internal-only / mixed / unresolved tool intent / empty다. 이 값은 renderer가 생성하고 DeliveryController가 raw text로 재추론하지 않는다.
- `classifyOutputLimitStage`, `canRecoverOutputLimit`, `retainReportPrefix`, `observe`가 동일 presentation을 사용하도록 호출부를 교체한다. raw 문자열을 넘기는 우회 경로는 adapter 하나로 제한하고 테스트에서 점검한다.
- internal-only는 `continue_report` 자격이 없다. 유효한 이전 보고 prefix가 없는 실행에서는 reportPresent=false, reportDelivered=false, reportCompleted=false, taskCompleted=false와 구체적 종료 사유를 기록한다. 정상 보고서 뒤의 보완이 internal-only라면 정제된 empty supplement로 취급하고 기존 보고서의 완료·전달 상태를 유지한다. 출력 실패를 새 성공 보고로 바꾸거나 같은 내부 데이터를 새 모델 입력에 append하지 않는다.
- mixed는 정제된 실제 보고 본문이 있을 때만 기존 1회 이어쓰기 자격을 평가한다. 중단된 도구 인수·사용자 취소·시간 초과에는 기존 우선순위를 유지한다. 한도에 걸렸다는 이유로 무조건 새 최종 작성 라운드를 추가하지 않는다.
- 기존 detector에 인식된 내부 프레임의 반복/무진행 신호를 연결한다. 휘발성 modelInputId가 바뀌어도 동일 host protocol만 반복하고 정상 본문/도구 진행이 없으면 기존 repeat count에서 중단할 수 있어야 한다. 일반 텍스트·표·코드를 폭넓게 유사도 판정하지 않는다.
- detector는 신호만 반환하고 기존 loop/ExecutionState가 stop·종료를 맡는다. 별도 retry budget·timer·AbortController 소유자를 만들지 않는다. 반복 중단 뒤 미완성 note/architecture patch는 commit하지 않는다. 이미 반환된 tool evidence는 보존한다.
- 설정 계약을 명시한다. observeOnly에서는 새 주입·중단을 하지 않는다. generationRepetitionAction=off에서는 반복 감지/조기 중단을 하지 않고, warn은 경고만, pause는 기존 중단을 유지한다. recover에서는 기존 reasoning 복구를 유지하되 **host 내부 프레임만 반복하는 visible 출력은 중단하고 같은 텍스트로 복구하지 않는다**. 이는 의도된 정책 변경이다. off/warn에서도 정상 활성 하네스의 출력 정제와 internal-only 보고 불인정은 유지하므로 토큰 한도 종료 뒤 잘못된 이어쓰기는 없다. 네 설정과 observeOnly를 각각 테스트한다.
- reportDelivered의 의미는 기존 정상 보고서 보존 계약을 유지한다. 유효 보고서 뒤 보완이 비어도 원 보고서는 유지되며, 내부 텍스트만 있는 prefix는 그 보호 대상이 아니다.

수락: 감지 활성 설정에서 2,392자 합성 프레임 3회와 변형 프레임 반복을 감지하고 pause/recover에서 한도 소진 전에 중단. internal-only에서 recovery 0회. 정상 잘린 보고서는 최대 1회. 두 번째 복구는 없음. 기존 잘린 보고의 완료 여부도 뒤의 empty supplement로 true가 되지 않음. cancel/timeout/late callback 뒤 데이터·판단이 다시 commit되지 않음.

### S3. 패턴과 기존 지침을 같은 후보 정책에서 배분 — F4

수정 중심: `design-guidance.ts`, `architecture-policy.ts`, `round-input.ts`, `generated-design-guidance`의 생성 경로 및 후보 테스트.

- `6-candidate.ids.length`의 사후 잔여 배정과 하드코딩 6을 제거한다. 기존 REFERENCE_LIMITS와 dependency closure를 사용하는 하나의 후보 구성 단계에서 기존 절과 패턴의 역할을 배정한다.
- 보호 순서: 필수 권한/엔진 사실/검증 계약 → 사용자 목적의 필수 절 → 관련 구조 카드 → 선택 보완 절. 기존 pack 구성의 contract/verification/task-primary 역할과 requires로 보호 집합을 계산한다. supplement가 minimum에 포함됐다는 이유만으로 영구 필수로 승격하지 않고, 명시적으로 optional인 보완 절만 양보한다. 역할을 모르는 custom section은 보호 쪽으로 남긴다. mandatory closure는 카드 때문에 깨지지 않는다. 같은 우선순위는 안정적 ID 순이다.
- 관련 카드가 있고 mandatory closure+카드 1개가 절/토큰 한도에 들어가면, 선택 절 하나를 양보하는 후보를 **기존 6절+카드 0 후보보다 앞에** 제시한다. 최대 카드 2개는 유지한다. mandatory가 한도를 전부 쓰면 카드를 생략하고 원인을 기록한다.
- 우선순위가 다른 후보를 순서대로 측정하는 기존 ContextManager만 재사용한다. 최종 실제 token 측정, 최소 읽기 예약, 출력 reserve, 공용 allowance와 기존 후보 시도 상한은 유지한다. 후보 수를 잘라내면서 유일한 카드 후보가 사라지지 않는 테스트를 둔다.
- telemetry는 requested/admitted/omitted pattern IDs와 `section_limit`, `mandatory_contracts`, `token_budget`, `view_omitted`, `not_applicable` 등 실제 원인을 구별한다. view가 있다고 카드가 전달됐다고 표시하지 않는다.
- sharedReferenceAddedTokens는 공용 증가량임을 유지한다. architecture 실제 token 값이 없으면 unknown을 유지하고, 원인 분석용 typed part 문자 수·선택 IDs를 기록한다. 계측만을 위한 추가 모델 호출이나 반복 tokenizer 호출은 넣지 않는다.

수락: 기존 지침 6개 fixture에서 optional 절이 있으면 필수 계약을 보존하면서 카드가 1개 이상 실제 입력에 존재한다. 전부 mandatory 또는 실제 token 부족이면 0개와 정확한 생략 원인이 기록된다. Off/Unity/엔진 미확인/복구 단계는 기존 비활성 계약을 유지한다.

### S4. 구조 관찰·가용성·판단의 수명 분리 — F5, 재현 후 적용

수정 중심: `read-ledger.js`, `evidence-manager.ts`, `architecture-observation.ts`, `architecture-round.ts`, `architecture-state.ts`, 저장/복원 projection 테스트.

- 먼저 같은 직접 반환 관찰에서 압축 전후 `sourceKey/version/facets/availability` 변화를 production 경로로 재현한다. 본문이 빠진 것 외에 버전 변경이나 다른 무효화가 있었는지 분리한다.
- 단서 보존이 필요한 부분만 기존 source observation record에 bounded 파생 metadata로 붙인다. `sourceKey`, 직접 관찰 version, span, extractor version, positive facets를 유지하며 별도의 source-version map/독립 저장소를 만들지 않는다.
- 직접 본문 수신 시 기존 EvidenceManager가 단서를 계산하고 ReadLedger 관찰과 같은 버전으로 결합한다. 부분 본문에서 나온 양성 단서는 전체 파일 분석 완료나 teardown 부재의 증명이 아니다.
- 압축은 availability를 metadata_only/unavailable로 바꾼다. 같은 관찰 버전의 단서를 자동 stale로 만들지는 않는다. 반대로 직접 쓰기/결과 불명 변경은 기존 sourceObservations 무효화와 함께 단서를 stale/unknown으로 바꾼다. 보관본·검색 hit는 이를 최신으로 되살릴 수 없다.
- 복원 단서는 historical이며 조회 후보 선택에만 사용한다. 새로운 실제 코드 결론·편집 권한·현재 본문 근거 handle로 승격하지 않는다. 모델 note는 여전히 선택적이고, note가 없었다는 이유로 구조 검토 완료/문제없음을 생성하지 않는다.
- 프로젝트/목적 불일치 또는 검증되지 않은 lineage 변경에서는 기존 scope 규칙에 따라 폐기한다. 검증된 parentLineage 복원은 historical로 허용하되 current 권한은 부여하지 않는다. catalog 변경은 패턴 적용성을, extractor 변경은 파생 단서의 재사용 가능성을 별도로 무효화한다. 원본 관찰의 권위와 파생 후보의 권위를 혼합하지 않는다.
- map 상한·직렬화 상한을 기존 저장 예산 안에 둔다. 축출 시 explicit unavailable로 남기거나 제거하며 재조회 루프를 시작하지 않는다. 파생 관찰은 sourceObservations → ReadLedger.toJSON → WorkingContext 한 경로로만 저장하고 NotePersistence에 복제하지 않는다. 모델 판단 note는 기존 NotePersistence 경로를 유지한다.

수락: note가 없는 같은 버전 source의 압축 후에도 관련 후보 단서는 유지되며 availability는 현재 본문과 구별된다. 변경/unknown write/프로젝트 전환/복원/축출 시 권한이 부활하지 않는다. 소스가 없는 구조 상태를 만들어 넣지 않는다.

## 5. 종단 검증과 완료 조건

| 테스트 묶음 | 반드시 확인할 조합 |
|---|---|
| 프로토콜 분류 | exact, ID 변경, key 순서, malformed/truncated, 모든 헤더 분할 위치, LF/CRLF, 연속 여러 프레임, 정상 문단 혼합 |
| 사용자 리터럴 | fenced/blockquote/indented 예시, 헤더만 있는 일반 설명, user/tool 자료, 알려지지 않은 JSON, 사용자가 요청해 assistant가 출력한 프로토콜 설명/예시 |
| production 출력 | stream on/off × internal-only/mixed × eos/token-limit/cancel; GUI/history/capture 결과 일치 |
| 복구 | internal-only 무복구, 정상 보고서 1회 이어쓰기, 정상 보고서 뒤 빈 supplement 보존, tool intent는 보고서 불인정 |
| 반복·수명 | 2,392자 및 변형 프레임, off/warn/pause/recover/observeOnly 설정, 반복 중 취소, 종료 후 callback, 반환 근거 보존/미완성 판단 미commit |
| 저장·복원 | 기존 architecture echo 중 provenance/schema로 판별 가능한 사례 정리, 불명확 예시 보존, 여러 text 조각의 변형 echo, 반복 정리의 멱등성, tool-call/result 위치 보존, v1/v2 note 호환 |
| 입장 | 기존 6절 경쟁, mandatory closure 초과, 저예산 fallback, sticky instruction, actual input과 issued refs 일치 |
| 단서 수명 | 압축/복원/동일 버전 재사용/새 버전/unknown write/archive 재전달/프로젝트 전환/상한 축출 |

1. 해당 합성 회귀가 먼저 실패하는지 확인한 뒤 S1→S2→S3를 적용한다. S4는 별도 재현과 독립 변경으로 진행한다.
2. plugin build/전체 관련 tests, guidance 및 runtime identity 생성 검사, Python/RAG와 Agent의 영향 경로 테스트를 실행한다. 관계없는 전체 테스트를 반복 실행해 불확실성을 덮지 않는다.
3. 실제 모델은 같은 설정·Auto 상태의 동일 요청과, 별도 구조 검토 문구 없는 일반 Unreal 수정 요청을 실행하여 비교한다. 원본과 변경본 각각 3회 측정한다. 편차를 숨기지 않고 중앙값·범위·실패 횟수를 남긴다. 모델 출력은 확률적이므로 단발 성공을 개선 증명으로 삼지 않는다.
4. 기계적 합격: 내부 프로토콜 표시/저장 0, protocol-only 이어쓰기 0, 정상 보고 완료 여부의 정확한 상태, 필수 계약 보존, eligible 카드 실제 전달, 불필요한 재조회·새 숨은 RAG 호출 0. baseline의 전체 예산을 늘려 합격시키지 않는다.
5. 품질 합격: 현 소유자 유지/Component/별도 Actor/scoped subsystem/정의 자산과 런타임 상태 중 관련 대안을 실제 근거와 비용으로 비교한다. 새 타입 수가 많아졌다는 이유로 합격시키지 않는다. 근거 없는 엔진 P1 확정, 위치 검색을 구현 검증으로 표시, 구조 검토 생략을 문제없음으로 표시한 경우는 실패다.
6. 성능은 동일 작업 완료 정도와 함께 비교한다. 실패를 일찍 끝내 시간만 줄인 것은 답변 품질 개선으로 보고하지 않는다. 이번 56.7초 recovery prefill은 잘못된 복구 제거의 관찰 지표이며, 모든 prefill 지연을 architecture 비용으로 귀속하지 않는다.
7. 구현 후 설치 단계에서는 현재 연결/프로젝트/인덱스 설정을 보존하며 패키지 identity와 실제 다음 실행 loaded identity를 확인한다. 전체 RAG 인덱스 재생성은 이 결함 수정의 필수 조건이 아니다. 이번 계획 작성 단계에서는 설치하지 않는다.

부분 실패 복구: S1/S2는 출력·저장 계약과 함께 하나의 일관된 단위로 검토한다. S3/S4는 분리해 되돌릴 수 있게 한다. 새 저장 metadata는 optional/versioned로 하여 구버전에서 권위 없이 무시할 수 있게 하고, 기존 window를 대량 삭제하지 않는다. 배포 실패 시 이전 패키지와 설정 백업을 사용한다. 변경된 오염 창은 로딩 시 비파괴 정제하며 raw 대화 파일을 자동 편집하지 않는다.

## 6. 구현 전 고정할 불변식

- 내부 참고 자료, 사용자에게 보인 보고서, 직접 반환 근거, 모델 판단, 실행/편집 권한은 서로 대체할 수 없다.
- 발급한 modelInputId/ref와 실제로 입장한 입력이 같아야 한다. 모델이 echo에 쓴 임의 handle은 발급 증명이 아니다.
- 저장 여부·화면 표시 여부·보고서 완료·목표 달성은 서로 다른 상태다.
- 미완성/취소/시간 초과 판단은 commit하지 않는다. 이미 반환된 근거는 별도 수명으로 보존한다.
- 출력 정제 규칙의 정본은 하나이며 GUI·recovery·history·durable memory가 동일한 분류를 소비한다.
- 필수 엔진 계약과 읽기 예약을 훼손하지 않고 optional 지침끼리 경쟁한다. 선택/입장/실제 표시를 별도 계측한다.
- 구조 판단 저장소·소스 관찰 저장소·note 저장 소유자·복구 상태 기계·도구 실행 루프를 중복 생성하지 않는다.
- 이번 원인 분석과 기계적 재현은 모델의 일반 지식 수준이나 Unreal 설계 능력 향상을 증명하지 않는다. 품질 평가는 위 실제 실행으로 별도 수행한다.

근거 패킷 검사는 형식과 필수 항목을 검사할 뿐 원인 판단의 진실성을 보장하지 않는다. 이번 소스 추적·원본 로그·독립 재현의 확인 수준과 남은 미측정 사항은 각 항목에 명시했다.
