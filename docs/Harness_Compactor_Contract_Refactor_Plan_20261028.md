# 하네스·컴팩터·아키텍처 기능 전체 계약 감사와 리팩토링 계획

- 작성일: 2026-10-28, Asia/Seoul.
- 요청 범위: 압축 후 컨텍스트 재증가, 압축 안정성, 아키텍처 추가 기능과 기존 하네스·컴팩터·도구 사이의 SSOT/책임/경계/계약/수명 조사 및 구현 가능한 리팩토링 계획.
- 기준: codex/harness-output-contract-fixes, HEAD 2b13b38c5644b9072e04c488e642205b46cbc804 + 현재 미커밋 아키텍처 구현. 제품 코드·설치본·실제 인덱스·사용자 게임 프로젝트는 이번 감사에서 변경하지 않았다.
- 실측: conversation 1793194960145의 마지막 검토 실행, compactor 0.4.86 / revision 133, loaded/installed build 1aac031f… 일치.
- 이 문서가 전체 리팩토링의 우선 계획이다. [앞선 출력 중심 계획](Harness_Run_1793194960145_Audit_Fix_Plan_20261028.md)은 출력·복구 재현의 상세 참고로 유지하되, 작업 순서와 입력/저장/출처 경계는 이 문서를 따른다.
- 근거 패킷: [전체 계약 감사 기록](evidence/harness-full-contract-audit-plan-20261028.json).
- 원본과 재현 결과는 사용자 로컬 진단 폴더 <local-diagnostics>/harness-full-contract-audit-20261028에 둔다. 저장소에 원본 대화·게임 소스·연결 설정을 복사하지 않는다.

## 1. 결론과 조사 범위

이전 계획은 출력 형식 누락을 중심으로 잡아 범위가 좁았다. 추가 조사에서 입력 구성의 교체 규칙, 압축 우선순위, 출처의 프로젝트/버전 순서, note의 저장/실패 의미, RAG 지식의 본문/버전 연결까지 결함을 확인했다.

현재 최종 입력의 정확한 측정과 hard limit 보호는 유지된다. 반면 **불필요하거나 서로 모순된 자료를 포함한 입력을 정확하게 측정한 뒤 실행하는 경우**가 있다. 따라서 tokenizer 호출을 늘리거나 압축 비율만 높이는 수정은 핵심을 해결하지 못한다.

| 조사 경계 | 확인한 경로 | 판단 수준 |
|---|---|---|
| 입력·압축 | prepareRoundInput → compose/assembler → low water → optional admission → final measure → dispatch | 실측 + 최소 재현 |
| 참고 자료 수명 | shownDataBlocks → Derived delta → architecture snapshot → consolidation → 최종 refs | 소스 + 최소 재현 |
| 출력·복구·보존 | 내부 형식 → stream/capture → continuation → historical sanitizer/window | 이전 실측 + 재현 유지 |
| 출처 권위 | confirmed tool pairs → causal exchange → ReadLedger → architectureSources → 판단 commit | 소스 + 배치/프로젝트 재현 |
| 구조 상태·note | begin/reconcile/invalidate/finish → merge → mirror/persist → read/restore | 소스 + 경계 재현 |
| RAG | canonical export → raw row → build → query/page → patternRef | 합성 인덱스 재현 |
| 목적·선택 | guidanceObjective → 일반 guidance/architecture 선택 → 실제 입장 | 소스 + 불완전 목적 재현 |
| 실제 모델 품질 | 이번 실행의 읽기/출력/최종 상태 | 정상 최종 보고 미완성; 개선율은 아직 평가하지 않음 |

이번 감사에서 발견한 모든 추가 결함이 이번 실행에서 실제 발생했다는 뜻은 아니다. 아래에서 RuntimeVerified와 TestVerified를 구분한다. 외부 SDK의 캐시 구현, 새로운 실제 모델 실행, 다음 사용자 턴 복원은 수행하지 않았다.

## 2. 압축 후 증가를 정확히 나누기

### 2.1 실제 측정

roundIndex 7에서 low-water 단계는 47,508토큰으로 목표 47,719 이하에 도달했다. 그 뒤 현재 정책이 선택 참고를 high-water까지 허용하면서 5,358토큰이 추가되어 실제 입력은 52,866토큰이 되었다. high-water는 63,626, hard input ceiling은 73,984였다.

| roundIndex | 압축 후 기본 history | metadata 단계 증가분 | 실제 최종 입력 | 현재 runtime 자료 | 이전 잔여 runtime 자료 |
|---:|---:|---:|---:|---:|---:|
| 7 | 32,237 tokens | 13,127 tokens | 52,866 tokens | 23,181 chars | 3,927 chars |
| 8 | 34,297 tokens | 13,196 tokens | 54,995 tokens | 23,318 chars | 4,036 chars |
| 9 | 35,051 tokens | 14,832 tokens | 57,385 tokens | 23,385 chars | 8,847 chars |

7→9의 증가 4,519토큰 중 기본 history 단계는 2,814, metadata 단계는 1,705 증가했다. 기본 history에는 새 도구 결과와 모델 텍스트가 포함된다. 현재 runtime 자료는 204자만 늘었지만 이전 잔여 자료는 4,920자 늘었다.

이전 잔여 자료에는 ledger·검토 상태·참고 자료 등 여러 종류가 섞여 있다. 4,920자를 모두 architecture 중복이라고 귀속하지 않는다. runtimeDataTokens도 지침 등 입력 조립 증가분을 포함하므로 순수 architecture 비용이 아니다. 최종 입력의 종류별 manifest가 없어 실제 비중은 아직 구분할 수 없다.

### 2.2 정상 동작과 결함의 구별

- 47,508→52,866은 현재 high-water optional admission 정책의 결과다. 재측정을 누락한 것이 아니다.
- 최종 입력 토큰 = SDK prompt 토큰, 도구 schema surface 일치, 최종 fingerprint 일치가 **11/11 라운드**에서 확인됐다.
- assembler 측정 시간 합은 약 3.9초, 모델 prompt 처리 합은 약 265.5초다. 측정 호출 수 자체를 수분대 지연의 원인으로 단정하지 않는다.
- round8/9의 prefix는 보존되고 prefill은 약 3.7/3.9초였다. 매번 모든 과거 자료를 갈아엎는 방식은 이 이점을 잃을 수 있다.
- 그러나 아래 C1/C2처럼 필요 없는 optional 자료가 압축 원인이 되거나 현재 snapshot이 탈락/중복되는 경로는 재현된 결함이다.
- 따라서 현재 상태를 “컨텍스트 크기가 무제한으로 새거나 hard limit을 넘겼다”라고 설명하지 않는다. **총량 보호는 작동하지만 내용 선택과 수명 규칙의 안정성이 부족하다**는 판단이다.

## 3. 확인한 결함과 반대 근거

이 절의 plugin 소스 경로는 lmstudio-context-compactor-plugin/src/ 기준이다.

### C1. 과거 optional 자료가 압축 우선순위를 역전시킴 — P1 / Bug / TestVerified

경로: round-input.ts:111–123의 selection pending → context-manager.ts:323의 과거 shown blocks 복원 → :329–331에서 크기 비교에서만 Derived 제외 → 실제 입력에는 계속 포함 → round-input의 low-water → context-manager.ts:748–788에서 source checkpoint 선택.

명시적 계약은 “optional guidance가 source eviction을 유발하지 않는다”인데, 과거 optional이 먼저 올라온 baseline으로 압력을 판단한다. 같은 source history를 비교한 최소 재현:

| 조건 | preflight | 압축 | 직접 반환 결과 |
|---|---:|---|---:|
| 과거 참고 17,206자 유지, 새 참고 미선택 | 4,777 proxy tokens | 실행 | 8→7 |
| 과거 참고만 제외 | 469 proxy tokens | 없음 | 8→8 |

이 값은 ceil(chars/4) 합성 tokenizer와 작은 fixed threshold를 이용한 **순서/소유권 재현**이다. 실제 round7의 원인 비중을 나타내지는 않는다. addOptionalReferences 자체는 source를 압축하지 않으며 그 이전의 baseline 구성 순서가 문제다. 수정은 과거 optional 제거·재평가를 근거 압축보다 먼저 하는 것이다.

### C2. 현재 snapshot과 누적 observation의 갱신 규칙 혼합 — P1 / Bug / TestVerified

round-input.ts:434–440은 Derived와 architecture를 한 문자열로 결합한다. context-manager.ts:392–424는 첫 두 줄의 Derived items만 보고 중복 제거한다.

- Derived items가 같으면 새 architecture view도 함께 버려 이전 modelInputId만 남는다.
- Derived item이 새로 있으면 이전 architecture와 새 architecture가 함께 남는다.
- consolidateShown=true여도 :332의 문자 수 조건 때문에 이전 1,852자보다 현재 1,905자가 크면 교체되지 않는다. 결과는 총 3,704자, architecture frame 2개, replacesShown=false다.

반대 근거: round-input.ts:547,560은 최종 새 view가 없으면 refs 발급을 취소한다. 사라진 새 view의 refs가 정상 승인됐다는 결함까지 확인한 것은 아니다. 그러나 오래된 view만 남거나 두 현재 상태가 공존하는 입력 자체는 잘못이다.

문자 수는 압력 정보이며 snapshot의 유효성을 결정할 권한이 없다. 교체·무효화는 semantic revision/phase/scope가 결정하고, cache prefix 유지보다 우선해야 한다.

### C3. optional 예산의 기준점이 오염되고, 압축 성공 지표가 최종 입력과 다름 — P2 / 계약 보완 / SourceVerified

context-manager.ts:689의 addedTokens는 candidate−base 순증가량이다. base에 과거 optional이 남으면 resident optional 전체 비용이 아니다. 이번 round6의 addedTokens=0은 참고 비용 0을 의미하지 않는다.

또한 lowWater.success는 참고 재입장 전 측정이고, workingContext.targetMet은 설정 target 46,000과 비교한다. 실제 auto low-water는 47,719다. 예를 들어 round10 최종 47,662는 effective low 이하인데 설정 target 기준 targetMet=false다. 서로 다른 기준의 지표를 “압축 성공/실패” 하나로 읽으면 운영 판단이 왜곡된다.

현재 high-water 입장 자체는 ByDesign이다. 리팩토링에서는 정상 성장 목표와 압축 직후 최종 목표, 설정값과 effective값을 명시적으로 분리한다.

### O1. 내부 프로토콜의 출력·복구·복원 분류 누락 — P1 / Bug / RuntimeVerified

새 architecture 형식은 internal-data-contract/internal-data-output에 등록되지 않았다. 11,960자/21,230자의 내부 형식 반복이 보고서로 표시되고, prediction-loop.ts:1545–1554에서 이어쓰기 입력으로 재주입됐다. exact/변형/혼합 형식과 legacy sanitizer 통과도 재현했다. 2,392자 반복은 최대 2,000자 detector에서 빠지고 recover의 visible 반복 정책은 경고만 한다.

taskCompleted=false와 복구 1회 제한은 정상이다. 다음 실제 사용자 턴의 오염은 미실측이다. 상세 재현은 이전 계획 F1–F3을 유지한다. C2와 동일한 typed part 계약을 사용해 입력과 출력 양쪽을 고쳐야 한다.

### A1. 동일 배치의 다른 소스 버전을 배열 순서로 최신화 — P2 / Bug / TestVerified

EvidenceManager.recordReturnedSources(:75–114)는 observationRecords 결과를 순회하고 read-ledger.js:153–174는 마지막 직접 관찰을 최신으로 저장한다. 동일 tool batch에서 같은 파일의 다른 hash A/B가 반환될 때 기존 exchangeIndex는 uniqueLatest=null로 순서를 확정하지 않지만, 새 sourceObservations는 배열 마지막 B를 same_observed_version으로 발급한다. 그 refs를 사용한 구조 판단 commit도 accepted다.

핵심은 map을 하나로 만들었는지가 아니라 **그 정본이 인과 순서를 보존하는가**다. 서로 다른 버전이 동시에 관찰되면 unknown/conflict여야 한다. 순서가 확정된 이후 직접 읽기로만 해소하며 자동 재조회는 시작하지 않는다.

### A2. 자동 프로젝트 인식과 판단 binding의 scope 불일치 — P2 / Bug / TestVerified

prediction-loop.ts:425–426의 ArchitectureBinding은 원래 ToolScope의 projectIdentity를 받는다. available_tools/빈 identity 상태에서 effective reference scope는 반환된 프로젝트 B로 갱신될 수 있으나 binding은 빈 값이다. evidence-manager.ts:124–145의 architectureSources도 현재 프로젝트로 분할하지 않는다.

A 읽기 후 B 읽기 경로에서 note reconcile은 이전 A note를 거절하는 정상 보호가 있다. 그러나 새 B state를 만들고도 A/B refs가 모두 same_observed_version으로 발급되며, B가 현재 scope인 상태에서 A ref로 새 판단이 commit된다. explicit 프로젝트 불일치 방어는 작동하는 대조군이다.

따라서 reset만 추가해서 끝내면 안 된다. effective project identity, source projection, state binding, issued ref validation이 같은 정규화 scope를 소비해야 한다.

### N1. host invalidation이 비멱등적이며 merge 실패가 명시 clear와 충돌 — P2 / Bug / TestVerified

architecture-state.ts:22–26의 invalidateUnknown은 이미 unknown인 판단에도 revision을 다시 올린다. 유효한 6,000자 note에서 revision9→10만으로 6,001자가 되어 mergeArchitecture가 null을 반환하는 경계가 재현됐다.

architecture-note-contract.js:79–90의 null 실패가 prediction-loop.ts:208–211의 replaceActiveNote에 들어가면 일반 note의 명시 clear처럼 다시 합성된다. 합성 재현에서 일반 판단 1개→0개, 구조 판단만 남았다. 예외 경계의 재현이며 이번 실측에서 그 note가 존재했다는 주장은 아니다.

필요한 계약은 명시 clear / unchanged / accepted / rejected_budget / failed를 구분하는 결과형과 host invalidation의 멱등성이다. 상한을 늘리는 수정만으로 해결하지 않는다.

### N2. v2 write와 read의 물리 저장 한도 불일치 — P1 / Bug / TestVerified

architecture-note-contract.js:4, continuity-model-notes.js:354–358은 v2 6,000자를 허용한다. write(:498–505)는 이를 저장하지만 read(:491)는 파일 크기 4,096바이트 초과 시 거절한다.

- 유효한 ASCII v2 note 6,000자, 저장 envelope 6,082바이트: write=true, read=null.
- 유효한 한국어 note 2,504자, 저장 envelope 5,998바이트: write=true, read=null.

window fallback이 전체 턴 손실을 가릴 수 있으므로 history note store의 round-trip 실패로 한정한다. 문자 상한과 UTF-8 파일 바이트 상한, envelope 크기와 schema version의 책임이 분리돼 있다.

### K1. RAG pattern canonical hash가 인덱스 본문과 분리됨 — P2 / Bug / TestVerified

scripts/architecture_pattern_catalog.py:62–108의 raw 현재성/PatternInventory는 metadata identity만 검증한다. rag_build_writer.py:54–68은 실제 doc.text를 받지만 inventory에는 metadata만 전달한다.

raw row 본문만 바꾼 합성 입력이 16개 완전 catalog로 게시되고 검색은 변경 본문과 원래 contentHash를 반환했다. 정상 canonical export 단계의 검증은 존재하며 실제 설치 인덱스 손상을 확인한 것은 아니다. canonical record의 hash와 chunk hash를 구별하면서 raw→writer 경계에서 실제 본문을 연결해 검증해야 한다.

### K2. query→chunk page에서 pattern identity/generation이 사라짐 — P2 / Bug / TestVerified

query 응답에는 patternId/contentHash/catalogHash/indexGenerationId가 있다. direct_rag_chunk_read.py:69–93은 metadata_json을 읽지 않고 그 네 필드를 반환하지 않는다. EvidenceManager.architecturePatterns(:179–190)도 matches[]만 소비한다.

합성 query→page에서 본문·chunkSha256은 있지만 네 identity 필드가 모두 사라졌다. cached_index_text와 consistent-generation SQL은 정상이다. 더 상세히 읽었는데 패턴 연결이 사라지는 계약 비대칭이며, 프로젝트 본문/편집 권한 승격 결함은 아니다.

### K3. 패턴 corpus 검색 상태와 일반 hit 혼동 — P2 / Bug / TestVerified

direct_rag_evidence.py:14–23은 요청에 architecture_pattern이 있고 matches가 비어 있지 않으면 matched로 표시한다. mixed doc_type의 일반 guideline hit 1개/패턴 hit 0개에서 matched가 재현됐다. corpus capability, 실제 pattern hit, transport로 반환된 identity 수를 구분해야 한다.

이번 실측은 api_lookup 검색 1회였다. K1–K3가 이번 컨텍스트 증가를 일으켰다는 근거는 없다.

### K4. 관찰된 RAG 패턴 변경이 기존 판단을 무효화하지 않음 — P2 / Bug / TestVerified

source A와 pattern hash B/generation g1에 의존한 판단 뒤 동일 패턴 hash C/generation g2를 반환시켰다. architecturePatterns에는 두 관찰이 있지만 architecture-round.ts:44의 invalidation 입력은 source changes/versions뿐이다. finish 후 기존 g1 의존이 유지되고 invalidation event는 0개였다.

bundled catalog hash 변경의 stale 전이는 정상이며 RAG ref의 unverified 표시도 유지된다. API 검증 권한이 생기는 문제가 아니라 이미 관찰한 지식 변경이 판단 수명에 연결되지 않는 문제다. reducer가 검색이나 refresh를 수행할 필요는 없다.

### P1. 목적 완전성 가드가 실제 architecture 호출에서 누락 — P2 / Bug / TestVerified

continuity-objectives.js:251–269는 잘린 목적에 text를 주지 않고 objective_truncated를 반환한다. 일반 guidance는 이를 소비하지만 architecture 호출은 text만 넘기고 selectArchitectureContext의 complete=false 가드를 연결하지 않는다.

4,206자 합성 요청에서 objective_truncated/text 없음인데 실제 prepareRoundInput은 component 패턴을 선택·입장했다. 같은 policy에 complete=false를 직접 주면 selectedPatternIds=[]다. 단위 테스트에 있는 가드와 production wiring이 다르다. 이번 실제 목적은 이 조건에 해당하지 않는다.

### P2. 배분·계측 및 관찰 가용성의 기존 보완 사항

앞선 계획 F4/F5를 유지한다. 카드는 optional이므로 0개 자체가 버그는 아니다. 카드별 탈락 이유 누락은 확정 결함이며, 기존 6절과의 공동 배분은 의도적인 정책 개선이다. 본문 압축 뒤 positive facets 감소는 확인했지만 그 보존이 최종 모델 품질에 미치는 인과는 아직 미확정이다.

## 4. 새로 세울 계약과 정본

“하나의 거대한 관리자”를 만들지 않는다. 각 사실의 정본과, 그 사실에서 파생되는 view를 구분한다.

| 정보 | 정본/변경 소유자 | 소비자 | 수명 |
|---|---|---|---|
| 실제 tool request/result와 배치 순서 | 기존 tool boundary + exchangeIndex | EvidenceManager/ledger | 실행·검증된 복원 |
| source project/version/order/무효화 | ReadLedger의 검증된 관찰 | 구조·보고·가용성 projection | version/scope별 |
| effective project/engine applicability | 기존 reference scope resolver | guidance/architecture/state/refs | scope epoch |
| 모델 구조 판단 | ArchitectureState | note/input view | 검증된 scope와 의존 버전 |
| canonical 패턴 본문·identity | authored Markdown/catalog→generated export | bundle/collector/index | catalog/content revision |
| RAG generation | 기존 index generation transaction | query/page/pattern observation | generation snapshot |
| note의 일반·구조 합성 및 저장 수락 | note contract + NotePersistence | active note/window/history store | note revision |
| 입력 part의 갱신/퇴거 | ContextManager의 실행 로컬 shown-data 상태 | 최종 assembler | part kind/scope/revision |
| 실제 입력 준비 승인 | ContextManager + BudgetBroker | prediction loop/tool batch | 단일 prepared input |
| 정제 출력 및 분류 | PredictionStreamRenderer + shared protocol parser | GUI/history/delivery/persistence | generation |
| phase/retry/cancel/terminal | ExecutionState/기존 round loop | 각 adapter | execution |

핵심 불변식:

1. optional resident 자료를 제거·재평가하기 전에 source evidence를 optional 때문에 축출하지 않는다.
2. 현재 snapshot은 같은 kind/scope에서 하나만 활성이다. append-safe observation은 고유 identity로 합치되 현재 권위로 자동 승격하지 않는다.
3. 입력 크기와 semantic 유효성은 다른 판단이다. 새 snapshot이 더 크더라도 교체는 이뤄져야 하며 이후 예산에서 축소/생략한다.
4. 모델에게 보인 stable 참조 label과 input별 발급 권한은 다르다. label 재사용이 이전 입력의 권한 재사용이 되지 않는다.
5. latest version은 배열 마지막이 아니라 검증된 인과 순서로만 결정한다.
6. 현재 project scope와 다른 source ref는 단일 프로젝트 판단에 발급하지 않는다. mixed/unresolved는 명시 상태다.
7. host 무효화는 반복 호출에 멱등적이고, 모델 patch 거절·예산 부족과 독립적으로 효력이 남는다.
8. 저장 실패는 clear가 아니다. write 성공으로 보고한 note는 같은 형식의 read에서 읽을 수 있어야 한다.
9. RAG pattern identity는 본문과 generation에 묶인다. chunk hash와 whole-record hash를 대체하지 않는다.
10. 모든 상태 변화는 준비/입장/dispatch/관찰/commit/저장 완료 중 어떤 단계인지 구별한다.
11. 최종 exact measurement·도구 surface·batch reservation의 일치는 현재 보호를 유지한다.
12. Off/observeOnly/사용자 인용/정상 보고 보존/검증된 parentLineage historical 복원은 훼손하지 않는다.

### 4.1 SOLID 관점의 구체적 정리

| 원칙 | 이번에 확인한 경계 문제 | 리팩토링 기준 |
|---|---|---|
| 단일 책임(SRP) | 문자열 중복 제거가 snapshot 수명까지 결정하고, note의 null 처리에 clear 정책이 섞임 | 입력 갱신은 typed part 계약, 예산은 BudgetBroker/assembler, 판단 전이는 ArchitectureState, 합성·저장은 note 경계가 담당 |
| 확장에 대한 개방(OCP) | architecture 형식을 추가하면서 output/history/recovery 분류를 따로 수정해야 했고 일부가 누락됨 | 공통 protocol registry에 producer/parser/허용 projection을 등록. 새 형식은 등록 및 경계 테스트를 요구하고 여러 consumer의 prefix 분기를 복제하지 않음 |
| 대체 가능성(LSP) | v2 writer가 허용한 note를 같은 store의 reader가 거절; query와 상세 page의 knowledge identity 보존 수준이 다름 | 지원 schema의 write/read round-trip, query/page의 공통 identity/provenance 조건을 계약 테스트로 고정. 누락을 조용히 정상 응답처럼 승격하지 않음 |
| 인터페이스 분리(ISP) | 전체 text/nullable note로 서로 다른 갱신·실패 의미를 운반 | read-only observation, 렌더링 part, 입력 준비 결과, note 갱신 결과를 필요한 필드로 분리. renderer에 저장·도구 실행 기능을 넘기지 않음 |
| 의존성 역전(DIP) | 상위 정책이 문자열 포맷과 도구 응답 모양에 기대 현재성·입장을 추론 | 기존 context-ports와 evidence adapter에서 정규화한 계약을 policy/state에 전달. 별도 DI 컨테이너나 범용 manager는 추가하지 않음 |

이 표는 클래스 수를 늘리기 위한 분해 기준이 아니다. 같은 사실을 두 소유자가 재판정하거나, 실패 결과가 다른 의미로 변환되는 연결 지점을 없애는 기준이다.

## 5. 최소 계약형과 연결 방식

실제 타입 이름은 저장소 명명에 맞추되 다음 의미는 유지한다. 새로운 범용 plugin framework로 확대하지 않는다.

### 5.1 입력 자료

각 reference producer는 text 하나 대신 종류·scope·revision·갱신 의미가 있는 part를 반환한다.

- identity: kind + scopeKey + semanticKey.
- revision: 실제 내용/적용성 revision. 단순 modelInputId 변경은 semantic revision이 아니다.
- update: append_unique 또는 replace_snapshot.
- protocol: shared internal-data-contract의 schema/version.
- text: 직렬화 view. 신뢰/권한은 text에서 역추론하지 않는다.
- origin: 호스트 생성 여부. 사용자/도구 자료와 구별하며, raw 문자열에 자기 선언으로 넣어 승인하지 않는다.

현재 architecture view는 replace_snapshot, 고유 직접 관찰은 append_unique로 시작한다. mutable ledger/status view도 현재 상태를 표시하는 것과 immutable 전달 기록을 구별한다. 기존 하나의 문자열에서 startsWith/첫 두 줄을 읽어 임의 하위 자료까지 버리는 경로는 제거한다.

modelInputId와 input별 issuance map은 prepared input metadata에 둔다. 모델에게 필요한 label은 동일 scope/content에서 안정적으로 유지할 수 있지만 **최종 manifest에 실린 label만 해당 입력에 발급**한다. 문자열 includes(view.text)로 발급 여부를 추론하는 경로를 manifest 검증으로 바꾼다. 동일 의미 입력에서 nonce 때문에 매번 전체 JSON을 다시 싣지 않는다.

### 5.2 입력 준비 결과

기존 prepareRoundInput 결과를 명확한 PreparedInput 계약으로 정리한다.

- 최종 Chat, tool surface, exact measurement/fingerprint.
- 최종 admitted part manifest, omitted/retired part와 이유.
- 이번 입력용 evidence/pattern/decision refs.
- source/history 변경안과 shown-data 변경안.
- 압축 전 baseline, 목표 정책, 최종 post-optional disposition.
- 다음 phase를 실행해 달라는 명령 대신 기존 ready/pressure/continuation_blocked 결과.

후보 평가에서 retained state/shown-state를 직접 확정하지 않는다. 선택 결과의 변경안만 loop에 반환한다. 최종 입력과 결과 manifest를 하나의 값으로 넘겨, 이미 바뀐 retention을 다른 후보가 읽는 순환 의존을 줄인다.

실제 호출 경계에서 dispatch_attempted를 기록하고, 모델 소비 여부는 관찰 가능한 경우에만 별도로 기록한다. 준비된 입력이 있다는 사실을 모델이 읽었다는 증명으로 사용하지 않는다. 현재 저장 창의 pre-dispatch checkpoint 기능은 유지하되 그 상태명을 구별한다.

### 5.3 관찰과 판단

EvidenceManager는 existing exchangeIndex에서 request/result의 provider/project/sourceKey/batch/order를 보존한 관찰을 만든다. ReadLedger는 관찰 병합과 충돌 판정을 한 번 수행한다. architectureSources가 자체 last-wins 정책을 추가하지 않는다.

동일 배치 동일 hash는 병합 가능, 다른 hash는 conflict/unknown, 이후 순서가 확인된 직접 read는 새 관찰로 대체 가능하다. archive/search/restore는 새로운 직접 읽기를 가장하지 않는다. 효과가 불명인 write는 기존 무효화 정책을 유지한다.

ArchitectureState의 binding은 policy와 같은 effective scope를 받는다. project mismatch/engine applicability/catalog/pattern generation/source version 변화는 구별된 invalidation reason을 낸다. source, pattern dependency 모두 동일 판단 revision 전이에 연결한다.

### 5.4 note 결과와 저장

null에 여러 뜻을 싣지 않는다. unchanged / accepted_update / explicit_clear / rejected_budget / failed를 구분하고, caller는 accepted 또는 explicit_clear일 때만 해당 영역을 교체한다.

- 일반 영역의 명시 clear는 구조 영역 삭제를 의미하지 않는다.
- host invalidation은 실제 상태가 달라졌을 때만 revision 증가.
- 범위를 벗어난 입력/대형 note는 기존 판단을 조용히 삭제하는 대신 typed rejection과 보존 결과를 반환.
- schema별 char 제한과 serialized envelope의 UTF-8 byte 제한은 하나의 NoteLimits 계약에서 계산/검증.
- read의 방어적 file-size cap은 writer가 허용한 전체 envelope를 수용해야 한다. 입력 token allowance는 별도로 유지한다.
- 저장 한도를 늘려 입력 예산까지 우회하지 않는다. v1/v2 읽기 호환과 old oversized-but-valid note 복구를 테스트한다.
- 가용성·상태 변화에 필요한 최소 host metadata의 저장 실패는 명시적으로 기록하고, 성공으로 보고하지 않는다.

NoteLimits는 모델 작성 내용의 상한과 host 수명 metadata의 상한을 나누고, 두 부분을 포함한 전체 envelope의 물리 상한을 계산한다. 항목 수·dependency 수·revision 표현의 기존 제한에서 host metadata의 최대 크기를 산출하여 같은 note envelope 안에 여유를 확보한다. 임의의 큰 숫자로 상한만 올리지 않는다. 동일 의미 반복은 revision을 늘리지 않고, revision 표현 상한에 도달하면 재사용 가능한 revision으로 wrap하지 않고 typed terminal/rebase 처리로 이전 patch를 거절한다.

이미 6,000자를 채운 legacy v2는 일반 판단을 삭제하지 않는 명시적 migration 경로로 읽는다. 변경된 한도/저장 envelope 계약은 reader와 writer 및 generated schema를 함께 배포하고, 이전 reader가 읽을 수 없는 형식이면 version을 올린다. migration/저장 실패 시 기존 판단의 내용은 보존하되 current로 재승인하지 않으며 복원 시 기존 historical 검증을 유지한다. 모델 입력에 실을 note의 token allowance는 여전히 별도다. 별도 무효화 DB는 만들지 않는다.

### 5.5 입력 승인·판단 commit·종료의 연결

새 round 상태 기계를 추가하지 않고 기존 round transaction에 다음 순서를 명시한다.

1. prepare는 기존 상태의 revision/scope epoch를 읽고 후보별 입력과 변경안을 만든다. 후보 선택 중 영속 note, 발급 권한, 이미 보여준 자료 목록을 확정하지 않는다.
2. 최종 후보 채택은 기존 ContextManager의 단일 적용 API가 expected base revision, execution/scope epoch, 최종 fingerprint, tool surface, reserve를 검증한 뒤 한 번 수행한다. loop는 이 API를 호출하며 shown-state를 직접 수정하지 않는다. 후보 평가 중 scope나 원본 버전이 달라졌다면 해당 후보의 발급표를 폐기하고 기존 bounded 재준비 경로로 돌린다. 채택 전 취소는 준비 변경안을 폐기한다.
3. 기존 pre-dispatch window checkpoint는 계속 허용한다. 다만 checkpoint/dispatch_attempted/모델 소비 관찰을 서로 다른 상태로 기록한다. checkpoint가 있다는 이유로 보지 않은 source를 consumed로 처리하지 않는다. 해당 phase의 알려진 실패에서 발급표를 폐기하고 예약을 반환한다.
4. 모델 patch는 해당 execution과 선택된 input의 issuance map에만 적용한다. stable label이 같아도 다른 준비 후보·이전 input·종료 실행의 patch를 승인하지 않는다. 기존 transaction의 planningCommitted와 입력 발급표 모두를 만족할 때만 모델 판단을 적용한다.
5. 이미 발생한 도구 효과와 host source invalidation은 모델 patch/저장 실패로 되돌리지 않는다. 취소 중 변경 결과가 불명인 경우 기존 conservative invalidation을 유지한다. note 합성 거절은 새 모델 판단을 거절할 수 있지만 host 무효화 전 상태를 복원하면 안 된다.
6. NotePersistence가 각 저장 경로의 결과를 모아 상태를 보고한다. window에 mirror한 상태, window 파일의 durable flush, history 파일의 atomic write는 서로 다른 관찰이다. 한쪽 성공을 양쪽 저장 성공으로 표시하지 않으며, 복원에서 구 current 판단이 재승인되지 않도록 기존 historical/scope 검증을 유지한다.
7. 취소 시 확인된 request/result pairs와 dispatched-but-unobserved trace를 기존 transaction 규칙으로 보존한다. 결과 없는 쓰기는 outcome_unknown을 유지하고 모델 planning은 commit하지 않는다. 기존 finally/release/close 경로가 모든 종료에서 한 번 정리한다. 원래 round의 capture/terminal seal 경계 뒤 callback은 active note, ledger, shown-state를 갱신하거나 lease를 재생성하지 못한다. 정리 실패는 기존 cleanup telemetry로 보고하며 조용히 성공 처리하지 않는다.

### 5.6 pattern generation을 비교할 수 있는 조건

pattern observation에는 provider/index/corpus 식별자, catalogHash/contentHash, generationId, 반환 요청의 causal batch, 관찰 출처(query/page/bundle)를 구별한다. generationId 문자열의 크기나 배열 위치를 최신 순서로 쓰지 않는다.

같은 지식 origin에서 확인된 현재 generation 전환은 의존 판단을 stale로 바꿀 수 있다. 순서 불명인 서로 다른 generation이나 다른 origin의 같은 patternId는 unknown/conflict로 남기며, 과거 page 재전달로 active generation을 되돌리지 않는다. 본문·catalog가 같고 generation만 달라도 근거 연결을 재검증해야 한다. 소비자가 새 generation을 관찰하지 않았으면 최신이라고 추정하거나 자동 refresh하지 않는다. 이 처리는 기존 evidence adapter와 ArchitectureState dependency reducer 안에서 수행하고 별도 generation 권위 저장소를 만들지 않는다.

## 6. 리팩토링 작업 순서

### R0 — 경계 테스트와 baseline 고정

현재 미커밋 구현 및 generated build identity를 기록하고, 기존 코드에서 아래 실패를 재현하는 최소 fixture를 먼저 추가한다. 원본 대화와 게임 코드에 의존하지 않는다. 기존 정상 회귀는 대조군으로 유지한다. 이번 감사에서 owner-boundaries 11개 + lifecycle 13개 + note 20개, 총 44/44가 통과했다. 이것은 새 결함을 반박하는 증거가 아니라 정상 보호를 보존해야 할 기준이다. 영향의 심각도와 작업 순서는 구별하며, P2 출처 경계도 후속 리팩토링의 선행 조건이므로 R1에 둔다.

산출물: 실패/정상 fixture 대응표, owner/port 목록, 입력 종류별 update policy, migration/rollback 기준.

### R1 — 출처 scope와 인과 순서 정리

수정: tool-evidence/exchangeIndex adapter, EvidenceManager.recordReturnedSources/architectureSources, ReadLedger, reference scope resolver, architecture-round/state.

- observation에 batch/order/project/provider를 끝까지 전달.
- 버전 충돌을 하나의 reducer에서 판정.
- effective scope를 policy/state/ref issuance가 공유.
- auto project A→B에서 A ref를 B 판단에 발급하지 않음.
- scope 미확정은 baseline/후보 정보로 제한하고 구조 판단의 현재 적용성을 승인하지 않음.
- 비교 작업의 서로 다른 프로젝트는 별도 partition으로 유지하며 단일 active scope처럼 섞지 않음.
- 기존 explicit mismatch·archive/historical·unknown write 방어를 재사용.

완료: same-batch conflict→unknown, ordered later read→새 버전, auto/explicit project 경계 모두 동일한 참조 규칙, 순서만 바꾼 batch replay가 권위를 바꾸지 않음.

### R2 — typed part와 snapshot 수명, 출력 공통 계약

수정: internal-data-contract, reference-rendering, architecture-input, design-guidance, context-manager, round-input, internal-data-output, prediction-stream, history sanitizer.

- Derived+architecture 결합 문자열 제거.
- stable semantic revision으로 같은 view는 재사용, 새 snapshot은 크기와 무관하게 교체.
- scope/phase 무효화 때 폐기 대상이 남지 않음.
- 새 architecture view만 바뀌어도 정확히 새 view가 전달됨.
- 오래된 optional을 pending baseline에서 실제 제거.
- 같은 registry/parser를 GUI/history/복구/저장에도 사용.
- 복원된 assistant text라는 이유만으로 host-origin으로 인정하지 않으며 사용자가 요청한 프로토콜 예시는 보존.
- v2 footer 문법 안내는 note protocol 소유자로 옮기고 snapshot 본문에 반복하지 않음.

완료: 동일 의미 20라운드에서 reference resident량이 선형 증가하지 않음. current snapshot 1개. 혼합/변형/절단 echo가 내부 보고로 재주입되지 않음. 부정확한 legacy 자료는 보수적으로 보존하고 판별 가능한 echo만 정리.

### R3 — 압축·입장·최종 목표를 단일 순서로 정리

수정: prepareRoundInput, ContextManager/createInputAssembler, BudgetBroker, GuidanceRetention/shown-data 연결, 관련 telemetry.

실행 순서:

1. 신뢰 경계와 scope/invalidations 반영.
2. phase/scope에 따른 필수 authority·engine·verification·task closure를 확정하고 반환 evidence와 함께 구성.
3. 이전 optional 자료를 제외한 동일 baseline 측정.
4. 이 baseline이 압력 조건을 만족할 때만 source projection/compaction.
5. 이미 확정된 mandatory baseline 위에서 optional 후보와 후보별 dependency closure를 구성. optional에만 필요한 dependency는 optional 비용에 포함하며 mandatory floor로 승격하지 않음.
6. 같은 기준점에서 **최종 resident optional 전체**가 공용 allowance 안인지 평가.
7. 최종 자료/도구/reserve를 포함한 입력을 재측정.
8. 하나의 prepared input/manifest/issuance/change-set 반환.
9. 기존 loop가 ContextManager의 단일 채택 API를 호출하고 dispatch 및 round transaction을 소유.

정상 성장 중에는 high-water까지 성장하는 기존 정책을 유지할 수 있다. **압축을 수행한 라운드는 최종 optional 입장 후 effective low-water에 도달하는 것을 기본 계약으로 변경**한다. mandatory floor가 low를 넘는 경우만 floor_exception으로 구별하며, optional 자료를 넣기 위해 더 많은 source를 버리지는 않는다. 먼저 상세 카드/추가 snapshot 설명을 줄인다. 필수 engine/authority 계약은 mandatory로 분류해 그 floor에 포함한다.

configured/effective/baseline/final 목표를 별도 필드로 기록한다. 참고 입장 전에만 low를 만족한 것을 최종 compacted_to_low로 기록하지 않는다. 기존 후보 시도 상한과 measurement cache는 유지하고 측정 정확도·scope가 달라진 후보의 캐시를 재사용하지 않는다.

여기서 압축 완료 라운드는 compactionChanged=true, 즉 source/history의 선택 결과가 실제 변경된 경우를 뜻한다. compactionRequested=true이나 changed=false인 경우는 별도 no_change/pressure disposition으로 남겨 압축 완료라고 보고하지 않는다. low 미달이 새 압축·복구 반복을 자동 생성하지 않으며 기존 시도 상한과 pressure 분기를 따른다. 필수 baseline이 달라졌으면 기존 측정값을 재사용하지 않는다.

완료: 과거 optional 17,206자 fixture에서 source8개 유지. mandatory가 같고 source가 같다면 optional 추가만으로 source eviction이 생기지 않음. 압축 완료 직후 최종 low 달성 또는 명시 floor_exception. final token/schema/issuance 일치 유지.

### R4 — note 트랜잭션과 저장 round-trip

수정: architecture-state/round/note-contract, continuity-model-notes, NotePersistence, replaceActiveNote, semantic-handoff, working-context note adapter.

- invalidation 멱등성 및 typed merge result.
- 준비된 상태/일반 note/구조 note의 변경을 분리하고, merge 실패를 clear로 전달하는 경로 제거.
- 일반 note update와 구조 patch는 독립 검증하되 실제 합성/용량 결정은 한 경계에서 수행.
- host 무효화는 모델 patch의 실패와 독립적으로 유지. 예산 때문에 무효화 전 current 상태가 복원되면 안 됨.
- 동일 NoteLimits를 writer/reader/validator에 사용하고 UTF-8 envelope 기준을 검증.
- NotePersistence의 history/window 우선순위를 유지하면서 저장 성공/부분 실패를 구별.
- 파생 source facets는 ledger→window 한 경로만 사용. note에는 모델 판단을 저장하고 관찰 캐시를 복제하지 않음.

완료: 6,000자 revision 경계에서 일반 판단 보존. 동일 invalidation 반복은 revision 고정. ASCII/Korean/emoji v1/v2 write→read 동등. window 실패/history 성공, 그 반대, 둘 다 실패 및 parentLineage 복원에서 거짓 성공/조용한 clear 없음.

### R5 — 패턴 데이터의 본문·generation·의존 수명 통합

수정: architecture_pattern_catalog, collect_project_guidelines, rag_build_writer/metadata/outputs, direct_rag_evidence/search/chunk_read, EvidenceManager.architecturePatterns, 구조 판단 의존 무효화.

- canonical hash 입력 필드를 raw 경계에도 보존하고 collector-current/writer에서 같은 verifier 사용.
- 본문과 metadata 불일치는 staging generation 게시 전에 거절. 이전 index 유지.
- query/page의 공통 KnowledgeObservation projection; pattern identity와 generation, whole-record/본문-chunk hash 범위 구별.
- mixed filter의 corpus capability와 pattern hit, metadata omission을 별도 기록.
- 확인된 새 generation/contentHash가 기존 pattern 의존 판단과 다르면 stale/unknown으로 무효화. 본문 비교 없이 같은 patternId만으로 현재성을 승계하지 않음.
- 새 generation을 관찰하지 않았을 때 자동 refresh하지 않음. 제공되지 않은 최신성은 unknown.
- 일반 패턴 수집과 엔진 source tier의 결합은 풀되 기존 staged refresh 안의 독립 단계로 연결한다. lite에서 어떤 corpus를 제공하는지 manifest로 명시하고, 이전 stage의 패턴을 최신으로 오인하지 않음.

완료: raw 본문 변조 거부, query→page→압축→page-only 뒤 identity 유지, mixed 일반 hit만이면 pattern matched=false, generation 변경에 의존 판단 무효화, 구버전 index는 unknown으로 호환.

### R6 — 정책 입력·역할 배분·coordinator 책임 축소

수정: guidanceObjective adapter, architecture-policy, design-guidance, round-input, prediction-loop 호출부.

- text만 잘라 넘기는 대신 기존 objective owner의 reason/완전성 의미를 보존. 별도 목적 parser는 만들지 않음.
- mandatory authority/engine/verification/task closure와 optional supplement/pattern을 명시.
- optional 여지가 있을 때 관련 카드 후보를 공동 비교. 6절 상한을 단순 확대하지 않음.
- 요청/선택/입장/전달/저장 계측을 구분. selected IDs만으로 기능 실행을 주장하지 않음.
- source-derived positive facets를 보존할 필요는 압축 재현으로 결정하고 기존 source observation에 제한된 metadata로 붙임. source availability와 source validity를 혼합하지 않음.
- prediction-loop는 기존 session/phase/cancel/round transaction과 prepared input 소비를 담당. string parsing, pattern 선택, merge fallback, source latest 판정을 loop에 새로 넣지 않음.
- round-input은 후보 준비/측정 연결을 담당. 영속 저장·phase 전환·도구 호출 권한을 갖지 않음.

완료: 불완전/모호한 목적에서 상세 구조 정책 가드 적용, 일반 Unreal 요청에 baseline 동작, Off/Unity/unknown scope 비활성 유지, mandatory-only면 정확한 생략 사유, 정상 보고와 내부-only의 상태 구분.

### R7 — 통합 검증·측정·배포 단계

제품 변경 후 단계다. 이번 문서 작성에서는 수행하지 않는다.

- 관련 plugin 전체 테스트, Agent/도구 반환 경계, RAG/Python 영향 테스트, 생성 계약/hash/build identity 검사.
- 다음 절의 종단 시나리오와 기존 정상 회귀 실행.
- 같은 모델/설정/프로젝트 snapshot으로 변경 전후 각각 3회 측정. 일반 Unreal 작업과 명시 구조 검토를 구분.
- 완료 품질·재조회·중복 reference·전달 카드·저장 일치·토큰·시간의 중앙값/범위/실패 횟수 보고.
- 설정/프로젝트/인덱스를 보존하며 설치하고 다음 실제 실행 loaded identity까지 확인. 전체 index 재생성은 RAG schema/content 경계의 필요 여부로 별도 결정.
- 안전한 rollback 패키지/설정 보관. 원 대화 파일을 자동 수정하거나 기존 window를 대량 삭제하지 않음.

## 7. 수명 및 검증 행렬

| 사건 | source observation | 입력 part / refs | 판단 / note | 실행·저장 |
|---|---|---|---|---|
| 새 실행 | 복원은 historical | 새 발급표; 실행 로컬 retention 초기화 | scope 검증 후 historical | 기존 context/session 소유자 |
| 같은 입력 재평가 | 바뀌지 않음 | semantic revision 같으면 재사용; tentative 후보는 commit 없음 | 변경 없음 | 측정 cache만 사용 |
| 동일 배치 복수 버전 | conflict/unknown | current 근거 ref 불발급 | 의존 판단 current 승인 금지 | 자동 재조회 없음 |
| 순서 확정 직접 read | 새 버전 기록 | 해당 input에만 발급 | 이전 의존 stale; 새 판단은 명시 patch | 기존 tool 경계 |
| projection/compaction | version 유지, availability 변경 | obsolete snapshot 교체, optional 우선퇴거 | 관찰과 판단 구별 | 최종 측정 뒤 준비 승인 |
| source write/불명 결과 | 기존 무효화 규칙 | 영향 refs 폐기 | idempotent stale/unknown | 취소돼도 무효화 보존 |
| 프로젝트 전환 | partition 유지 | 현재 scope만 발급 | 불일치 판단 적용성 폐기 | 기존 note reconcile |
| pattern generation 변경 | 지식 provenance 갱신 | 구 identity current 발급 금지 | 해당 의존 무효화 | 자동 refresh 없음 |
| 보고/복구 단계 | 반환 근거 유지 | 새 구조 탐색 없음; phase별 snapshot 정리 | 미완성 patch commit 없음 | 기존 retry 상한 유지 |
| 취소/timeout/late callback | 확인된 pairs와 dispatched-but-unobserved trace 보존; 결과 없는 쓰기는 outcome_unknown | 발급 수명 종료; terminal seal 뒤 갱신 금지 | 미완성 patch 거절; host 무효화 유지 | 기존 release/close 1회; lease 재생성 금지 |
| 저장 실패 | 관찰 사실과 저장 성공 구별 | 임시 참조 비영속 | typed failure, clear 금지 | 한 소유자가 실패 계측 |
| 검증된 parentLineage | historical 복원 | 현재 권한 재발급 아님 | historical 재활성화 | 기존 ancestry 검증 |
| 미검증 fork/다른 프로젝트 | 적용성 불명/폐기 | refs 승계 금지 | scope 정합성 거절 | 자동 병합 없음 |

필수 회귀 묶음:

- C: 20라운드 변화 없음/새 source만 추가/새 architecture만 변경/현재 snapshot 더 큼/phase·scope 무효화/후보 재평가 취소/optional만 거대함.
- A: 같은 배치 동일·다른 hash/도착 순서 반전/ordered read/unknown write/archive 재전달/auto A→B/explicit mismatch/현재 scope 미확정.
- N: normal+architecture budget 경계/revision 자릿수·상한 변화/반복 invalidation/legacy 6,000자 migration/일반 explicit clear/merge rejected/UTF-8 ASCII·한글·emoji/두 저장소 fallback/atomic write 실패/결과 0건 취소/close 후 callback.
- K: canonical body·metadata 변경/query→page/다중 page/generation 변경/mixed filter/metadata 생략/old index/실패 generation의 이전 index 보존.
- O: stream on/off/exact·변형·절단·혼합 echo/정상 보고 뒤 internal-only supplement/사용자 요청 예시/off·warn·pause·recover·observeOnly.
- P: 목적 완전/잘림/모호함/일반 작업/6절 optional 경쟁/mandatory-only/저예산 fallback/실제 manifest와 issued refs 일치.

최소 종단 시나리오 4개:

1. Auto 프로젝트 인식 → 여러 파일 읽기 → 구조 판단 → context pressure → optional 제외 → compaction → 새 입력의 같은 판단 view → 정상 보고 → v2 저장 → 다음 parentLineage 복원.
2. 같은 배치 소스 버전 충돌 → 판단 current 거절 → 이후 명시된 직접 read → 새 ref 발급 → 취소 중 write 결과 불명 → 재시작에서도 invalidation 유지.
3. pattern query → page → query 본문 압축 → 새 generation/page → 의존 판단 무효화 → malformed patch → 일반 note 보존.
4. 내부 프레임 반복 → 설정에 따른 감지/중단 → 내부-only 무복구 → GUI/history/window 정리 → 정상 사용자 예시 보존.

검증에서 “모델이 알아서 유효 footer를 작성한다”는 fake-model 전제를 피한다. footer 없음·변형 출력·중간 실패가 기본 시나리오에 들어가야 한다. 결정적 계약 테스트와 실제 모델의 설계 품질 평가는 별개다.

## 8. 운영 계측과 범위 제한

최종 input manifest에는 원문 대신 kind/scope digest/semantic revision/update mode/current-retired counts/문자 수/최종 참조 수를 기록한다. 토큰 비용은 같은 baseline의 정확한 차이로 표현하고, 측정하지 않은 part별 값은 unknown으로 둔다. 합계가 다른 토큰화 경계를 무리하게 더하지 않는다.

추가 지표: optional resident 비용, superseded snapshot 수, baseline pressure와 final pressure, configured/effective low-high, compaction requested/changed/final target, source conflict 수, scope rejection 이유, note rejected와 clear 건수, history-write/read round-trip, pattern identity omission/generation mismatch.

반복 off/warn에서는 조기 중단을 강제하지 않는다. recover의 알려진 내부-only 반복 중단은 새 복구 호출을 자동 생성하지 않는다. 정상 보고서를 이미 전달한 경우 empty/내부-only 보완이 원 보고서를 지우지 않는다.

새로 만들지 않을 것: 독립 source-version 저장소, 별도 note DB, architecture 전용 token budget, 새 phase/retry 상태 기계, 숨은 자동 RAG/전면 프로젝트 스캐너, 도구 권한용 별도 참조 해석기, cache를 핑계로 obsolete snapshot을 보존하는 우회 경로.

남은 불확실성: 이번 round7 압축에서 과거 optional이 차지한 실제 기여율, 종류별 과거 runtime 4,920자의 구성, 새 구조 판단의 실제 모델 품질 향상, backend 실제 cache token 수, 새 릴리즈에서의 정상 최종 보고 완료율. 이들은 추측으로 닫지 않고 위 계측과 실행 평가에서 확인한다.

형식 검사기는 근거 패킷의 구조와 필수 기록만 검사한다. 소스/재현/실측 확인 수준을 대신하지 않는다. 구현은 R0의 실패 재현과 R1–R6의 경계 수정을 작은 검토 단위로 진행하고, 정상 exact measurement·도구 권한·취소·복원 보호를 끝까지 대조군으로 유지한다.

