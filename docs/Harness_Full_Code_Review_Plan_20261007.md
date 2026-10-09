# 전체 코드 재검토와 읽기 비용 개선 계획

작성일: 2026-10-07  
대상: UE5_Local_LLM_MCP_lmstudio의 현재 작업 트리, 기준 HEAD `78abd7f` 및 기존 미커밋 변경  
상태: 2026-10-07 승인 후 저장소 구현 및 아래에 기재한 자동 검증 완료. 아래 F01–F16은 수정 전 감사 기록이며, 현재 반영 상태는 문서 끝의 구현 결과를 따른다. 설치본·게임 프로젝트는 변경하지 않았다.

## 1. 결론과 우선순위

현재 긴 검토가 반복되는 원인을 모델 크기나 지침 부족 하나로 설명할 수 없다. 코드에서 확인한 문제는 다음 경계에 걸쳐 있다.

1. 한 번 입력에 포함된 원문을 다음 라운드에서 발췌로 교체하는 정책.
2. 원문 복원 도구의 전체 응답 예산을 본문 길이처럼 이해하기 쉬운 공개 계약.
3. 메모의 근거 유효성을 표시용 최근 8개 자료에 의존시키는 수명 결합.
4. 잘못된 새 메모가 유효한 기존 메모를 지우는 갱신 경로.
5. 병렬 결과 순서를 최신 파일 버전 순서로 해석하는 중복 판단.
6. 취소·조회·Editor 작업·CI 등록의 별도 결함.

우선 구현할 묶음은 **메모 보존 결함 → 읽기/복원 계약 → 예산에 따른 원문 유지 → API 확인 경로와 모델 행동 지침**이다. 파일 게시와 Editor 작업의 취소 누락은 이와 독립된 우선 수정으로 진행한다.

이번 첨부는 모델의 설명을 포함한 채팅 기록이다. 그 설명만으로 실제 모델 입력, 설치본과 로드된 코드의 동일성, 총 낭비 토큰을 확정하지 않는다. 아래의 코드 결함과 probe 결과는 현재 작업 트리에서 확인한 것이다.

## 2. 검토 범위와 확인 수준

| 영역 | 확인한 경계 | 남은 실행 확인 |
| --- | --- | --- |
| GUI compactor | preprocessor → scope → 입력 조립 → 예산 → SDK prediction → 도구 결과 → 종료/복원 | 첨부 당시 실제 host 입력 및 로드된 build |
| 근거와 원문 | decoder, identity, archive, projection, catalog/read, pin/lease/GC, lineage restore | 실제 긴 대화에서의 원문 유지 비용 |
| 메모와 목표 | 사용자 목표, assistant note, rejected hypothesis, source applicability, 변경/미수신 결과 | 같은 모델의 장기 재사용 능력 |
| 도구 실행 | 승인, 예산 예약, round 취소, 늦은 callback, UI 종결 | 과거 승인 대기의 실제 재발 조건 |
| Unreal 도구 | 파일/심볼 읽기, 쓰기/replace/bundle/delete, 버전·경로·잠금, build 경계 | 실제 UBT/Editor 작업의 취소와 결과 유실 |
| Unity 도구/Bridge | 파일·Git, transport, Editor session/domain, operation journal, test callback | Editor reload·PlayMode·실제 선택 테스트 callback |
| RAG/Editor export | 요청 취소, process owner, shard lock/journal/publication, Editor marker 대기 | 실제 owned Editor child 종료와 기존 Editor job |
| API/문서/Auto | 엔진·패키지 적용성, 심볼 근거 수준, 공통/주제/엔진 문서 선택 | 어떤 절이 실제 입력에 들어가 행동에 쓰였는지 |
| 설치/배포/CI | 배포 파일, 런타임 identity, 설치 검증, update/rollback, 테스트 suite 소속 | 새 변경 이후 clean install/update와 로드 확인 |
| 첨부/헤드리스 | 첨부 범위·정제·서명, headless deterministic core 연결 | GUI 전용 기능과 headless 기능의 별도 검증 |

전체 모듈과 공개 진입점의 책임을 분류하고 관련 실행 경로를 추적했다. 모든 파일의 모든 분기, 플랫폼, Editor 조합을 실행한 결과는 아니다. `legacy_eval`은 제품 Direct 경로와 분리된 기존 평가 자료로 취급한다. 사용자 게임 소스는 잘못된 주장과 실제 구현을 대조하는 근거로만 사용했다.

### 이번 검토에서 유지할 정상 경로

- 최초 원문을 아직 모델 입력에 넣지 않은 상태에서 보존하려는 first-consumer 보호.
- 정확한 입력 측정과 BudgetBroker의 전체 입력/출력/도구 결과 여유 계산.
- provider-qualified Allow all 처리와 수동 승인 경로의 구분.
- archive의 sibling 격리, parent snapshot 상속, pending pin, 복원 실패 rollback, close와 cross-scope GC 잠금.
- Unity의 Editor session/domain 구분, operation 재실행 방지, 제출 후 결과 미확인 보존.
- RAG의 shard 잠금·journal·staging·publication과 publication 이후 committed 결과 보존.
- 기존 설치 트랜잭션, 관리 Python 경로 처리, 배포용 검증과 개발 회귀 검증 분리.

## 3. 현재 발견

아래 F 번호는 수정 묶음과 검증을 연결하기 위한 식별자다. 확정 결함, 정책 개선, 실행 확인이 필요한 후보를 구분한다.

### F01. 원문 노출 완료가 곧 발췌 교체 가능 상태가 된다

**정책 개선 · TestVerified**

`prediction-loop.ts:272`는 매 라운드 입력 측정 전에 projection을 수행한다. `working-context.js:363`은 아직 소비 표시가 없는 원문을 보호하고, `captureExposure:518`은 생성 완료 뒤 그 표시를 설정한다. 다음 라운드에는 예산 압박과 무관하게 발췌로 교체할 수 있다. 기본 발췌는 `config-defaults.ts:5`의 512자다.

메모리 probe에서 3,020자 본문은 첫 projection에 유지되었고, 한 번의 completed exposure 뒤 512자로 줄었다. 뒤쪽 확인 대상 문자열은 다음 입력에서 사라졌다. 저장소 원문은 남아 있었다. 이는 “모델이 입력을 받았다”와 “여러 파일 관계를 검토하는 동안 원문이 계속 필요하다”가 다른 수명이라는 점을 보여준다.

현재 first-consumer 보호는 유효하다. 개선은 정확한 예산 안에서 원문 유지/축약 시점을 결정하도록 책임을 옮기는 것이다. 모델이 실제로 이해했는지는 하네스가 판정하지 않는다.

### F02. archive의 maxChars가 전체 응답 예산인데 본문 길이처럼 보인다

**공개 계약 개선 · TestVerified**

`working-context.js:459`의 schema는 maxChars의 범위를 제공하지만 본문과 전체 응답의 비용을 구분하지 않는다. `evidence-archive.js:493`은 JSON 메타데이터와 escaping을 포함한 전체 응답 길이를 제한한다.

같은 코드 2,078자/envelope 2,410자 probe:
- 남은 envelope 길이를 다음 maxChars로 요청: 본문 1,315 → 80 → 4자 → response_budget_too_small, 총 4회.
- 고정 4,096 예산과 반환 nextOffset 사용: 1회 완전 복원.

기존 maxChars의 의미를 조용히 바꾸지 않는다. v1 설명을 명확히 하고, 새 본문 view에서는 본문 범위와 응답 예산을 분리한다.

### F03. 메모 근거 유효성이 최근 8개/6,000자 표시 제한에 종속된다

**확정 결함 · TestVerified**

`working-context.js:308`의 summaryRefs는 summaryEvidence()에서 ref를 얻는다. summaryEvidence는 기본 8개/6,000자 제한을 가진다. 이 집합을 `prediction-loop.ts:315` 등에서 메모 검증에 사용한다. `continuity-model-notes.js:233`은 남은 유효 ref가 없으면 항목을 제거한다.

아카이브에 9개 근거가 정상 존재하는 probe에서 오래된 record는 load 성공인데 summaryRefs에는 없었다. 원래 tool call도 현재 history에 남지 않고 해당 archive ref에만 의존하는 기존 메모의 항목이 1개에서 0개로 감소했다. 9번째 자료를 읽으면 모든 메모가 삭제된다는 뜻은 아니다.

**표시 예산은 근거의 존재·접근 권한·버전 유효성을 결정해서는 안 된다.** 실제 메모가 참조하는 소수 ref를 archive owner로 검증하도록 분리한다.

### F04. 잘못된 비어 있지 않은 새 메모가 기존 메모를 지운다

**확정 결함 · TestVerified**

`continuity-model-notes.js:229`에서 존재하지 않는 ref의 새 항목을 제거하면 빈 note가 생길 수 있다. `prediction-loop.ts:691`은 이를 기존 note에 대입한 뒤 빈 결과를 null로 만든다. 유효 prior 1개 + 잘못된 ref를 가진 새 항목 1개의 probe에서 caller가 prior를 지우는 결과가 재현됐다.

반대 경로인 `semantic-handoff.ts:166`은 유효 항목이 남아야 대체한다. 명시적 빈 객체로 note를 비우는 기존 계약은 보존하면서 accepted_update / explicit_clear / rejected를 구분한다.

### F05. 병렬 반환의 배열 순서를 최신 파일 버전으로 사용한다

**확정 결함 · TestVerified**

`continuity-model-notes.js:254,327`은 같은 파일의 마지막 관측을 최신으로 사용한다. 같은 batch의 두 읽기 결과 순서를 바꾸면 동일 note의 applicability가 same_observed_version과 source_superseded 사이에서 바뀐다.

SDK는 병렬 결과를 요청 순서로 정렬한다. 그 순서는 파일 관측의 인과 순서를 증명하지 않는다. Reference 경로는 이미 같은 batch 충돌을 unknown으로 다루므로 최신성 판단이 중복돼 있다. 기존 근거 owner가 순서·충돌 view를 제공하고 note와 Reference가 함께 소비해야 한다.

### F06. catalog index pagination은 중간 GC 후 항목을 누락한다

**확정 결함 · TestVerified**

`evidence-archive.js:379`는 매 호출 재정렬한 현재 배열에 startOffset을 적용한다. A/B/C에서 A를 반환한 후 A가 회수되면, nextOffset=1은 B/C의 C부터 시작한다. B가 존재하는데도 hasMore=false로 끝나는 probe를 확인했다.

현재 자료만 노출한다는 계약은 정상이다. 계속 존재하는 미조회 항목을 조용히 건너뛰는 것은 별도 문제다. scope·필터·정렬·snapshot 경계에 결속된 continuation이 필요하다.

### F07. canonical source identity를 조회·요약에서 다시 계산한다

**계약 불일치 · TestVerified**

`evidence-identity.js:13`과 `working-context.js:199`가 생성·저장한 canonical digest를 projection은 사용한다. 그러나 `evidence-archive.js:475`와 `working-context.js:325`는 표시용 sourceIdentity 객체를 재해시한다. 같은 record가 다른 digest를 반환하는 probe를 확인했다.

현재 archive coverage가 evidenceId/version을 사용하므로 곧바로 잘못된 파일이 열린다는 증거는 없다. 공통 body view와 중복 조회 추적을 연결하기 전에 정본 identity 재사용으로 고친다.

### F08. 복원 표현과 식별자가 모델에게 큰 조립 부담을 준다

**정책/사용성 개선 · SourceVerified**

현재 read는 sanitized_archived_tool_envelope를 반환한다. 긴 evidenceId와 archive version을 반복해서 입력하고 JSON 응답 자체를 다시 읽는다. 로그에는 ID 추측 후 catalog 재조회가 있다.

현재 본문 선택 함수는 content/text/body/code/items/rows를 지원하지만 directory entries는 빠져 있다. projection과 archive body view가 같은 selector를 사용하도록 하고, 짧은 read handle과 재사용 가능한 continuation을 제공한다. 정확한 ID/버전/접근 검사를 제거하지 않는다.

### F09. 공유 예산 거부는 확인됐지만 영구 예약 누수는 입증되지 않았다

**정상 보호와 비용 개선 구분 · SourceVerified**

`budget-broker.ts:65`의 held_reservations_insufficient는 병렬 batch의 다른 호출이 예산을 보유한 경우 발생할 수 있다. archive 예약은 maxChars에 multiplier 3, 최소 2,048, 추가 1,024를 적용하여 최소 예약량이 7,168인 경로가 있다.

이 수치는 UTF-8/JSON 응답 크기를 고려한 보수적 예약 추정치다. 실제로 소비한 모델 토큰 7,168개를 측정했다는 뜻은 아니다. 반환 크기 정산과 다음 입력의 정확한 토큰 측정은 별도 단계다.

로그에는 첫 페이지 성공과 동료 호출 거부, 이후 순차 복원이 보인다. 이를 영구 deadlock으로 분류하지 않는다. 큰 첫 요청이 batch 여유를 차지하고 작은 후속 페이지가 늘어나는 비용은 producer의 실제 전체 응답 계약과 연결해 개선한다.

### F10. Unreal 파일 변경에서 게시 전 취소가 재검사되지 않는다

**확정 결함 · TestVerified**

`direct-server.js:58`의 시작 검사 후 `direct-file-mutation-capabilities.js:140` 이하의 async 해석/잠금/재검사를 거쳐 파일을 게시한다. mutation owner가 signal을 소비하지 않는다.

임시 프로젝트 probe에서 게시 전 abort를 발생시켜도 fileCreated=true, result.ok=true, operation=created였다. replace/bundle/delete에도 동일한 취소 전달을 점검해야 한다. Unity의 shared Files.mutate에는 게시 전 검사가 있어 양쪽의 동작 계약이 다르다.

### F11. Git 읽기는 동기 child process여서 취소 전달이 막힌다

**구조 개선 · SourceVerified**

`shared-tool-core/git.js:28`의 execFileSync와 Workspace 호출에는 요청 signal 연결이 없다. 기존 10초 timeout/4MiB 제한은 보호장치지만, 그동안 Node event loop와 취소 처리가 지연될 수 있다.

공유 Git owner에 비동기 process 실행·요청 취소·timeout·출력 제한·owned child 종료를 연결한다. 별도 Git 전용 예산 시스템을 만들지 않는다.

### F12. RAG의 Editor export 경로가 공통 취소·deadline을 우회한다

**확정 결함 · TestVerified(격리 marker 대기)**

일반 collector는 operation scope를 사용한다. Editor export는 `editor_export_process.py`의 subprocess.run과 `editor_export_markers.py:72`의 polling을 사용하며 공통 check_operation을 호출하지 않는다.

10ms 후 취소, operation deadline 30ms, marker timeout 80ms의 probe가 취소/operation timeout 대신 약 94ms 후 EDITOR_EXPORT_MARKER_TIMEOUT으로 끝났다. 기본 marker 대기에는 1,800초 설정 경로가 있다.

이 하네스가 시작한 headless child와 사용자가 이미 실행 중인 Editor를 구분한다. 전자는 owned process 종료를 확인하고, 후자는 응답 대기를 취소해도 실제 Editor 작업 효과는 별도로 unknown/관측 상태로 남겨야 한다.

### F13. API 확인 도구의 범위가 필요한 검증을 모두 지원하지 않는다

**capability 개선 · TestVerified/SourceVerified**

- engine_local declaration 경로는 함수/enum을 중심으로 처리하여 Actor.h에 실제 존재하는 Owner/Instigator 필드가 helper에서 None이었다. GetOwner/GetWorld 함수는 성공했다.
- Unreal read_symbol의 lexical 추출은 주석의 중괄호나 앞선 호출문 때문에 본문 경계를 잘못 잡는 사례가 재현됐다.
- 현재 도구가 heuristic/absenceProof=false를 표시하는 것은 유지할 정상 계약이다.

필드·매크로·부모 선언을 찾는 경로와 정확한 범위 조회를 보강한다. 검색 실패를 API 부재로 확정하는 기능은 추가하지 않는다.

### F14. Unity 테스트 완료 callback의 전체 선택 범위 확인이 부족하다

**실행 확인이 필요한 계약 후보 · SourceVerified**

`FrameworkAdapter.cs:119`에서 selected 집합을 만들고 반환 leaf를 제거하지만 종료 전에 selected.Count를 확인하지 않는다. 같은 root의 요청 A/B에 A만 있는 callback도 완료로 정리될 수 있는 소스 경로다.

실제 Editor callback으로 재현한 것은 아니다. 취소/skip/filter의 실제 계약을 먼저 대조하고, execution completed와 requested coverage complete를 별도 표현하도록 보완한다. 부분 결과를 전체 성공으로 사용할 수 없게 한다.

### F15. Unity 설치 회귀 테스트가 CI suite에 등록되지 않았다

**확정 결함 · SourceVerified**

`tests/test_unity_install.py`가 `scripts/ci/ci_suites.py`의 PRIMARY suite에 없다. CI는 그 suite들과 Node npm test를 실행하므로 로컬에서 실행한 설치 회귀와 원격 CI의 범위가 다르다.

기존 suite SSOT에 등록하고, 제품 테스트의 suite 소속 또는 명시적 제외를 검사한다. legacy/실Editor/수동 시험의 제외 사유를 유지한다.

### F16. 지침과 진단은 존재하지만 전달·사용 효과를 구분해야 한다

**행동/계측 개선 · SourceVerified**

공통 문서에는 이미 API 선언·부모·반례·소유자·수명 확인이 있다. Auto는 유한한 주제 선택과 최대 6절/4후보, 정확한 추가 토큰 측정을 사용한다. 3개 이상 주제가 충돌하면 core로 축소하고, 관측 API 신호는 원문이 현재 입력에 있을 때만 선택에 영향을 준다.

따라서 문서의 존재만으로 실제 주입을 증명하지 않는다. 또한 주입만으로 모델이 적용했다고 평가하지 않는다. 이번 Client RPC 방송 오판은 확인된 엔진 계약을 사용하지 못한 대표 평가 사례다.

## 4. 책임과 계약을 나누는 기준

| 사실/행동 | 정본 소유자 | 소비자와 금지할 중복 |
| --- | --- | --- |
| 실제 파일·Editor 변경 효과 | 각 provider의 mutation/operation owner | 하네스는 반환·미수신을 관측; 성공을 추론해 생성하지 않음 |
| 원본 identity/version/범위 | 기존 evidence identity와 provider adapter | projection/summary/read가 표시 자료에서 다시 계산하지 않음 |
| archive bytes·접근·보존·회수 | EvidenceArchive + WorkingContext | 새 DB/전역 작업 저장소를 추가하지 않음 |
| 관측 순서·충돌·근거 유효성 | EvidenceManager가 제공하는 공통 관측 view | note/Reference/availability가 독자적인 최신성 규칙을 만들지 않음 |
| 이번 입력에 실제 들어간 범위 | 최종 input assembler/availability | 과거 반환, 생성 완료, 의미 이해를 구분 |
| 입력 유지/축약 시점 | ContextManager | storage가 예산 없이 무조건 축약 시점을 정하지 않음 |
| 여유·예약·정산 | BudgetBroker | 도구별 별도 토큰 잔고를 추가하지 않음 |
| 모델 판단 메모 | 기존 continuity-model-notes | 검토 완료/버그 사실로 자동 승격하지 않음 |
| 문서 선택 | 기존 finite Auto selector | 모델 호출·작업 계획·도구 강제 실행을 추가하지 않음 |
| 승인·진행 UI | Tool boundary와 UI tracker | Allow all 처리와 provider 작업 완료를 혼합하지 않음 |

SSOT는 모든 상태를 한 객체에 합치는 뜻이 아니다. 각 사실의 정본과 무효화 조건을 공유하고 필요한 읽기 view만 전달한다.

- **SRP:** 보관, 유효성 검증, 입력 표시, 예산, 실제 실행의 책임을 구분한다.
- **OCP:** 기존 도구 계약/adapter를 통해 body 표현과 provider 지원을 늘린다.
- **LSP:** 같은 읽기 계약은 source version·표현·단위·부분성·오류 의미를 동일하게 보장한다.
- **ISP:** renderer는 읽기 view, selector는 유한 signal, broker는 크기 계약만 받는다.
- **DIP:** 정책 함수는 관측 값과 명시적 port를 소비한다. UI/요약기가 직접 파일을 재조회하거나 provider를 실행하지 않는다.

## 5. 구현 계획

### W0. 재현 자료와 측정 기준 고정

- 이번 로그의 잘못된 판단, 정정, 재읽기, 동일 SHA, archive page 흐름을 작은 재현 사례로 정리한다.
- 저장소 build, 설치 build, 실제 로드 build를 각각 기록한다.
- 기존 availability/evaluation 코드를 재사용하여 현재 입력 중복과 과거 원문 재획득을 구분한다.
- 자료가 빠진 수치를 0으로 간주하지 않는다. host 입력 확인 여부도 별도 유지한다.
- 비용 기록은 입력/생성/요약 호출/도구 횟수/본문·envelope bytes/벽시계 시간을 구분한다.

완료 기준: 같은 fixture를 같은 설정으로 다시 실행해 비용과 정확도를 비교할 수 있다. 이 단계에서 새 모델 호출을 상시 서비스에 추가하지 않는다.

### W1. 메모 유효성과 갱신 계약 수정

대상: F03–F05. 기존 note owner, EvidenceManager, WorkingContext.

1. 표시용 summaryEvidence와 근거 검증 함수를 분리한다.
2. 메모가 실제 참조한 ref만 scope·record·archive version·source applicability로 검증한다.
3. accepted_update / explicit_clear / rejected 결과를 만든다.
4. rejected 갱신은 기존 메모를 보존한다. 부분 갱신은 기존 id와 명시적 supersedes/status를 기준으로 정의하고, 전체 snapshot 교체와 구분한다. 기존 4항목/1,500자 상한을 유지하며, 합친 결과가 상한을 넘거나 갱신 의미가 불명확하면 prior를 보존하고 거부 사유를 남긴다. 기존 footer의 갱신 의미를 조용히 바꾸지 않는다.
5. 같은 batch 상충 결과는 순서 미확인으로 표시한다. 다른 batch의 후속 관측과 구별한다.
6. 표시에서 생략, 실제 근거 회수, 파일 변경, 프로젝트 변경, 사용자 목표 변경을 서로 다른 무효화 사유로 다룬다.

완료 기준: 9개 이상 근거, 화면/요약 예산 축소, 잘못된 ref, 병렬 순서 반전으로 유효 note가 잘못 삭제되거나 최신성이 바뀌지 않는다.

### W2. 근거 표현과 archive 읽기 계약 정리

대상: F02, F06–F08. archive/identity/decoder와 기존 local read tool.

1. canonical identity와 sourceVersion을 생성 경계에서 확정하고 모든 view가 재사용한다.
2. source file hash, 원래 응답 hash, 정제된 archive hash를 구별한다.
3. v1 envelope 조회를 유지하면서 versioned body view를 추가한다.
4. projection/read/summary가 같은 body selector를 사용한다. directory entries와 search matches의 부분성도 표현한다.
5. 범위와 응답 예산을 별도 필드로 제공하고 고정 예산+반환 continuation으로 페이지를 전진시킨다.
6. archive owner가 짧은 read handle을 발급한다. 접근 view와 immutable record/version/representation에 결속하고, 해제·분기·재시작 시 다른 자료로 재연결하지 않는다.
7. catalog continuation은 안정적인 key/snapshot 경계로 바꾼다. v1 offset은 호환 경로로 남긴다.
8. 최소 응답 크기 부족은 실행 가능한 최소 예산/재요청 정보와 함께 반환한다.

완료 기준: 작은 파일의 불필요한 끝부분 쪼개기와 ID 재작성 비용이 줄고, 범위/버전/권한 보호가 유지된다. 새 view는 원본 파일 읽기나 mutation receipt로 승격되지 않는다.

### W3. 정확한 예산에 따라 원문을 유지하고 교체

대상: F01, F09. ContextManager가 유지 정책을 결정하고 WorkingContext는 보관/표현 작업을 수행한다.

- 보관 가능성과 모델 입력에서 축약할 필요를 분리한다.
- 원문을 포함한 후보를 먼저 정확하게 측정한다. 충분히 맞으면 여러 파일 관계 검토에 필요한 최근 원문을 유지한다.
- 후보 조정은 기존 bounded fitting 루프와 측정 재사용 경로에 통합한다. 파일마다 별도 tokenizer 호출을 반복하는 새 루프를 만들지 않고, 최종 입력은 기존 정확 측정을 통과해야 한다.
- 압박이 생기면 완전한 call/result 교환을 보존하면서 오래된 자료부터 축약한다.
- 아직 노출되지 않은 결과, 현재 필요한 복원 페이지, 취소 후 미수신 사실의 보호를 유지한다.
- “한 번 생성이 끝났다”를 “검토 완료”로 표현하지 않는다.
- 동일 원문이 입력에 남아 있는 재읽기와, 압축 때문에 다시 얻는 읽기를 별도 집계한다.
- 필요한 fresh read나 수정 직후 재검증을 막는 횟수 제한을 추가하지 않는다.
- producer의 실제 응답 제한과 broker 예약 계약을 맞춘다. 실제 SDK envelope 비용과 한국어/JSON escaping을 포함해 검증한 후 상수를 조정한다.

완료 기준: headroom이 충분한데 동일 원문이 즉시 사라지는 경로가 없어지고, 압박 상황에서도 최종 정확 측정·다음 도구 결과 여유가 충족된다. 과거 reasoning을 줄이는 설정은 명시적 SDK 경계와 기존 설정 의미를 보존한다.

### W4. 취소와 게시 시점의 계약을 양쪽 엔진에 맞춤

대상: F10–F12. 기존 provider/operation/process owner.

- Unreal signal을 경로 해석, 잠금 대기, 재검사, 게시 직전으로 전달한다.
- 취소와 publication의 순서를 명시한다. 게시 전 취소는 변경하지 않고, 게시 후 취소는 실제 적용 사실을 보존한다.
- bundle은 적용된 파일·rollback 결과·남은 불확실성을 기존 transaction journal에 기록한다.
- Git child는 공통 비동기 실행 경로로 옮겨 요청 cancel/timeout/output cap을 연결한다.
- RAG Editor export의 child 실행과 marker 대기가 기존 operation cancellation/deadline을 소비하게 한다.
- 기존 사용자 Editor를 요청 취소 때문에 종료하지 않는다. owned process 종료와 제출된 Editor job의 결과 미확인은 별도 계약이다.
- 취소 후 재시도에서 같은 mutation/operation을 자동 재실행하지 않는다.

완료 기준: 게시 전/후, 잠금 대기, child 실행, marker 대기, 결과 유실, 재시작의 각 조건에서 파일 효과와 보고가 일치한다.

### W5. API와 심볼 확인 경로 보강

대상: F13–F14와 Unreal/Unity 공통 확인 계약.

- Unreal 엔진 선언 조회에 필드·매크로 인접 선언·부모 위치 확인을 지원한다.
- lexical 심볼 추출은 주석/문자열/선언/정의/호출을 구분하고, 확신하지 못하면 후보 범위와 정확한 read range를 반환한다.
- UPROPERTY/UFUNCTION/Delegate/FORCEINLINE은 목적·선언·호출부·부모·버전을 연결한다. 작은 함수나 raw 포인터라는 이유만으로 매크로를 일괄 요구하지 않는다.
- RPC는 server/client/multicast, 소유 연결, actor 복제 조건을 구분한다.
- Unity는 해결된 패키지·asmdef·Editor/Player·직렬화/Scene/Prefab 사용처를 유지한다.
- Unity 테스트 completion은 요청 범위, 실제 실행 결과, 누락·skip·취소를 별도 반환한다. callback 계약은 실제 Editor에서 확인한다.
- 검색 결과 없음과 API 부재를 구별하고, 빌드 성공을 런타임 동작 증명으로 확대하지 않는다.

완료 기준: 이번에 나온 잘못된 API 주장과 정상 반례를 함께 통과한다. 일반 코드 검토를 막는 새 정적 검증 gate는 추가하지 않는다.

### W6. 지침과 진행 정보를 짧고 재사용 가능하게 제공

- 공통 지침에는 필요한 근거 확인, 반례 검토, 짧은 진행 기록만 둔다.
- 파일마다 전체 원문을 자연어로 재서술하는 대신 확인한 관계·근거 위치·미확인만 남기도록 예시를 바꾼다.
- “읽은 범위”와 “모델이 검토했다고 주장한 범위”를 다른 정보로 유지한다.
- 초기화/보정/부모/호출자를 확인하기 전 결함 등급을 확정하지 않는 사례를 추가한다.
- SSOT 관련 정상 예시는 설정값/실행 상태/복제값/표시 캐시의 차이를 포함한다.
- Auto는 기존 유한 selector를 유지한다. 명시 주제·현재 반환 자료·진단 신호의 우선순위와 생략 사유를 보여준다.
- 전문 문서를 모두 넣는 대신 필요한 짧은 계약 절을 선택하고 추가 토큰 예산 안에서 측정한다.
- 단순 중복이나 transport 무진전만 기계적으로 알린다. 모델 대신 버그 여부·설계 우수성·전체 작업 완료를 판정하지 않는다.

완료 기준: 지침 토큰을 늘리는 것만으로 성공 처리하지 않는다. 실제 평가에서 중복 읽기·오판 재발·완료 보고 품질을 함께 본다.

### W7. 설치·CI·평가에 새 계약 연결

- F15를 기존 CI suite에 반영하고 제품 테스트 누락을 검사한다.
- 새 모듈/생성 자료/SDK patch가 portable package와 installer 검증에 포함되는지 확인한다.
- identity는 저장소/설치/로드를 구분한다. 소스 파일이 맞는다는 이유로 현재 채팅에서 실행됐다고 보고하지 않는다.
- 기존 Off/0/수동 topic/명시 GUI 설정을 마이그레이션으로 덮어쓰지 않는다.
- archive 새 표현은 읽기 호환을 제공하고 write/GC 계약 변경이 필요할 때만 새 storage namespace를 사용한다.
- headless는 같은 core의 변경 영향과 별도 입력/도구 예산 계약을 시험한다. GUI 전용 개선을 headless 지원으로 광고하지 않는다.
- 설치/업데이트 rollback과 schema 호환을 통과한 뒤 동일 모델 비교를 수행한다.

## 6. 수명과 무효화 규칙

| 대상 | 시작과 종료 | 갱신/무효화 |
| --- | --- | --- |
| tool call 예약 | 실행 전 reserve → result reconcile/거부/실패/round close | 늦은 callback은 닫힌 예약을 되살리지 않음 |
| 현재 원문 입력 | 반환 → 다음 실제 입력 선택 | 정확한 예산 정책에 따른 축약; archive 존재와 독립 |
| archive record | immutable capture → 접근 제한/GC | scope·lineage·version 검사, 실제 회수는 unavailable |
| body view/handle | 기존 record의 표현 발급 → record/view 종료 | offset 단위/representation 변경 시 재사용 불가 |
| note | 승인된 갱신 → 명시 clear 또는 실제 무효화 | 표시 생략만으로 삭제하지 않음; 실패한 새 갱신은 prior 보존 |
| source applicability | 관측 batch와 버전으로 생성 | 병렬 충돌 unknown; 후속 실제 관측으로 변경 |
| reference 문서 | 매 입력의 유한 선택 | 목표/엔진/패키지/예산 변화; source facts와 구분 |
| RAG operation | 요청 → 수집 → publication → terminal | 취소는 publication 전/후를 구분 |
| Unity operation | request/session/domain에 결속 → terminal/journal | reload/소켓 유실은 완료 증명 아님 |
| UI 상태 | 준비/승인/실행/종료 | provider 효과와 사용자 취소를 혼합하지 않음 |

## 7. 검증 행렬

| 검사 | 반드시 포함할 반례 | 통과 기준 |
| --- | --- | --- |
| note 참조 | 8→9→40개 근거, 작은 표시 예산, 실제 GC | 표시 축소로 유효 ref가 소멸하지 않음 |
| note 갱신 | 잘못된 ref/JSON, 부분 수락, explicit clear | 실패 갱신은 prior 유지, 명시 clear만 비움 |
| 관측 순서 | 같은 batch A/B 순서 반전, 후속 batch 변경 | 병렬 순서가 최신성을 결정하지 않음 |
| archive paging | 작은 파일, 한글, escape, 최종 잔여, 너무 작은 예산 | 응답 상한 준수와 단조 전진, 빈 성공 페이지 없음 |
| catalog | 중간 삭제/추가/TTL, 같은 timestamp, scope 변화 | 살아 있는 미조회 항목을 성공 EOF로 누락하지 않음 |
| identity/view | 원문→projection→summary→body/envelope read | identity/version의 의미 보존 |
| 원문 유지 | headroom 충분/부족, 큰 병렬 batch, 취소, 복원 | 필요 없는 즉시 축약 감소, hard limit 준수 |
| provider 취소 | 대기/게시 직전/게시 직후/결과 유실 | 실제 효과와 reported outcome 일치 |
| Git/RAG | 느린 process, 출력 초과, marker 없음, cancel | 기존 operation owner에서 종료/unknown 처리 |
| Unreal API | 필드·부모·다중행 macro·주석 괄호·overload·호출문 | 후보/정의/부재/불확실을 정확히 구분 |
| Unity 테스트 | 요청 A/B 중 A만 반환, skip/cancel/reload | 종료와 요청 전체 범위 확인을 구분 |
| 설치/CI | package clean install/update, 한글 경로, suite 소속 | 배포본 실행 의존성과 필수 회귀 검사 포함 |
| 모델 행동 | 실제 버그, 정상 코드, 외부 파일 변경, 다른 프로젝트 | 오판/누락/반복을 함께 측정; 짧은 빈 답변은 성공 아님 |

### 비용과 품질 지표

기존 평가 도구의 range accounting을 확장한다.

- 현재 입력에 이미 있는 동일 버전·동일 범위의 중복 조회.
- 압축으로 사라져 다시 얻은 과거 원문 범위.
- 새로 얻은 source 범위와 catalog/envelope 탐색 비용.
- 실제 코드 본문 대비 메타데이터·escaping의 비율.
- archive ID 오류/재조회, budget 거부, 종료까지 tool call 수.
- 입력/생성/요약 모델 호출별 토큰과 시간. prompt 합계와 실제 재계산 비용은 동일하다고 가정하지 않음.
- 정확한 결함 탐지, 정상 코드를 결함으로 지적한 횟수, 철회한 오판의 재발, 최종 보고의 완료/미확인 구분.

효과를 아직 측정하지 않았으므로 절감률을 약속하지 않는다. 결정적 계약 시험을 먼저 통과시킨 뒤, 같은 모델·양자화·KV 설정·컨텍스트·생성 설정에서 대표 실패 사례와 다른 프로젝트의 정상/결함 사례를 비교한다.

## 8. 도입 순서와 되돌리기

1. W0과 W1을 먼저 수행한다. 메모 결함을 고쳐도 본문/출처 계약은 그대로 유지한다.
2. W2를 호환 경로로 추가하고 W3가 그 표현을 소비하게 한다.
3. W4는 별도 작은 변경 묶음으로 병행할 수 있다. 토큰 최적화와 파일 게시 의미를 같은 커밋에서 섞지 않는다.
4. W5/W6는 실제 전달 계약을 바탕으로 적용한다.
5. W7에서 배포·회귀·모델 비교를 확인한다.

기존 v1 archive 조회, 명시 GUI 설정, provider 권한/receipt, 사용자 목표를 보존한다. 새 view 선택은 되돌릴 수 있어야 하며, 되돌린 뒤 새 handle을 다른 record로 해석해서는 안 된다. 저장 형식이나 GC 계약을 바꿀 경우 전용 namespace와 이전 자료 읽기 규칙을 먼저 정한다.

## 9. 이번 검토의 실행 기록

- 메모리 projection probe: 첫 원문 유지, 한 번 completed exposure 후 3,020자→512자, tail 누락 확인.
- 독립 archive probe: remaining 예산 방식 4회/실패, 고정 4,096 방식 1회/완료; catalog GC 누락과 digest 차이 확인.
- 독립 note probe: 표시 8개 제한과 refs 유효성 결합, 잘못된 새 note의 prior 삭제, 같은 batch 순서에 따른 applicability 변경 확인.
- 독립 provider probe: 임시 Unreal 프로젝트의 게시 전 취소 무시, RAG marker의 공통 cancel/deadline 미소비 확인.
- 기존 working-context/input-availability/behavior-storage 시험 3개 파일을 직접 실행하여 종료 코드 0 확인.
- 독립 storage 회귀 20/20, 승인 관련 집중 사례 7개 통과 확인.
- 실제 LM Studio 재실행, 실제 Editor/빌드, 설치/업데이트/배포는 수행하지 않았다.

근거 패킷: `docs/evidence/harness-full-review-plan-20261007.json`. 패킷 형식 검사는 계획의 근거 구조를 확인하는 검사이며 제품의 static validate를 추가하는 작업이 아니다.



## 10. 승인 후 구현 결과 — 2026-10-07

### 반영한 책임과 호환 계약

| 묶음 | 현재 반영 | 검증/남은 경계 |
| --- | --- | --- |
| W0 | 기존 14개 행동 평가 사례에 원문 재사용/재획득, Unicode paging, 표시에서 빠진 note 근거, Client RPC 수신 범위 변형과 비용 항목 추가 | 수동 모델 비교 명세이며 라이브 전후 결과가 아님 |
| W1 | 표시 예산과 note ref 검증 분리; 잘못된 snapshot은 prior 보존; 명시 clear 별도; note 보관 ref와 실제 입력 전달 분리; Windows alias/deleted/동일 batch 최신성 통일 | 최대 4항목/1,500자 유지. 새 부분 병합 프로토콜을 만들지 않고 기존 전체 snapshot을 원자적으로 수락/거부 |
| W2 | 동일 본문 selector·canonical digest, 짧은 scoped handle, body view, 안정적인 catalog v2 continuation, 정확한 전체 JSON 예산, lexical/byte 범위와 검증 한계 전달 | 기존 envelope와 catalog v1 기본값 유지. source SHA/정제 hash/본문 UTF-16 범위/원본 byte 범위 분리 |
| W3 | 실제 입력에 여유가 있으면 원문 유지. ContextManager가 정상 입력과 fresh planning 후보의 pressure를 판정하고 projection 후 정확히 재측정 | 기존 projection 단위 유지. Broker 예약 상수는 근거 없이 낮추지 않음. 새로운 읽기 횟수 제한 없음 |
| W4 | Unreal create/CAS/bundle/delete 게시 전 취소; 첫 bundle 게시 이후 commit/rollback 완결; Git 비동기·request signal; Editor child와 외부 Editor job의 취소/미확인 구분 | 기존 Editor 작업은 임의 종료하지 않음. 미확인 작업은 같은 runId 종료 marker 전까지 재실행 금지 |
| W5 | Unreal field/UPROPERTY 인접 선언·접근 수준·부모 후보; 주석·문자열·호출을 제외한 lexical 함수 후보; 다른 owner의 qualified symbol 대체 금지; Unity 선택 leaf 누락을 incomplete/outcome_unknown으로 표시 | C++ compiler proof나 부모 재귀 해석은 아님. Unity 실제 Editor callback은 미실행 |
| W6 | 공통 proof 절 900자 이내 유지하며 근거 재사용·좁은 읽기·반례·짧은 보고 보강. Unreal Client RPC/소유 연결 계약 보강 | 기존 Auto selector와 추가 토큰 한도 사용. 모델·judge·정적 품질 gate 추가 없음 |
| W7 | Unity 설치 회귀를 기존 CI suite에 포함; 필수 membership 검사; 기존 module 크기 경계는 실제 책임 증가에 맞춰 3개만 좁게 조정 | 설치/패키지 fixture와 개발 snapshot으로 검증. 실행 중인 LM Studio에 설치하지 않음 |

### 추가로 발견하여 고친 경계

- note 보존 pin은 first-consumer 전달 증명이 아니므로 실제 입력 검사와 분리했다.
- 복원 body가 lexical ambiguity/검증 제한·마지막 byte 범위를 잃지 않도록 sourceFacts를 정제·projection·복원·checkpoint에 전달한다. `hasMore=false`로 원본 전체를 확인했다고 생성하지 않는다.
- 긴 연속 식별자에서 archive capability 정규식이 각 문자부터 다시 매치하여 오래 걸리던 경로를 토큰 시작 경계로 제한했다. 실제 HMAC 형식 제거와 일반 소스 보존을 자식 프로세스 deadline fixture로 검증했다.
- 원문 유지 후 fresh planning의 새 pressure에도 동일 정책이 적용되며, 다음 라운드는 실제 측정한 candidate history를 사용한다.
- bundle catch의 미정의 result 접근, field lookup의 alias/forward 선언 오분류를 제거했다.
- Editor request timeout은 종료 증명이 아니므로 두 번째 writer를 자동 실행하지 않는다. watcher 실패 marker에도 원래 job의 runId를 보존한다.
- 이전 child 종료 대기 때문에 시작하지 않은 새 Editor job을 pending으로 기록하지 않는다. scope=all/editor_metadata 및 CLI에서 취소 의미와 보존 경로가 유지된다.

### 재현 테스트와 실행 범위

- `lmstudio-context-compactor-plugin/test/harness-stability.test.cjs`: note 유효성/갱신/동일 batch, first-consumer, catalog GC, Unicode body paging, legacy 호환, lexical/byte 사실 전달, 긴 식별자 정제.
- `test/behavior-continuity.test.cjs`, `test/prediction-loop.test.cjs`: headroom/pressure, fresh planning/원문 복원 통합 경로.
- `lmstudio-unreal-agent-mcp/test/harness-stability.test.js`: create/CAS, bundle 게시 직전·직후 취소와 실제 파일 내용, lexical 추출 반례.
- `lmstudio-unity-mcp/test/git.test.js`: 실제 임시 Git 저장소, 취소 후 재사용, event loop 응답.
- `tests/test_editor_export_runner.py`: cooperative marker 취소, 실제 Python child deadline/종료, 외부 job marker·복구, watcher runId, sync/CLI 예외 경계.
- `tests/test_engine_header_evidence_identity.py`: 필드·다중 줄 매크로·부모 후보·중첩 타입·alias/forward 반례.

구체 실행 결과와 로그 위치는 `docs/evidence/harness-stability-implementation-20261007.json`에 기록한다. 테스트 수는 모델 품질 점수가 아니다. 실제 LM Studio에 로드된 build, Unity Editor reload/PlayMode, 실제 Unreal Editor 종료와 네트워크 플레이는 이번 자동 시험으로 증명하지 않는다.

### 운영 복구

Editor 결과 미확인 marker의 의미와 해제 조건은 [Editor_Metadata_Export.md](Editor_Metadata_Export.md#요청-중단과-결과-미확인)를 따른다. 기존 설치/GUI 설정은 유지했다. 메모·archive 형식을 파괴적으로 이전하거나 기존 데이터를 삭제하는 마이그레이션은 없다.
