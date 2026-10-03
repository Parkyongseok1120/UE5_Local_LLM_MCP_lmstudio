# 전체 구조·계약·책임·수명 재점검 — 2026-10-03

## 결론과 범위

현재 구조는 기능별 분리가 상당 부분 되어 있다. 그러나 **실행 사실·근거 유효성·저장 상태를 여러 모듈이 독립적으로 해석하는 경계**에 결함이 남아 있다. 문서 주입 강화만으로 해결할 수 없는 종류이며, 기존 소유자들의 입력·출력·생명주기 계약부터 보정해야 한다.

기준은 `6893493`에 이미 존재하던 미커밋 구현을 합친 현재 working tree이다. 제품 소스·설정·설치·게임 프로젝트는 이 감사에서 변경하지 않았다. 감사 문서와 독립 재현 파일만 추가했다. 이전 작업의 미커밋 파일을 감사자가 수정한 것으로 계산하면 안 된다.

현재 활성 경로의 시작→분기→실행/저장→반환→소비자를 추적했다. 저장소 모든 줄, 모든 OS·엔진 버전, 실제 LM Studio/Unity/Unreal 런타임을 전부 검증했다는 의미는 아니다. fake dependency 및 임시 fixture 재현은 실제 Editor 실행과 구분한다. 첨부 라이브 테스트의 특정 오판을 아래 결함 하나로 확정할 최종 host 입력·wire trace는 없다.

### 이전 설명의 정정

일반 `WorkingContext.project()`는 현재 코드에서 이미 body-first projection이다. JSON 앞부분 절단 문제는 **별도 semantic summary 근거 경로**에 남아 있다(A04). `compaction-tool-memory`의 분리 요청 pairing도 이미 보정되어 있다. 현재 남은 중복 구현은 retry 및 reference 소비자이다(A02). 이전 구현에서 해결한 문제를 새 결함으로 다시 세지 않았다.

## 1. 현재 책임과 변경 방향

| 경계 | 현재 담당 | 유지할 책임 | 보정할 계약 |
|---|---|---|---|
| 한 요청의 실행 | PredictionLoop / RoundLoop / DeliveryController | 생성·도구 round·출력 종료 조립 | 출력 종료와 작업 사실 판단의 분리 유지 |
| 예산·주입 | ContextManager / BudgetBroker / guidance 선택 | 실제 입력 fit, reserve, 선택적 문서 | Auto가 실행기·검증자 역할을 맡지 않도록 유지 |
| 기록 해석 | evidence-manager / reference-context / working-context | 소비 목적별 view | pairing·identity·coverage의 공통 해석 |
| 판단 note | activeNote / noteStore / workingContext.note | 모델 판단 보관 | 현재 상태 하나, 저장은 스냅샷; clear·scope 전이 일원화 |
| 파일 변경 | Unreal capability / transaction / version policy | 검증·commit·receipt·recovery | commit 결과와 후처리 결과 분리, 복구 시 물리 경계 |
| child process | diagnostic capability / bounded runner | 시작·출력·종료 | signal 전달, bounded shutdown, close·timeout 경쟁 정산 |
| Unity | Node adapter / Bridge / Operations / EvidenceStore | transport·Editor dispatch·원장·관찰 | 실행 origin과 delivery identity, applied와 budget 실패 분리 |
| RAG | selection / history / freshness / generation | 검색·receipt·신선도·게시 | 검색 identity, 프로젝트별 provenance, cleanup 독립 |
| 패키지 | installer / portable builder | 설치·검증·게시·복구 | 기존 output 보존과 게시 실패 복원 |

큰 manager를 하나 추가해 모든 상태를 모으는 변경은 필요하지 않다. 공유해야 하는 것은 **동일 사실의 정의와 전이 규칙**이며, 도구별 실행 책임은 현재 소유자에 둔다.

## 2. 발견 목록

우선순위 P1은 주요 실행 계약, P2는 조건부 실패·확장/유지 문제, P3는 표현 문제이다. 수정 순서는 등급뿐 아니라 실제 side effect와 변경 범위도 고려한다. `TestVerified`는 아래 독립 재현 범위이며 실제 LM Studio 재현이라는 뜻이 아니다.

| ID | 등급 | 판단 / 확인 | 내용 |
|---|---|---|---|
| A01 | P2 | Bug / TestVerified | 판단 note의 현재 상태와 저장 window가 따로 갱신되어 clear·목표 변경 뒤 과거 판단이 복구된다 |
| A02 | P2 | Bug / TestVerified | 요청·결과 pairing 구현이 갈라져 합법적인 분리 요청 묶음에서 완료 결과가 누락된다 |
| A03 | P2 | Bug / TestVerified | 읽기 실패가 최신 파일 관찰을 무효화하지 않아 과거 변경 근거가 계속 남는다 |
| A04 | P2 | Bug / TestVerified | semantic summary용 근거 projection은 JSON 앞부분만 잘라 실제 본문을 누락할 수 있다 |
| A05 | P2 | Ambiguous / SourceVerified | window manifest와 버려진 scope의 저장 수명은 evidence archive의 quota·TTL로 관리되지 않는다 |
| A06 | P2 | Bug / TestVerified | headless 경로는 긴 현재 turn을 줄이지 못한 뒤 입력 여유가 음수여도 그대로 전송할 수 있다 |
| A07 | P2 | Bug / TestVerified | Unity 하위 샘플의 설정 파일을 루트 프로젝트의 엔진·package 설정으로 승격한다 |
| A08 | P1 | Bug / TestVerified | Unity 작업이 적용된 뒤 응답 크기 제한 실패가 not_applied로 보고된다 |
| A09 | P2 | Bug / TestVerified | 프로세스 timeout이 종료 처리의 무기한 대기에 종속되어 응답 deadline을 보장하지 못한다 |
| A10 | P1 | Bug / TestVerified | MCP 취소 신호가 Unreal 도구 실행기로 전달되지 않는다 |
| A11 | P2 | Bug / TestVerified | 텍스트 검색의 IO 누락이 완전한 검색·일치 없음으로 해석될 수 있다 |
| A12 | P2 | Bug / TestVerified | RAG 반복 억제 receipt가 바뀐 검색 조건과 긴 query를 같은 요청으로 취급한다 |
| A13 | P2 | Bug / TestVerified | RAG generation 게시 후 journal 정리 실패를 미게시·롤백 시도로 보고한다 |
| A14 | P2 | Bug / TestVerified | 다른 프로젝트 refresh가 전역 DB 시각을 갱신하면 오래된 프로젝트 근거도 fresh로 판정된다 |
| A15 | P2 | Bug / TestVerified | 패키지 최종 게시 실패가 기존 정상 output과 새 staging을 모두 없앤다 |
| A16 | P2 | Bug / SourceVerified | Unity Bridge 응답 장식이 저장된 증거의 원래 session/domain identity를 현재 값으로 덮는다 |
| A17 | P2 | Bug / TestVerified | Unreal 파일 commit 뒤 receipt 관찰 실패가 실제 적용 결과를 일반 오류로 덮는다 |
| A18 | P1 | Bug / TestVerified | crash recovery가 같은 프로젝트 안에서 바뀐 canonical target을 원래 파일로 취급한다 |
| A19 | P2 | NeedsRuntimeProof / SourceVerified | Python RAG refresh와 synchronous stdio의 취소·시간 계약은 추가 실행 확인이 필요하다 |
| A20 | P3 | Bug / SourceVerified | 설치 시 ON 여부와 Auto 선택 문서가 현재 동작 설명과 일치하지 않는다 |

## 3. 상세 근거와 최소 변경

### A01 — 판단 note의 현재 상태와 저장 window가 따로 갱신되어 clear·목표 변경 뒤 과거 판단이 복구된다

**P2 · Bug · TestVerified**

empty footer 처리로 activeNote=null이 된 뒤 seal/restore에서 이전 판단이 남고, 다른 objective에서도 복구되는 함수 경로를 재현했다.

**근거 경로**

- [lmstudio-context-compactor-plugin/src/prediction-loop.ts:174](<C:/Users/sster/Documents/Git/UE5_Local_LLM_MCP_lmstudio/lmstudio-context-compactor-plugin/src/prediction-loop.ts:174>) — noteStore 복구는 objective를 비교하지만 workingContext.note fallback은 비교하지 않는다.
- [lmstudio-context-compactor-plugin/src/prediction-loop.ts:680](<C:/Users/sster/Documents/Git/UE5_Local_LLM_MCP_lmstudio/lmstudio-context-compactor-plugin/src/prediction-loop.ts:680>) — footer의 교체·clear는 activeNote만 갱신한다.
- [lmstudio-context-compactor-plugin/src/working-context.js:593](<C:/Users/sster/Documents/Git/UE5_Local_LLM_MCP_lmstudio/lmstudio-context-compactor-plugin/src/working-context.js:593>) — window는 독립적인 this.note를 저장한다.
- [lmstudio-context-compactor-plugin/src/continuity-model-notes.js:273](<C:/Users/sster/Documents/Git/UE5_Local_LLM_MCP_lmstudio/lmstudio-context-compactor-plugin/src/continuity-model-notes.js:273>) — reconcile은 refs/project를 검사하지만 현재 objective를 인수로 받지 않는다.
- [artifacts/architecture-audit-20261003/reproduction-results.json](<C:/Users/sster/Documents/Git/UE5_Local_LLM_MCP_lmstudio/artifacts/architecture-audit-20261003/reproduction-results.json>) — empty footer 처리로 activeNote=null이 된 뒤 seal/restore에서 이전 판단이 남고, 다른 objective에서도 복구되는 함수 경로를 재현했다.

**반대 근거·한계:** 일반 noteStore 복구는 objective를 비교하고, 참조된 근거와 project scope도 별도로 검증한다. ([lmstudio-context-compactor-plugin/src/prediction-loop.ts:174](<C:/Users/sster/Documents/Git/UE5_Local_LLM_MCP_lmstudio/lmstudio-context-compactor-plugin/src/prediction-loop.ts:174>)) 실제 LM Studio가 해당 fallback을 거친 순간의 입력은 없다. 이번 라이브 테스트 오판의 직접 원인으로 확정하지 않는다.

**최소 수정:** 현재 note의 replace/clear/reconcile를 단일 소유자의 전이로 만들고 두 저장 경로는 그 스냅샷만 저장한다. current objective/project/attachment 조건을 모든 복구에 동일하게 적용한다.

**검증:** clear→seal→restore, footer 교체, objective 변경, project 변경, attachment 폐기, 중단 후 복구에서 이전 판단이 되살아나지 않는지 확인한다.

### A02 — 요청·결과 pairing 구현이 갈라져 합법적인 분리 요청 묶음에서 완료 결과가 누락된다

**P2 · Bug · TestVerified**

같은 입력에서 canonical matches=2, recovery completed=1을 재현했다. reference diagnostic도 별도 재현에서 마지막 요청 결과만 남았다.

**근거 경로**

- [lmstudio-context-compactor-plugin/src/working-context.js:25](<C:/Users/sster/Documents/Git/UE5_Local_LLM_MCP_lmstudio/lmstudio-context-compactor-plugin/src/working-context.js:25>) — canonical exchangeIndex는 연속된 request A/B와 뒤따른 result A/B를 모두 연결한다.
- [lmstudio-context-compactor-plugin/src/evidence-manager.ts:121](<C:/Users/sster/Documents/Git/UE5_Local_LLM_MCP_lmstudio/lmstudio-context-compactor-plugin/src/evidence-manager.ts:121>) — completedRequestFingerprints는 새 assistant 요청 블록마다 active Map을 초기화한다.
- [lmstudio-context-compactor-plugin/src/reference-context.ts:79](<C:/Users/sster/Documents/Git/UE5_Local_LLM_MCP_lmstudio/lmstudio-context-compactor-plugin/src/reference-context.ts:79>) — pairedResults에도 요청 블록마다 초기화하는 독립 구현이 있다.
- [artifacts/architecture-audit-20261003/reproduction-results.json](<C:/Users/sster/Documents/Git/UE5_Local_LLM_MCP_lmstudio/artifacts/architecture-audit-20261003/reproduction-results.json>) — 같은 입력에서 canonical matches=2, recovery completed=1을 재현했다. reference diagnostic도 별도 재현에서 마지막 요청 결과만 남았다.

**반대 근거·한계:** 단일 assistant 블록에 요청이 함께 있으면 정상이다. compaction-tool-memory의 분리 요청 pairing은 현재 코드에서 이미 보정되어 있다. ([lmstudio-context-compactor-plugin/src/compaction-tool-memory.js:590](<C:/Users/sster/Documents/Git/UE5_Local_LLM_MCP_lmstudio/lmstudio-context-compactor-plugin/src/compaction-tool-memory.js:590>)) 모든 LM Studio 버전의 메시지 분할 빈도는 측정하지 않았다.

**최소 수정:** 순수 exchange matcher 하나를 기존 구현에서 추출하여 retry/evidence/reference 소비자가 같은 결과를 쓰게 한다. 소비자별 projection은 유지한다.

**검증:** 분리 요청, 역순 결과, duplicate/idless/orphan, 취소, assistant 경계별로 세 소비자의 매칭 및 ambiguity 판정이 일치해야 한다.

### A03 — 읽기 실패가 최신 파일 관찰을 무효화하지 않아 과거 변경 근거가 계속 남는다

**P2 · Bug · TestVerified**

NOT_FOUND/FILE_NOT_FOUND/ACCESS_DENIED 후 이전 diff가 남았다. FILE_VERSION_CONFLICT는 정상 제거됐다. 재현은 실패 응답에 identity를 줘도 발생했다.

**근거 경로**

- [lmstudio-context-compactor-plugin/src/compaction-tool-memory.js:824](<C:/Users/sster/Documents/Git/UE5_Local_LLM_MCP_lmstudio/lmstudio-context-compactor-plugin/src/compaction-tool-memory.js:824>) — 실패 응답은 FILE_VERSION_CONFLICT 외에는 파일 관찰에서 제외한다.
- [lmstudio-context-compactor-plugin/src/direct-compaction-core.js:585](<C:/Users/sster/Documents/Git/UE5_Local_LLM_MCP_lmstudio/lmstudio-context-compactor-plugin/src/direct-compaction-core.js:585>) — 변경 근거 무효화는 추출된 stateMemory 관찰만 사용한다.
- [lmstudio-unreal-agent-mcp/src/direct-read-capabilities.js:172](<C:/Users/sster/Documents/Git/UE5_Local_LLM_MCP_lmstudio/lmstudio-unreal-agent-mcp/src/direct-read-capabilities.js:172>) — NOT_FOUND 등 실패 결과는 성공 응답과 달리 명시적인 path/project metadata가 없는 경로가 있다.
- [artifacts/architecture-audit-20261003/reproduction-results.json](<C:/Users/sster/Documents/Git/UE5_Local_LLM_MCP_lmstudio/artifacts/architecture-audit-20261003/reproduction-results.json>) — NOT_FOUND/FILE_NOT_FOUND/ACCESS_DENIED 후 이전 diff가 남았다. FILE_VERSION_CONFLICT는 정상 제거됐다. 재현은 실패 응답에 identity를 줘도 발생했다.

**반대 근거·한계:** 성공한 hash 변경, 명시적인 삭제, conflict에는 기존 무효화 경로가 있다. ([lmstudio-context-compactor-plugin/src/change-evidence-memory.js:67](<C:/Users/sster/Documents/Git/UE5_Local_LLM_MCP_lmstudio/lmstudio-context-compactor-plugin/src/change-evidence-memory.js:67>)) 읽기 실패만으로 실제 삭제를 입증할 수 없다. 권한·일시 IO 실패일 수 있다.

**최소 수정:** 생산자 또는 신뢰된 request/result 연결점에서 resolved identity와 관찰 불가 상태를 보존한다. 최신 유효 근거를 내리되 역사적 변경 사실까지 지우거나 삭제됐다고 단정하지 않는다.

**검증:** 수정→읽기 실패→압축, scope 불명 실패, 다른 프로젝트 동일 경로, 복구 후 성공 읽기를 검증한다.

### A04 — semantic summary용 근거 projection은 JSON 앞부분만 잘라 실제 본문을 누락할 수 있다

**P2 · Bug · TestVerified**

앞 metadata가 긴 유효 envelope에서 summaryContainsBody=false, 일반 projectionContainsBody=true였다.

**근거 경로**

- [lmstudio-context-compactor-plugin/src/working-context.js:88](<C:/Users/sster/Documents/Git/UE5_Local_LLM_MCP_lmstudio/lmstudio-context-compactor-plugin/src/working-context.js:88>) — non-git semanticEvidenceView는 JSON.stringify 결과 앞부분을 자른다.
- [lmstudio-context-compactor-plugin/src/working-context.js:316](<C:/Users/sster/Documents/Git/UE5_Local_LLM_MCP_lmstudio/lmstudio-context-compactor-plugin/src/working-context.js:316>) — summaryEvidence는 항목별 640자 excerpt를 만든다.
- [lmstudio-context-compactor-plugin/src/working-context.js:373](<C:/Users/sster/Documents/Git/UE5_Local_LLM_MCP_lmstudio/lmstudio-context-compactor-plugin/src/working-context.js:373>) — 일반 project 경로는 body-first projection을 이미 사용한다.
- [artifacts/architecture-audit-20261003/reproduction-results.json](<C:/Users/sster/Documents/Git/UE5_Local_LLM_MCP_lmstudio/artifacts/architecture-audit-20261003/reproduction-results.json>) — 앞 metadata가 긴 유효 envelope에서 summaryContainsBody=false, 일반 projectionContainsBody=true였다.

**반대 근거·한계:** 일반 working-context projection 전체가 JSON 앞부분만 보는 것은 아니다. git summary 경로도 본문을 별도로 처리한다. ([lmstudio-context-compactor-plugin/src/working-context.js:373](<C:/Users/sster/Documents/Git/UE5_Local_LLM_MCP_lmstudio/lmstudio-context-compactor-plugin/src/working-context.js:373>)) 합성 envelope 재현이며 첨부 라이브 로그에서 최종 summarizer 입력이 동일했는지는 확인할 수 없다.

**최소 수정:** 기존 bounded body projection을 재사용하고 identity/hash/range/truncation을 보존한다. 별도 요약기나 모델 호출은 추가하지 않는다.

**검증:** 긴 앞 metadata, 본문 위치 변경, code/text/body/items, 부분 읽기와 git observation에서 의미 본문과 출처가 유지되는지 확인한다.

### A05 — window manifest와 버려진 scope의 저장 수명은 evidence archive의 quota·TTL로 관리되지 않는다

**P2 · Ambiguous · SourceVerified**

새 lineage의 window가 record quota 밖에 남는다. 오래 쓰지 않는 scope를 주기적으로 정리하는 별도 경로도 확인하지 못했다.

**근거 경로**

- [lmstudio-context-compactor-plugin/src/working-context.js:189](<C:/Users/sster/Documents/Git/UE5_Local_LLM_MCP_lmstudio/lmstudio-context-compactor-plugin/src/working-context.js:189>) — lineage별 window 파일 이름을 만든다.
- [lmstudio-context-compactor-plugin/src/working-context.js:598](<C:/Users/sster/Documents/Git/UE5_Local_LLM_MCP_lmstudio/lmstudio-context-compactor-plugin/src/working-context.js:598>) — window manifest는 archive.put과 별도로 기록한다.
- [lmstudio-context-compactor-plugin/src/evidence-archive.js:96](<C:/Users/sster/Documents/Git/UE5_Local_LLM_MCP_lmstudio/lmstudio-context-compactor-plugin/src/evidence-archive.js:96>) — entries와 put의 quota/TTL 정리는 ev_ record만 대상으로 한다.

**반대 근거·한계:** 개별 evidence record에는 실제 quota/TTL이 있고 README는 수동 저장소 삭제 방법을 안내한다. ([lmstudio-context-compactor-plugin/src/evidence-archive.js:67](<C:/Users/sster/Documents/Git/UE5_Local_LLM_MCP_lmstudio/lmstudio-context-compactor-plugin/src/evidence-archive.js:67>)) 사용자 디스크 증가량과 전체 저장소 보존 기간 요구는 측정·확정하지 않았다. 8MiB가 전체 저장소의 약속이라고 확대하지 않는다.

**최소 수정:** 현 저장소 수명 담당자가 window와 record의 참조 관계를 함께 정리하도록 보존 정책을 정의한다. 활성 lineage/parent/pin은 보호한다.

**검증:** 여러 turn·branch·scope를 만든 fixture에서 활성 복구 근거 보존, 만료 window 정리, cleanup 실패 후 정상 읽기를 확인한다.

### A06 — headless 경로는 긴 현재 turn을 줄이지 못한 뒤 입력 여유가 음수여도 그대로 전송할 수 있다

**P2 · Bug · TestVerified**

단일 user turn+40 tool exchange, remainingTokens=-5000에서 82개 메시지가 그대로 반환됐다. adapter는 새 assistantCheckpoint/changeDataCheckpoint도 조합하지 않는다.

**근거 경로**

- [scripts/headless_mcp_chat.py:280](<C:/Users/sster/Documents/Git/UE5_Local_LLM_MCP_lmstudio/scripts/headless_mcp_chat.py:280>) — 문자 기반 estimate로 압축을 호출하며 current-turn 제한 옵션을 전달하지 않는다.
- [scripts/headless_compact.js:61](<C:/Users/sster/Documents/Git/UE5_Local_LLM_MCP_lmstudio/scripts/headless_compact.js:61>) — 기본 core 정책에서 omitted가 없으면 원본 messages를 반환한다.
- [scripts/headless_mcp_chat.py:334](<C:/Users/sster/Documents/Git/UE5_Local_LLM_MCP_lmstudio/scripts/headless_mcp_chat.py:334>) — 압축 후 별도 fit 판정 없이 요청 입력을 구성한다.
- [artifacts/architecture-audit-20261003/reproduction-results.json](<C:/Users/sster/Documents/Git/UE5_Local_LLM_MCP_lmstudio/artifacts/architecture-audit-20261003/reproduction-results.json>) — 단일 user turn+40 tool exchange, remainingTokens=-5000에서 82개 메시지가 그대로 반환됐다. adapter는 새 assistantCheckpoint/changeDataCheckpoint도 조합하지 않는다.

**반대 근거·한계:** GUI prediction-loop에는 별도 ContextManager/BudgetBroker fit 정책이 있다. 이번 발견은 headless 진입점이다. ([lmstudio-context-compactor-plugin/src/context-manager.ts:44](<C:/Users/sster/Documents/Git/UE5_Local_LLM_MCP_lmstudio/lmstudio-context-compactor-plugin/src/context-manager.ts:44>)) 재현은 adapter만 실행했고 모델 요청을 보내지 않았다. 토큰 추정의 정확도는 보증하지 않는다.

**최소 수정:** 기존 core의 complete-exchange current-turn 정책과 checkpoint 조합을 headless adapter에 연결하고 전송 직전 estimate/fit 실패를 명시한다.

**검증:** 긴 단일 turn, 최근 사용자 요청 보존, 완전한 도구 교환, 역할이 분리된 checkpoint, 미적합 입력의 전송 중단을 확인한다.

### A07 — Unity 하위 샘플의 설정 파일을 루트 프로젝트의 엔진·package 설정으로 승격한다

**P2 · Bug · TestVerified**

Assets/Templates/Packages/packages-lock.json의 NGO 버전과 하위 ProjectVersion 값이 root applicability로 반영되는 경로를 재현했다.

**근거 경로**

- [lmstudio-context-compactor-plugin/src/reference-context.ts:112](<C:/Users/sster/Documents/Git/UE5_Local_LLM_MCP_lmstudio/lmstudio-context-compactor-plugin/src/reference-context.ts:112>) — ProjectVersion/packages-lock 경로를 suffix만으로 식별한다.
- [lmstudio-context-compactor-plugin/src/reference-context.ts:237](<C:/Users/sster/Documents/Git/UE5_Local_LLM_MCP_lmstudio/lmstudio-context-compactor-plugin/src/reference-context.ts:237>) — 같은 프로젝트 안의 완전한 파일이면 metadata를 추출한다.
- [lmstudio-context-compactor-plugin/src/reference-context.ts:241](<C:/Users/sster/Documents/Git/UE5_Local_LLM_MCP_lmstudio/lmstudio-context-compactor-plugin/src/reference-context.ts:241>) — 추출된 version/packages가 applicability에 들어간다.
- [artifacts/architecture-audit-20261003/reference-results.json](<C:/Users/sster/Documents/Git/UE5_Local_LLM_MCP_lmstudio/artifacts/architecture-audit-20261003/reference-results.json>) — Assets/Templates/Packages/packages-lock.json의 NGO 버전과 하위 ProjectVersion 값이 root applicability로 반영되는 경로를 재현했다.

**반대 근거·한계:** 다른 프로젝트, scope 밖 파일, partial read, mutation/conflict는 기존 검사로 배제된다. ([lmstudio-context-compactor-plugin/src/reference-context.ts:225](<C:/Users/sster/Documents/Git/UE5_Local_LLM_MCP_lmstudio/lmstudio-context-compactor-plugin/src/reference-context.ts:225>)) 현재 사용자 Unity 프로젝트에 이런 샘플 경로가 존재하는지는 조사 범위가 아니다.

**최소 수정:** 기존 canonical root/path identity로 정확한 루트 Packages/packages-lock.json과 ProjectSettings/ProjectVersion.txt만 설정 근거로 인정한다. 하위 파일의 일반 관찰은 유지한다.

**검증:** root/nested sample/package fixture, 대소문자와 Windows separator, 다른 project를 비교한다.

### A08 — Unity 작업이 적용된 뒤 응답 크기 제한 실패가 not_applied로 보고된다

**P1 · Bug · TestVerified**

fake bridge에서 create 1회 적용 후 큰 결과를 반환하면 byteBudget=1024에서 not_applied가 된다. 재시도 판단의 실행 사실이 틀어진다.

**근거 경로**

- [lmstudio-unity-mcp/src/server.js:68](<C:/Users/sster/Documents/Git/UE5_Local_LLM_MCP_lmstudio/lmstudio-unity-mcp/src/server.js:68>) — bridge.call 결과를 받은 뒤 응답을 조합한다.
- [lmstudio-unity-mcp/src/server.js:76](<C:/Users/sster/Documents/Git/UE5_Local_LLM_MCP_lmstudio/lmstudio-unity-mcp/src/server.js:76>) — 실행 뒤 bounded를 호출한다.
- [shared-tool-core/files.js:17](<C:/Users/sster/Documents/Git/UE5_Local_LLM_MCP_lmstudio/shared-tool-core/files.js:17>) — bounded 실패가 not_applied 예외를 만들고 server catch가 그대로 반환한다.
- [artifacts/architecture-audit-20261003/behavior-results.json](<C:/Users/sster/Documents/Git/UE5_Local_LLM_MCP_lmstudio/artifacts/architecture-audit-20261003/behavior-results.json>) — fake bridge에서 create 1회 적용 후 큰 결과를 반환하면 byteBudget=1024에서 not_applied가 된다. 재시도 판단의 실행 사실이 틀어진다.

**반대 근거·한계:** Bridge 자체의 큰 응답 처리는 operationId가 있으면 outcome_unknown을 사용한다. 이 발견은 Node adapter의 후처리이다. ([unity-editor-bridge/Editor/Bridge.cs:162](<C:/Users/sster/Documents/Git/UE5_Local_LLM_MCP_lmstudio/unity-editor-bridge/Editor/Bridge.cs:162>)) 실제 Unity Editor에서 중복 객체가 만들어졌다는 뜻은 아니다. 동일 operationId 원장도 재실행을 막는다.

**최소 수정:** 변경 결과와 응답 전달 결과를 분리한다. 실행 전 최소 receipt 크기를 확보하고 실행 후 optional body만 줄이며 적용 결과와 operationId를 보존한다.

**검증:** applied+oversize, 관찰+oversize, 같은 ID 재조회, transport 실패와 신규 ID 재시도 판단을 확인한다.

### A09 — 프로세스 timeout이 종료 처리의 무기한 대기에 종속되어 응답 deadline을 보장하지 못한다

**P2 · Bug · TestVerified**

terminate가 끝나지 않는 fake child에서 timeout 이후 close까지 발생해도 promise가 끝나지 않았다.

**근거 경로**

- [lmstudio-unreal-agent-mcp/src/bounded-process-runner.js:162](<C:/Users/sster/Documents/Git/UE5_Local_LLM_MCP_lmstudio/lmstudio-unreal-agent-mcp/src/bounded-process-runner.js:162>) — timeout 때 settled=true로 바꾼 후 terminate promise를 기다린다.
- [lmstudio-unreal-agent-mcp/src/bounded-process-runner.js:173](<C:/Users/sster/Documents/Git/UE5_Local_LLM_MCP_lmstudio/lmstudio-unreal-agent-mcp/src/bounded-process-runner.js:173>) — 그 사이 close는 settled 때문에 finish를 완료하지 못한다.
- [lmstudio-unreal-agent-mcp/src/direct-diagnostic-capabilities.js:209](<C:/Users/sster/Documents/Git/UE5_Local_LLM_MCP_lmstudio/lmstudio-unreal-agent-mcp/src/direct-diagnostic-capabilities.js:209>) — 별도 run_command도 kill 완료 뒤 fallback timer를 설치한다.
- [artifacts/architecture-audit-20261003/behavior-results.json](<C:/Users/sster/Documents/Git/UE5_Local_LLM_MCP_lmstudio/artifacts/architecture-audit-20261003/behavior-results.json>) — terminate가 끝나지 않는 fake child에서 timeout 이후 close까지 발생해도 promise가 끝나지 않았다.

**반대 근거·한계:** 정상 종료 및 terminate가 정상 완료되는 timeout은 기존 경로로 끝난다. ([lmstudio-unreal-agent-mcp/src/bounded-process-runner.js:130](<C:/Users/sster/Documents/Git/UE5_Local_LLM_MCP_lmstudio/lmstudio-unreal-agent-mcp/src/bounded-process-runner.js:130>)) 실제 OS 종료 함수의 hang 빈도나 현재 LM Studio 대기가 이 경로였는지는 증명하지 않았다.

**최소 수정:** 기존 runner가 종료 시작 전에 bounded shutdown deadline을 소유하고 close/abort/timeout 경쟁을 한 번만 정산한다. 종료 확인 여부를 별도 결과로 유지한다.

**검증:** terminate resolve/reject/hang, close 경쟁, spawn throw/error, log 저장 실패를 확인한다. 죽었다는 증거가 없으면 종료 성공으로 반환하지 않는다.

### A10 — MCP 취소 신호가 Unreal 도구 실행기로 전달되지 않는다

**P1 · Bug · TestVerified**

이미 aborted인 context를 넣은 fake run_command에서도 spawn=1, kill=0이며 성공 결과가 나왔다. 공개 MCP 진입점에서는 signal 자체가 전달되지 않는다.

**근거 경로**

- [lmstudio-unreal-agent-mcp/src/direct-server.js:103](<C:/Users/sster/Documents/Git/UE5_Local_LLM_MCP_lmstudio/lmstudio-unreal-agent-mcp/src/direct-server.js:103>) — SDK extra에서 sessionId만 requestContext로 전달하고 signal은 버린다.
- [lmstudio-unreal-agent-mcp/src/direct-diagnostic-capabilities.js:168](<C:/Users/sster/Documents/Git/UE5_Local_LLM_MCP_lmstudio/lmstudio-unreal-agent-mcp/src/direct-diagnostic-capabilities.js:168>) — run_command가 취소 신호를 소비하지 않고 실행을 시작한다.
- [lmstudio-unity-mcp/src/server.js:93](<C:/Users/sster/Documents/Git/UE5_Local_LLM_MCP_lmstudio/lmstudio-unity-mcp/src/server.js:93>) — Unity MCP handler 역시 extra를 받지 않는다.
- [lmstudio-unreal-agent-mcp/src/direct-server.js:72](<C:/Users/sster/Documents/Git/UE5_Local_LLM_MCP_lmstudio/lmstudio-unreal-agent-mcp/src/direct-server.js:72>) — Direct runtime 반환 API에 close가 없다.
- [lmstudio-unreal-agent-mcp/src/strict-server.js:152](<C:/Users/sster/Documents/Git/UE5_Local_LLM_MCP_lmstudio/lmstudio-unreal-agent-mcp/src/strict-server.js:152>) — Strict close는 session orphan을 처리하며 child 회수에 연결되지 않는다.
- [lmstudio-unity-mcp/src/symbols.js:25](<C:/Users/sster/Documents/Git/UE5_Local_LLM_MCP_lmstudio/lmstudio-unity-mcp/src/symbols.js:25>) — symbol worker는 지역 child이며 runtime close에서 회수할 API가 없다.
- [artifacts/architecture-audit-20261003/behavior-results.json](<C:/Users/sster/Documents/Git/UE5_Local_LLM_MCP_lmstudio/artifacts/architecture-audit-20261003/behavior-results.json>) — 이미 aborted인 context를 넣은 fake run_command에서도 spawn=1, kill=0이며 성공 결과가 나왔다. 공개 MCP 진입점에서는 signal 자체가 전달되지 않는다.

**반대 근거·한계:** 개별 도구의 timeout 및 Unity operation cancel은 존재한다. 이것이 MCP 요청 취소의 전달을 대신하지는 않는다. ([unity-editor-bridge/Editor/Operations.cs:68](<C:/Users/sster/Documents/Git/UE5_Local_LLM_MCP_lmstudio/unity-editor-bridge/Editor/Operations.cs:68>)) 실제 LM Studio Stop이 어떤 MCP notification을 보내는지 이번 턴에서 wire trace를 수집하지 않았다. Unity의 모든 하위 worker 취소를 실행 재현한 것은 아니다.

**최소 수정:** request context의 signal을 기존 capability와 runner로 전달하고 predispatch abort는 spawn=0으로 처리한다. 실행 뒤에는 취소 요청·실제 종료·적용 결과를 구분한다. runtime close에서 소유한 자원도 정리한다.

**검증:** preabort, 실행 중 abort, disconnect, timeout+abort 경쟁, CAS commit 도중 취소에서 거짓 not_applied가 없는지 확인한다.

### A11 — 텍스트 검색의 IO 누락이 완전한 검색·일치 없음으로 해석될 수 있다

**P2 · Bug · TestVerified**

유일한 .cpp 읽기에 EACCES를 주입해도 ok=true, results=[], filesScanned=1, truncated=false가 반환됐다.

**근거 경로**

- [lmstudio-unreal-agent-mcp/src/direct-read-capabilities.js:138](<C:/Users/sster/Documents/Git/UE5_Local_LLM_MCP_lmstudio/lmstudio-unreal-agent-mcp/src/direct-read-capabilities.js:138>) — 파일 read 예외를 catch 후 continue하며 누락 이유를 결과에 싣지 않는다.
- [lmstudio-unreal-agent-mcp/src/direct-read-capabilities.js:158](<C:/Users/sster/Documents/Git/UE5_Local_LLM_MCP_lmstudio/lmstudio-unreal-agent-mcp/src/direct-read-capabilities.js:158>) — filesScanned는 수집한 후보 수이고 성공적으로 읽은 수와 다르다.
- [lmstudio-context-compactor-plugin/src/compaction-tool-memory.js:425](<C:/Users/sster/Documents/Git/UE5_Local_LLM_MCP_lmstudio/lmstudio-context-compactor-plugin/src/compaction-tool-memory.js:425>) — maxFilesReached/truncated=false로 complete_for_requested_scope를 만들 수 있다.
- [artifacts/architecture-audit-20261003/behavior-results.json](<C:/Users/sster/Documents/Git/UE5_Local_LLM_MCP_lmstudio/artifacts/architecture-audit-20261003/behavior-results.json>) — 유일한 .cpp 읽기에 EACCES를 주입해도 ok=true, results=[], filesScanned=1, truncated=false가 반환됐다.

**반대 근거·한계:** 텍스트 확장자·크기·binary 제외 자체는 도구의 의도된 검색 범위일 수 있다. IO 실패에 의한 미관찰과 구분해야 한다. ([lmstudio-unreal-agent-mcp/src/direct-read-capabilities.js:132](<C:/Users/sster/Documents/Git/UE5_Local_LLM_MCP_lmstudio/lmstudio-unreal-agent-mcp/src/direct-read-capabilities.js:132>)) 최종 모델이 반드시 없는 파일이라고 말한다는 의미는 아니다. 잘못된 completeness 근거를 생성하는 경로가 확인됐다.

**최소 수정:** 검색 생산자가 attempted/read/skipped/errors와 coverage를 소유하고 consumer는 명시된 coverage만 사용한다. trace 제한과 읽기 성공을 하나의 truncated flag로 대체하지 않는다.

**검증:** 읽기 거부/중간 삭제/검색 cap/정상 0건을 구분하고 requested scope 밖 결론이 나오지 않는지 검사한다.

### A12 — RAG 반복 억제 receipt가 바뀐 검색 조건과 긴 query를 같은 요청으로 취급한다

**P2 · Bug · TestVerified**

unreal_cpp rows의 receipt 뒤 source를 ue_api_reference로 제한해도 duplicate=true였다. 앞 512자가 같은 다른 query도 key가 같았다.

**근거 경로**

- [scripts/direct_rag_history.py:125](<C:/Users/sster/Documents/Git/UE5_Local_LLM_MCP_lmstudio/scripts/direct_rag_history.py:125>) — query key에서 source/layer/doc_type/genre/extension/required_term/use_active_project가 빠지고 query는 512자로 잘린다.
- [scripts/direct_rag_search.py:177](<C:/Users/sster/Documents/Git/UE5_Local_LLM_MCP_lmstudio/scripts/direct_rag_search.py:177>) — preflight suppressed면 실제 retrieval 전에 no_new_information을 반환한다.
- [scripts/direct_rag_selection.py:149](<C:/Users/sster/Documents/Git/UE5_Local_LLM_MCP_lmstudio/scripts/direct_rag_selection.py:149>) — 누락된 필터들은 실제 SearchOptions에는 영향을 준다.
- [artifacts/architecture-audit-20261003/rag-package-reproduce.results.json](<C:/Users/sster/Documents/Git/UE5_Local_LLM_MCP_lmstudio/artifacts/architecture-audit-20261003/rag-package-reproduce.results.json>) — unreal_cpp rows의 receipt 뒤 source를 ue_api_reference로 제한해도 duplicate=true였다. 앞 512자가 같은 다른 query도 key가 같았다.

**반대 근거·한계:** receipt가 없으면 재검색하며 index/detail 변경은 기존 key에서 구분한다. ([scripts/direct_rag_search.py:202](<C:/Users/sster/Documents/Git/UE5_Local_LLM_MCP_lmstudio/scripts/direct_rag_search.py:202>)) key는 index fingerprint와 detail 등 일부 변경을 구분한다. ([scripts/direct_rag_history.py:136](<C:/Users/sster/Documents/Git/UE5_Local_LLM_MCP_lmstudio/scripts/direct_rag_history.py:136>)) 첨부 라이브 로그의 개별 symbol 실패가 이 receipt 분기였는지는 확인하지 않았다.

**최소 수정:** 기존 normalized effective selection을 key 입력으로 사용하고 전체 bounded query를 hash한다. preflight와 최종 delivery가 같은 계약을 사용해야 한다.

**검증:** 각 semantic filter 변경, 512자 이후 변경, effective scope, 동일 query 재전송, index generation 변경을 비교한다.

### A13 — RAG generation 게시 후 journal 정리 실패를 미게시·롤백 시도로 보고한다

**P2 · Bug · TestVerified**

최종 journal unlink만 실패시켜 liveIndex=new, journalState=committed인데 ok=false/stageCommitted=false가 반환되는 것을 재현했다.

**근거 경로**

- [scripts/direct_rag_generation_swap.py:97](<C:/Users/sster/Documents/Git/UE5_Local_LLM_MCP_lmstudio/scripts/direct_rag_generation_swap.py:97>) — 게시 후 committed를 기록한다.
- [scripts/direct_rag_generation_swap.py:138](<C:/Users/sster/Documents/Git/UE5_Local_LLM_MCP_lmstudio/scripts/direct_rag_generation_swap.py:138>) — finally의 journal 정리 예외가 밖으로 전파된다.
- [scripts/direct_rag_project_refresh.py:166](<C:/Users/sster/Documents/Git/UE5_Local_LLM_MCP_lmstudio/scripts/direct_rag_project_refresh.py:166>) — 모든 commit 예외를 stageCommitted=false와 rollback attempted로 반환한다.
- [artifacts/architecture-audit-20261003/rag-package-reproduce.results.json](<C:/Users/sster/Documents/Git/UE5_Local_LLM_MCP_lmstudio/artifacts/architecture-audit-20261003/rag-package-reproduce.results.json>) — 최종 journal unlink만 실패시켜 liveIndex=new, journalState=committed인데 ok=false/stageCommitted=false가 반환되는 것을 재현했다.

**반대 근거·한계:** 새 generation은 온전했고 committed journal이 남아 이후 recovery로 정리할 수 있다. ([scripts/direct_rag_generation_swap.py:97](<C:/Users/sster/Documents/Git/UE5_Local_LLM_MCP_lmstudio/scripts/direct_rag_generation_swap.py:97>)) 기존 backup 정리 실패 테스트에는 recovery 경로가 있다. ([tests/test_rag_refresh.py:1120](<C:/Users/sster/Documents/Git/UE5_Local_LLM_MCP_lmstudio/tests/test_rag_refresh.py:1120>)) 자료 손상이나 partial generation 노출을 입증한 사례가 아니다. public build/installer 영향은 호출부 소스로 확인했다.

**최소 수정:** 기존 generation owner가 commit outcome과 cleanup warning을 분리하고 refresh/public build/installer가 실제 committed 상태를 유지하게 한다.

**검증:** commit 전 실패·rollback 실패·commit 후 cleanup 실패·다음 시작 recovery별 결과와 디스크 상태를 대조한다.

### A14 — 다른 프로젝트 refresh가 전역 DB 시각을 갱신하면 오래된 프로젝트 근거도 fresh로 판정된다

**P2 · Bug · TestVerified**

A의 rows는 Value=1, 실제 파일은 Value=2이고 DB mtime만 더 최신인 fixture에서 source/symbol/architectureFresh 모두 true였다. 복수 selector에서 첫 프로젝트만 검사하는 추가 경로도 소스로 확인했다.

**근거 경로**

- [scripts/direct_rag_project_merge.py:78](<C:/Users/sster/Documents/Git/UE5_Local_LLM_MCP_lmstudio/scripts/direct_rag_project_merge.py:78>) — 선택하지 않은 프로젝트 rows는 유지한다.
- [scripts/direct_rag_freshness.py:136](<C:/Users/sster/Documents/Git/UE5_Local_LLM_MCP_lmstudio/scripts/direct_rag_freshness.py:136>) — 프로젝트 소스 시각을 해당 수집 시각 대신 전역 SQLite mtime과 비교한다.
- [scripts/direct_rag_retrieval.py:131](<C:/Users/sster/Documents/Git/UE5_Local_LLM_MCP_lmstudio/scripts/direct_rag_retrieval.py:131>) — freshness 결과가 stale suppression을 결정한다.
- [scripts/direct_rag_freshness.py:38](<C:/Users/sster/Documents/Git/UE5_Local_LLM_MCP_lmstudio/scripts/direct_rag_freshness.py:38>) — 복수 selector 중 첫 번째 해석 가능한 프로젝트만 freshness 대상으로 선택한다. 이 추가 경로는 소스로 확인했다.
- [artifacts/architecture-audit-20261003/rag-package-reproduce.results.json](<C:/Users/sster/Documents/Git/UE5_Local_LLM_MCP_lmstudio/artifacts/architecture-audit-20261003/rag-package-reproduce.results.json>) — A의 rows는 Value=1, 실제 파일은 Value=2이고 DB mtime만 더 최신인 fixture에서 source/symbol/architectureFresh 모두 true였다. 복수 selector에서 첫 프로젝트만 검사하는 추가 경로도 소스로 확인했다.

**반대 근거·한계:** 소스가 전역 DB보다 새로우면 stale 검사가 작동한다. exact project root와 generation 일관성 검사는 별도로 존재한다. ([scripts/direct_rag_freshness.py:141](<C:/Users/sster/Documents/Git/UE5_Local_LLM_MCP_lmstudio/scripts/direct_rag_freshness.py:141>)) 복수 selector 혼합 사례는 SourceVerified이며 별도 실행 재현하지 않았다.

**최소 수정:** collector→merge→build가 exact project별 수집 provenance를 보존하고 freshness는 그 자료를 사용한다. 구형 index의 provenance 부재는 unknown, 복수 프로젝트는 각각 판정한다.

**검증:** A수집→A수정→Brefresh, source/symbol/architecture tier, clone project, 복수 selector, 구형 index와 부분 upgrade를 검증한다.

### A15 — 패키지 최종 게시 실패가 기존 정상 output과 새 staging을 모두 없앤다

**P2 · Bug · TestVerified**

임시 fixture에서 최종 rename만 실패시킨 결과 prior_package_exists=false, output_exists=false, remaining_staging=[]였다.

**근거 경로**

- [scripts/build_integrated_package.py:1219](<C:/Users/sster/Documents/Git/UE5_Local_LLM_MCP_lmstudio/scripts/build_integrated_package.py:1219>) — 기존 output을 먼저 삭제한다.
- [scripts/build_integrated_package.py:1221](<C:/Users/sster/Documents/Git/UE5_Local_LLM_MCP_lmstudio/scripts/build_integrated_package.py:1221>) — 그 뒤 staging.replace로 새 output을 게시한다.
- [scripts/build_integrated_package.py:1224](<C:/Users/sster/Documents/Git/UE5_Local_LLM_MCP_lmstudio/scripts/build_integrated_package.py:1224>) — 게시 예외에서 staging도 삭제한다.
- [artifacts/architecture-audit-20261003/rag-package-reproduce.results.json](<C:/Users/sster/Documents/Git/UE5_Local_LLM_MCP_lmstudio/artifacts/architecture-audit-20261003/rag-package-reproduce.results.json>) — 임시 fixture에서 최종 rename만 실패시킨 결과 prior_package_exists=false, output_exists=false, remaining_staging=[]였다.

**반대 근거·한계:** required/private-path 검증은 교체 전에 끝나며 zip 출력은 별도 atomic replace를 사용한다. ([scripts/build_integrated_package.py:1152](<C:/Users/sster/Documents/Git/UE5_Local_LLM_MCP_lmstudio/scripts/build_integrated_package.py:1152>)) 새 staging의 required/private path 검증은 기존 output 교체 전에 수행한다. ([scripts/build_integrated_package.py:1181](<C:/Users/sster/Documents/Git/UE5_Local_LLM_MCP_lmstudio/scripts/build_integrated_package.py:1181>)) 실제 사용자의 배포 패키지에는 실행하지 않았다. 원본 저장소나 배포 서버 자료 손실을 뜻하지 않는다.

**최소 수정:** 기존 builder가 같은 부모의 backup→publish→성공 cleanup/실패 restore를 관리한다. rollback 실패면 복구 경로를 보존한다.

**검증:** rename 실패, restore 실패, backup cleanup 실패, 기존 output 없는 첫 생성, 기존 zip 보존을 확인한다.

### A16 — Unity Bridge 응답 장식이 저장된 증거의 원래 session/domain identity를 현재 값으로 덮는다

**P2 · Bug · SourceVerified**

operation 재조회뿐 아니라 snapshot/reference 조회에서도 원래 증거 origin과 응답 연결 identity가 섞인다.

**근거 경로**

- [unity-editor-bridge/Editor/Operations.cs:87](<C:/Users/sster/Documents/Git/UE5_Local_LLM_MCP_lmstudio/unity-editor-bridge/Editor/Operations.cs:87>) — operation journal은 실행 origin 없이 상태와 결과를 저장한다.
- [unity-editor-bridge/Editor/ReferenceIndex.cs:21](<C:/Users/sster/Documents/Git/UE5_Local_LLM_MCP_lmstudio/unity-editor-bridge/Editor/ReferenceIndex.cs:21>) — 저장된 reference 결과는 원래 editorSessionId/domainGeneration을 반환한다.
- [unity-editor-bridge/Editor/Bridge.cs:158](<C:/Users/sster/Documents/Git/UE5_Local_LLM_MCP_lmstudio/unity-editor-bridge/Editor/Bridge.cs:158>) — Pump가 모든 결과의 session/domain/observedAt을 현재 값으로 덮는다.

**반대 근거·한계:** mutation replay는 session을 포함한 digest로 차단되고 reference의 immutable_collection_not_live 표시는 남는다. ([unity-editor-bridge/Editor/Operations.cs:78](<C:/Users/sster/Documents/Git/UE5_Local_LLM_MCP_lmstudio/unity-editor-bridge/Editor/Operations.cs:78>)) immutable_collection_not_live freshness 표시는 보존된다. ([unity-editor-bridge/Editor/ReferenceIndex.cs:22](<C:/Users/sster/Documents/Git/UE5_Local_LLM_MCP_lmstudio/unity-editor-bridge/Editor/ReferenceIndex.cs:22>)) 실제 Unity Editor 재시작/domain reload를 이번 턴에서 실행하지 않았다. 모든 역사 데이터를 live로 단정한다고 확대하지 않는다.

**최소 수정:** Operations/EvidenceStore는 execution·observation origin을 보존하고 Bridge는 delivery session/time을 별도 의미로 표시한다. BridgeClient가 현재 응답 identity를 검증하므로 기존 최상위 session 필드만 제거하지 말고 producer/client 계약을 함께 바꾼다. 원장과 ID owner는 유지한다.

**검증:** 동일 session 재조회, domain reload, Editor restart, snapshot/reference query, 오래된 원장 호환에서 origin이 바뀌지 않는지 확인한다.

### A17 — Unreal 파일 commit 뒤 receipt 관찰 실패가 실제 적용 결과를 일반 오류로 덮는다

**P2 · Bug · TestVerified**

fake create가 한 번 성공한 뒤 stat에 EACCES를 주면 적용 정보 없이 INTERNAL_ERROR가 반환됐다. replace와 bundle에도 같은 후처리 구조가 있다.

**근거 경로**

- [lmstudio-unreal-agent-mcp/src/direct-file-mutation-capabilities.js:175](<C:/Users/sster/Documents/Git/UE5_Local_LLM_MCP_lmstudio/lmstudio-unreal-agent-mcp/src/direct-file-mutation-capabilities.js:175>) — 락 안에서 createExclusive commit 후 락을 해제한다.
- [lmstudio-unreal-agent-mcp/src/direct-file-mutation-capabilities.js:191](<C:/Users/sster/Documents/Git/UE5_Local_LLM_MCP_lmstudio/lmstudio-unreal-agent-mcp/src/direct-file-mutation-capabilities.js:191>) — commit 뒤 registerCurrentVersion을 호출한다.
- [lmstudio-unreal-agent-mcp/src/direct-file-version-policy.js:65](<C:/Users/sster/Documents/Git/UE5_Local_LLM_MCP_lmstudio/lmstudio-unreal-agent-mcp/src/direct-file-version-policy.js:65>) — 후속 stat/register 예외가 direct-server의 일반 INTERNAL_ERROR로 전파된다.
- [artifacts/architecture-audit-20261003/behavior-results.json](<C:/Users/sster/Documents/Git/UE5_Local_LLM_MCP_lmstudio/artifacts/architecture-audit-20261003/behavior-results.json>) — fake create가 한 번 성공한 뒤 stat에 EACCES를 주면 적용 정보 없이 INTERNAL_ERROR가 반환됐다. replace와 bundle에도 같은 후처리 구조가 있다.

**반대 근거·한계:** optional change evidence 생성 실패는 현재 코드에서 이미 commit 성공을 보존한다. 아직 남은 문제는 receipt 후처리이다. ([lmstudio-unreal-agent-mcp/src/direct-change-evidence.js:33](<C:/Users/sster/Documents/Git/UE5_Local_LLM_MCP_lmstudio/lmstudio-unreal-agent-mcp/src/direct-change-evidence.js:33>)) 실제 사용자 파일을 변경한 재현은 아니다. 동시 외부 변경이면 현재 파일 hash는 commit 당시 hash와 다를 수 있다.

**최소 수정:** commit 사실과 현재 version receipt 확보를 별도로 반환한다. receipt가 없으면 후속 mutation은 재읽기를 요구하되 이미 적용된 변경을 미적용으로 돌리지 않는다.

**검증:** create/replace/bundle의 commit 후 stat/register 실패, 외부 변경, receipt unavailable 후 재읽기를 검증한다.

### A18 — crash recovery가 같은 프로젝트 안에서 바뀐 canonical target을 원래 파일로 취급한다

**P1 · Bug · TestVerified — 실제 Windows 임시 junction으로 정정·확인**

실제 Windows 임시 junction에서 프로젝트 밖 경로는 기존 코드가 차단했다. 같은 프로젝트 안 다른 폴더로 부모를 바꾸고 postHash를 같게 하면 locksHeld=false/true 모두 다른 파일이 pre-image로 덮였다. 수정 후 네 경계 회귀가 모두 통과했다.

**이전 감사의 정정:** absolutePathIsWithin은 canonicalAbsolutePathIdentity를 통해 실제 realpath를 사용하므로 lexical-only라는 이전 설명은 틀렸다. 프로젝트 밖으로의 junction 탈출과 서로 다른 hash는 기존 코드에서도 차단된다. 이전 fake-fs 재현은 하위 filesystem-path-identity의 실제 fs까지 대체하지 않아 외부 탈출의 근거로 무효화한다. 실제 사용자 프로젝트·강제 프로세스 crash·모든 OS 조합은 검증하지 않았다.

**근거 경로**

- [filesystem-path-identity.js:77](<C:/Users/sster/Documents/Git/UE5_Local_LLM_MCP_lmstudio/lmstudio-unreal-agent-mcp/src/filesystem-path-identity.js:77>) — 현재 물리 containment를 검사하는 기존 반대 근거.
- [direct-transaction-recovery.js:29](<C:/Users/sster/Documents/Git/UE5_Local_LLM_MCP_lmstudio/lmstudio-unreal-agent-mcp/src/direct-transaction-recovery.js:29>) — frozen target 동등성 및 락 안/쓰기 직전 재검증을 보완한 기존 복구 소유자.
- [architecture-contract-regression.test.js:29](<C:/Users/sster/Documents/Git/UE5_Local_LLM_MCP_lmstudio/lmstudio-unreal-agent-mcp/test/architecture-contract-regression.test.js:29>) — 실제 임시 디렉터리와 junction으로 두 복구 분기를 확인한다.
- [unreal-a18-os-results.json](<C:/Users/sster/Documents/Git/UE5_Local_LLM_MCP_lmstudio/artifacts/architecture-audit-20261003/unreal-a18-os-results.json>) — 수정 전 재현과 수정 후 결과.
- [unreal-regression.log](<C:/Users/sster/Documents/Git/UE5_Local_LLM_MCP_lmstudio/artifacts/architecture-audit-20261003/unreal-regression.log>) — 전체 Unreal Node 회귀 실행 결과.

**최소 수정:** 기존 recovery owner에서 journal에 동결된 canonicalAbsolutePath와 현재 realpath의 동등성을 확인한다. 락 획득 뒤와 쓰기 직전에도 재검증하고 불일치는 rollback_incomplete/recoveryRequired로 남긴다. 기존 project/backup containment와 post-image hash 검사는 유지한다.

**검증:** 검증된 임시 루트에서 프로젝트 안/밖 부모 junction 교체와 locksHeld 양쪽 4개 테스트를 수행했다. 실제 파일과 journal을 사용했으며 사용자 프로젝트는 건드리지 않았다.

### A19 — Python RAG refresh와 synchronous stdio의 취소·시간 계약은 추가 실행 확인이 필요하다

**P2 · NeedsRuntimeProof · SourceVerified**

RAG local engine lookup의 bounded deadline을 refresh 전체의 deadline으로 볼 근거가 없다.

**근거 경로**

- [scripts/direct_rag_server.py:102](<C:/Users/sster/Documents/Git/UE5_Local_LLM_MCP_lmstudio/scripts/direct_rag_server.py:102>) — id 없는 notification은 반환하며 synchronous dispatch에 취소 token 경로가 없다.
- [scripts/direct_rag_project_collection.py:15](<C:/Users/sster/Documents/Git/UE5_Local_LLM_MCP_lmstudio/scripts/direct_rag_project_collection.py:15>) — collector subprocess 호출에 timeout이 없다.
- [scripts/direct_rag_freshness.py:72](<C:/Users/sster/Documents/Git/UE5_Local_LLM_MCP_lmstudio/scripts/direct_rag_freshness.py:72>) — freshness는 Source/Plugins 순회를 수행한다.

**반대 근거·한계:** per-index lock, generation journal/recovery, 별도 local engine helper의 제한은 존재한다. ([scripts/direct_rag_project_refresh.py:34](<C:/Users/sster/Documents/Git/UE5_Local_LLM_MCP_lmstudio/scripts/direct_rag_project_refresh.py:34>)) 실제 hang, 순회 시간, 사용자 Stop의 notification과 허용 timeout 요구를 측정하지 않았다.

**최소 수정:** 장기 작업에 대한 취소·시간·commit 경계를 먼저 명세하고 기존 dispatcher/collector가 job 수명을 소유하게 한다. commit 후 취소로 미게시를 보고하지 않는다.

**검증:** 작은 fake collector로 prestart/running/commit 이후 취소, child exit 지연, 중복 refresh를 관찰한 뒤 필요한 범위만 구현한다.

### A20 — 설치 시 ON 여부와 Auto 선택 문서가 현재 동작 설명과 일치하지 않는다

**P3 · Bug · SourceVerified**

설정 수명과 Auto 동작을 문서만으로 일관되게 해석하기 어렵다.

**근거 경로**

- [docs/ARCHITECTURE.md:9](<C:/Users/sster/Documents/Git/UE5_Local_LLM_MCP_lmstudio/docs/ARCHITECTURE.md:9>) — 설치·업데이트 시 기존 채팅 기본 ON으로 설명한다.
- [docs/ARCHITECTURE.md:34](<C:/Users/sster/Documents/Git/UE5_Local_LLM_MCP_lmstudio/docs/ARCHITECTURE.md:34>) — 같은 문서가 설치만으로 켜지지 않고 사용자가 켤 때만 동작한다고 설명한다.
- [docs/model-guidance/README.md:46](<C:/Users/sster/Documents/Git/UE5_Local_LLM_MCP_lmstudio/docs/model-guidance/README.md:46>) — GUI 선택 설명에 구현된 Auto가 빠져 있다.

**반대 근거·한계:** plugin README와 현재 설정/선택 구현에는 Auto 설명이 있다. ([lmstudio-context-compactor-plugin/README.md:32](<C:/Users/sster/Documents/Git/UE5_Local_LLM_MCP_lmstudio/lmstudio-context-compactor-plugin/README.md:32>)) 현재 설치된 GUI 설정을 이번 코드 감사에서 변경하거나 재조회하지 않았다.

**최소 수정:** 현재 installer/config 소유자의 동작을 기준으로 기존 문서를 수정하고 generated guidance는 원본 catalogue 경로에서만 갱신한다.

**검증:** GUI enum, 기본값, installer 기존 채팅 처리, Off/Auto 설명을 대조한다.

## 4. SSOT·SOLID 관점의 판단

| 원칙 | 실제 문제 | 적용할 변경 |
|---|---|---|
| SSOT | note의 현재값 2개, pairing 3곳 이상, 필터와 receipt key의 의미 차이 | authoritative state·순수 matcher·normalized selection 각각 한 소유자로 통일 |
| SRP | 실행 완료를 receipt/serialization/cleanup 예외가 뒤집음 | 실행 outcome과 후처리 outcome을 분리; 동일 owner가 각각의 결과를 보존 |
| OCP | 새 response field/도구가 추가되면 요약·reference·memory 분기가 따로 늘어남 | 최소 observation/projection 계약과 producer adapter 재사용; 범용 플러그인 체계까지 확대하지 않음 |
| LSP | applied 결과가 not_applied가 되거나 IO 누락이 complete가 되는 대체 불가능한 의미 | 모든 adapter에서 같은 상태의 의미를 지키는 계약 테스트 |
| ISP | consumer가 거대한 raw JSON의 앞부분과 암묵 필드를 해석 | identity/origin/coverage/execution outcome 등 소비 목적별 작은 view 제공 |
| DIP | 취소·시간 제어가 실제 runner까지 도달하지 않음 | 기존 request context와 runner seam으로 signal/clock/termination을 전달 |

SOLID의 각 글자마다 새 클래스나 인터페이스를 만들 필요는 없다. Node/Python/C# 사이에 구현 하나를 억지로 공유할 수도 없다. 이 경계는 **version이 있는 데이터 계약과 동일 fixture**로 맞춘다. 같은 프로세스의 순수 해석 함수는 실제로 재사용한다.

### 작은 계약에서 구분할 사실

- **Execution:** 시작 전 거부 / 실행 중 / 적용 / 부분 적용 / 결과 미상. 원래 operation·transaction ID 보존.
- **Delivery:** 본문 완전/축약/전달 실패. 실행 결과를 덮지 않음.
- **Observation:** 정확한 project/path, version/hash, 원래 session/domain/generation, 관찰 시각과 범위.
- **Coverage:** 완전/부분/불명, 실제 읽은 수·누락 이유. 결과 0개와 완전성은 별도.
- **Cleanup:** 정리 완료/보류, 복구 가능한 artifact 위치. commit 여부와 별도.

위 항목은 개념 구분이다. 모든 응답을 대형 공통 schema로 바꾸자는 제안은 아니다. 먼저 해당 producer의 현재 필드를 재사용하고 필요한 필드만 추가한다.

## 5. 수명별 구현 계획

### 1단계 — 실행 사실과 종료 보장

대상: A08/A09/A10/A13/A17. A18은 실제 임시 junction 재현으로 같은 프로젝트 내 target rebinding을 확인했고 동일 단계에서 identity 경계를 보완했다.

1. 공개 MCP handler → request context → capability → runner의 signal 전달을 완성한다.
2. predispatch 취소는 실행하지 않고, 실행 뒤 취소는 실제 종료·변경 결과를 보존한다.
3. timeout/abort/close 경합을 기존 runner에서 한 번만 정산한다. 종료 함수 자체가 응답 deadline을 무기한 붙잡지 못하게 한다.
4. Unity applied, Unreal committed, RAG published를 응답 축약·receipt·cleanup 실패로 바꾸지 않는다.
5. direct/strict/shared 호출부와 모든 기존 결과 소비자가 새로운 보조 상태를 받아도 기존 성공 여부를 오해하지 않게 한다.
6. 연결 종료도 기존 runtime에서 child 소유자에게 전달하고 종료 확인·미확인을 구분한다. Unity 동기 편집을 강제 중단하는 정책으로 확대하지 않는다.

완료 조건: 결과 상태와 실제 fixture side effect가 일치하고, 실행 시작 전 취소는 spawn=0, 종료가 확인되지 않은 상태는 명시적으로 남는다.

### 2단계 — 기억·근거 해석의 일관성

대상: A01/A02/A03/A04/A11.

1. note 교체·clear·복구를 하나의 전이로 만든다. storage fallback도 objective/project/attachment 조건이 동일해야 한다.
2. canonical pairing helper를 추출하고 모든 소비자를 교체한다. 도구 데이터의 신뢰도나 역할은 올리지 않는다.
3. 실패 읽기는 현재 근거의 신선도를 내린다. 역사적으로 적용된 변경 사실은 따로 유지한다.
4. semantic summary도 기존 body projection 규칙을 사용한다. metadata와 본문을 예산에 맞게 보존한다.
5. 검색 producer의 coverage를 memory가 그대로 사용한다. 읽지 못한 것을 없는 것으로 요약하지 않는다.

완료 조건: 동일 기록의 matching·identity·freshness가 consumer마다 달라지지 않고 note clear가 restart/압축 후에도 유지된다.

### 3단계 — 프로젝트·엔진·증거 origin

대상: A07/A12/A14/A16.

1. Unity root 설정 파일을 정확히 식별한다. 하위 sample의 설정은 일반 파일 관찰로만 남긴다.
2. RAG receipt key를 실제 effective query와 일치시킨다. 구형 receipt는 보수적으로 재검색한다.
3. exact project별 수집 provenance를 collector/merge/build에 넣고 retention된 rows의 기준을 유지한다.
4. Unity stored result에 원래 origin을 보존하고 delivery metadata를 분리한다.

완료 조건: A 프로젝트 변경 후 B만 refresh해도 A가 fresh로 승격되지 않으며, 저장 증거를 재조회해도 원래 session/domain은 바뀌지 않는다.

### 4단계 — 보조 진입점·저장·게시 수명

대상: A05/A06/A15/A19/A20.

1. package output 교체에 backup/restore를 추가하고 실패 시 복구 자료를 보존한다.
2. headless가 현재 core의 checkpoint 역할과 current-turn 정책을 따르게 한다. GUI 예산 관리자를 복제하지 않는다.
3. window/archive/scope 보존 정책을 정의하고 활성 참조를 보호하는 cleanup을 기존 저장소 수명에 연결한다.
4. RAG refresh의 실제 시간·취소 경로를 관찰해 필요한 dispatcher/collector 변경 범위를 확정한다.
5. 기존 문서의 기본값·Auto·설치 수명 설명을 현재 구현에 맞춘다.

완료 조건: 보조 경로도 실행·증거 계약을 지키고 실패한 게시가 직전 정상 결과를 없애지 않는다.

### 저장 형식과 부분 적용 복구

| 대상 | 호환·복구 원칙 |
|---|---|
| note/window | version과 scope 확인. clear를 persistence에서도 표현하며 구형 모호한 판단은 복구하지 않음 |
| RAG receipt | key 의미 변경 시 기존 receipt를 새 검색의 억제 근거로 사용하지 않음 |
| RAG index provenance | provenance 없는 기존 rows는 unknown. 전체 DB mtime으로 fresh를 만들어 주지 않음 |
| Unity journal/snapshot | 구형 origin이 없으면 unknown. 현재 session 값을 과거 origin으로 채워 넣지 않음 |
| mutation 응답 | 기존 applied/ok/ID는 유지하고 receipt/cleanup/delivery 보조 상태를 추가. 후속 수정의 version 검증은 약화하지 않음 |
| package output | 새 게시 실패 시 이전 output 복원. 복원 실패 시 backup/staging 위치와 상태를 남김 |

각 단계는 독립된 변경 묶음으로 진행한다. 데이터 형식 변경 producer와 consumer를 한 묶음으로 바꾸고, 기존 저장 데이터 fixture를 포함한다. 새 자료를 쓴 후 구버전으로 돌아갈 때의 unknown/ignore 동작도 확인한다.

## 6. 유지할 구조와 반대 근거

- Auto의 Off/Observe-only/allowance=0은 intent·reference snapshot·추가 reference 측정 경로를 건너뛴다.
- Auto는 별도의 모델/도구 호출을 만들지 않는다. 기존 Hybrid semantic handoff의 조건부 요약 호출은 별개이다.
- optional guidance는 기존 BudgetBroker 예산 안에서 공급된다. 채택 실패 시 기본 입력을 유지하는 경로가 있다.
- reference는 ephemeral이며 untrusted 파일 값은 별도 assistant data로 취급한다. 지침으로 승격하여 영구 보관하는 설계가 아니다.
- primary pack 불가 시 supplement만 독립적으로 넣는 기존 문제는 현재 코드에서 보정됐다.
- guidance catalogue/Markdown과 generated bundle의 일치 경로가 있다. 생성 파일을 별도 원본으로 관리할 이유가 없다.
- 파일 CAS/version receipt, project containment, transaction journal, RAG generation consistency가 존재한다. 위 결함은 그 보호장치 사이의 누락이지 보호장치 전체 부재가 아니다.
- DeliveryController의 출력 종료와 objectiveSatisfied 판단은 분리되어 있다. 모델이 완료라고 말한 것을 도구 적용 사실로 승격하면 안 된다.
- compactor 최상위 테스트 24개 중 npm 명령에 직접 열거된 것은 20개지만 나머지 4개는 suite 내부 require로 연결되어 있다. 단순 whitelist 누락으로 보고하지 않았다.
- portable required runtime 422개 중 scripts Python 170개를 AST로 대조한 local import 누락은 없었다. 이는 패키지 포함 관계 확인이며 설치·실행 성공의 증명은 아니다.

## 7. 검토 범위와 미검증

| 영역 | 확인 범위 | 남은 한계 |
|---|---|---|
| compactor | prediction round, tool boundary, recovery, budget, attachment, continuity, evidence/window/note, summary, Auto/reference | 실제 최종 LM Studio SDK 입력·live cancel wire trace 없음 |
| Unreal MCP | Direct 조립/공개 handler, read/search, file mutation, receipt, bundle, transaction recovery, diagnostic process; 관련 Strict lifecycle | 실제 엔진 빌드·OS 프로세스 kill·강제 crash 미실행; 임시 junction 및 수동 interrupted journal은 실행 |
| Unity | Node binding/adapter, Bridge dispatch, operation journal, snapshot/reference origin, symbol worker/index 경계 | Editor 실행, domain reload, PlayMode/TestRunner, .NET 설치 미실행 |
| RAG | entry/schema/dispatch, selection, history, freshness, generation lock/stage/swap/recovery, engine local 연결 | 대형 corpus 성능, 강제 crash, 실제 동시 reader/refresh 미실행 |
| installer/package | bootstrap, binding, staged build, managed rollback, required inventory/import closure, output/zip publication | 실제 설치·release archive 생성·launcher 실행 미실행 |
| headless | Python caller와 JS compaction adapter | 실제 모델 서버 요청·응답 없음 |
| 문서/CI | 현재 architecture/guidance/config 설명, suite 연결 관계 | 전체 CI와 모든 기존 테스트 재실행은 이번 읽기 중심 감사 범위에 포함하지 않음 |

성능·보안·모든 게임 API의 전면 인증은 이 보고서의 결론이 아니다. 오래된 호환 코드와 각 capability 내부의 모든 엔진별 분기를 line-by-line 확인했다고 주장하지 않는다. 이번 범위는 활성 주요 경계, 관련 호출부·반대 경로, 아래 재현이다.

### 추가 조건부 관찰

Unity reference에서 같은 session/domain의 상충하는 editorVersion 결과가 한 batch에 있으면 마지막 값이 선택된다. 정상 producer가 같은 domain에서 버전을 바꾸지 않는다면 도달하지 않는 입력이다. 확정 결함 목록에 별도 추가하지 않았고, origin 보정(A16)과 함께 consistency fixture로 다루면 된다.

Unity operation journal의 1,000개 상한과 자동 eviction 금지는 at-most-one-attempt 계약을 위한 의도된 제한이다. 장기 사용을 위한 명시적 보관·폐기 흐름은 별도로 설계할 여지가 있다. 오래된 ID를 자동으로 지워 재실행 가능하게 하는 해결은 적합하지 않다.

`shared-tool-core`가 Unreal 패키지의 snapshot/lock/atomic primitive에 의존한다. 현재 중복을 줄이는 효과는 있으며 독립 패키지 배포가 필요할 때 순수 primitive의 위치를 조정할 수 있다. Unity용 복사본을 만들어 같은 규칙을 두 곳에서 관리하는 변경은 피한다.

## 8. 재현과 감사 자료

감사 재현은 **현재 결함이 나타나는지**를 assert한다. 통과했다고 수정 완료나 사용자 시나리오 성공을 뜻하지 않는다. 구현 시에는 원하는 불변식을 검증하는 회귀 테스트로 옮겨야 한다.

- [artifacts/architecture-audit-20261003/reproduce.cjs](<C:/Users/sster/Documents/Git/UE5_Local_LLM_MCP_lmstudio/artifacts/architecture-audit-20261003/reproduce.cjs>)
- [artifacts/architecture-audit-20261003/reproduction-results.json](<C:/Users/sster/Documents/Git/UE5_Local_LLM_MCP_lmstudio/artifacts/architecture-audit-20261003/reproduction-results.json>)
- [artifacts/architecture-audit-20261003/reference-reproduce.cjs](<C:/Users/sster/Documents/Git/UE5_Local_LLM_MCP_lmstudio/artifacts/architecture-audit-20261003/reference-reproduce.cjs>)
- [artifacts/architecture-audit-20261003/reference-results.json](<C:/Users/sster/Documents/Git/UE5_Local_LLM_MCP_lmstudio/artifacts/architecture-audit-20261003/reference-results.json>)
- [artifacts/architecture-audit-20261003/behavior-reproduce.cjs](<C:/Users/sster/Documents/Git/UE5_Local_LLM_MCP_lmstudio/artifacts/architecture-audit-20261003/behavior-reproduce.cjs>)
- [artifacts/architecture-audit-20261003/behavior-results.json](<C:/Users/sster/Documents/Git/UE5_Local_LLM_MCP_lmstudio/artifacts/architecture-audit-20261003/behavior-results.json>)
- [artifacts/architecture-audit-20261003/rag-package-reproduce.py](<C:/Users/sster/Documents/Git/UE5_Local_LLM_MCP_lmstudio/artifacts/architecture-audit-20261003/rag-package-reproduce.py>)
- [artifacts/architecture-audit-20261003/rag-package-reproduce.results.json](<C:/Users/sster/Documents/Git/UE5_Local_LLM_MCP_lmstudio/artifacts/architecture-audit-20261003/rag-package-reproduce.results.json>)
- [docs/evidence/architecture-contract-audit-20261003.json](<C:/Users/sster/Documents/Git/UE5_Local_LLM_MCP_lmstudio/docs/evidence/architecture-contract-audit-20261003.json>)

근거 JSON의 validator는 주장 분류·근거 종류·동작 경로·아키텍처 계획의 형식을 검사한다. 코드 동작을 대신 검증하지 않는다.
