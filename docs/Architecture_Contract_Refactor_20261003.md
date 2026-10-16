# 계약·책임·수명 리팩토링 구현 기록 — 2026-10-03

대상은 `UE5_Local_LLM_MCP_lmstudio`의 기존 작업 트리다. 기준 커밋은 `6893493`이며, 그 위에 먼저 진행된 Auto·모델 행동 개선 변경을 유지한 상태에서 [구조 감사](Architecture_Contract_Audit_20261003.md)의 경계를 보정했다.

## 1. 지켜야 하는 조건

- 실행 결과, 현재 관찰 상태, 응답 전달 상태, 영속 저장 상태를 서로 구분한다.
- 취소를 요청했다는 사실로 실행이 없었다거나 OS 프로세스가 종료됐다고 단정하지 않는다.
- 이미 적용하거나 게시한 결과는 후속 receipt·원장·cleanup·응답 축약 실패로 `not_applied`가 되지 않는다.
- 프로젝트·목표·도구 교환·관찰 시점의 출처가 확인된 근거만 현재 입력에 연결한다.
- 기존 담당자가 생성, 실행, 취소, 복구, 종료를 끝까지 관리한다.
- Auto와 guidance는 기존 예산 안에서 참고 자료를 선택한다. 이번 리팩토링에 추가 모델 호출, 자율 실행 에이전트, 필수 static Validate, 자동 빌드 재시도는 없다.

## 2. 책임과 변경 범위

| 담당 영역 | 기존 소유자와 유지한 책임 | 이번 보정 | 감사 항목 |
|---|---|---|---|
| 판단 note | PredictionLoop / note store / WorkingContext | 교체·clear를 window 저장 상태와 동기화하고 복구 시 목표·프로젝트·첨부 조건을 공통 적용 | A01 |
| 도구 교환 | 순수 `tool-exchange-index.js` + 소비자별 projection | 분리된 요청 묶음, 역순 결과, 중복·고아 결과, 메시지 경계를 같은 규칙으로 해석 | A02 |
| 파일 근거 | read producer / file observation / memory / reference | 읽기 불가와 검색의 부분 coverage를 전달하고 과거 수정 사실과 현재 근거를 구분 | A03, A11 |
| 요약과 저장 | WorkingContext / EvidenceArchive | 의미 본문을 먼저 투영하고 보존 window의 참조와 활성 lineage를 고려해 정리 | A04, A05 |
| headless 입력 | 기존 deterministic compaction core + adapter | 긴 현재 turn 압축, assistant 판단의 역할 보존, 압축 후 예산 재검사 | A06 |
| 프로젝트 문맥 | reference adapter | Unity 프로젝트 루트의 정확한 설정 파일만 엔진·패키지 설정 근거로 사용 | A07 |
| Unity 응답 | Node runtime / Bridge / Operations | 실행 결과와 전달·원장 저장 실패 분리, 과거 origin과 현재 delivery 분리 | A08, A16 및 추가 원장 경계 |
| 실행 수명 | MCP runtime / bounded runner / Unity Symbols | request signal과 연결 종료 전달, 종료 대기 상한, 늦은 완료의 상태 오염 방지 | A09, A10 |
| RAG 검색·신선도 | selection / history / collector / freshness | 실제 검색 조건 전체를 receipt key에 반영하고 프로젝트별 수집 근거 유지 | A12, A14 |
| 게시·설치 | generation transaction / package builder / installer | commit 이후 cleanup 경고 분리, 이전 package output의 backup·restore | A13, A15 |
| Unreal 수정·복구 | version policy / transaction recovery | commit 이후 receipt 실패 분리, 복구 대상의 고정된 물리 경로 재검증 | A17, A18 |
| RAG 요청 수명 | stdio inbox / operation / collector | 취소 알림 수신과 직렬 도메인 실행 분리, 수집 단계 deadline, 게시 경계 보호 | A19 |
| 문서 | 실제 설정·installer 계약 | 기본 Off, Auto, 기존 채팅 설치 동작의 설명 일치 | A20 |

이 표의 모듈 분리는 원본 상태를 여러 곳에서 독립적으로 결정하도록 만드는 방식이 아니다. 예를 들어 pairing은 순수 helper 한 곳에서 결정하고 reference와 memory는 각자 필요한 형식만 만든다. response projection은 실행 상태를 결정하지 않고 producer가 확인한 상태를 보존한다.

## 3. 사용자에게 보이는 계약 차이

### 적용 결과와 후속 처리

- Unity 실행이 `applied`로 끝난 뒤 응답 본문이 예산을 초과하면 실행 상태와 ID를 유지하고 `delivery.bodyOmitted`로 본문 생략을 알린다.
- Unity 실행 결과를 얻은 뒤 최종 원장 저장이 실패하면 결과와 origin을 유지하고 `journalPersistence.status = unavailable`을 표시한다. 이후 원장 재조회는 `outcome_unknown`일 수 있으며, 이를 근거로 같은 작업을 다시 실행하지 않는다.
- Unity recording metadata 저장이 실패하면 snapshot 담당자가 샘플링을 멈추고 실제 `stopped` 상태·recording ID·`metadataPersistence` 경고를 반환한다. 저장 오류가 Editor update tick 밖으로 전파되거나 이미 끝난 중지 작업을 `not_applied`로 바꾸지 않는다.
- Unreal 파일 수정 후 receipt 발급이 실패해도 적용 사실은 유지한다. 후속 수정에는 다시 읽어서 유효한 근거를 얻어야 한다.
- RAG generation 게시 이후 backup/journal cleanup만 실패하면 `stageCommitted`를 유지하고 cleanup 상태를 별도로 전달한다.

### 취소와 종료

- 전송·spawn 전 취소는 작업을 시작하지 않는다.
- Unity RPC를 전송한 뒤 응답을 받기 전에 연결이 끊기거나 취소되면 `outcome_unknown`이다. `operationId`가 없다는 이유로 미실행을 단정하지 않는다.
- Unity 동기 Editor 작업의 응답 대기를 취소하는 것은 이미 시작된 Editor 변경을 롤백한다는 뜻이 아니다.
- 프로세스 종료 요청, 부모 프로세스 종료 관찰, 자식 트리 전체 종료 확인은 서로 다른 수준이다. OS에서 관찰하지 않은 수준을 성공으로 표시하지 않는다.
- Unity symbol worker의 종료가 확인되지 않으면 실행 예약을 유지한다. 취소 뒤 늦게 나온 정상 출력도 새로운 index로 게시하지 않는다.
- RAG의 게시 시작 전에는 취소를 확인한다. 파일 교체 트랜잭션이 시작된 뒤에는 게시 또는 rollback 결과를 끝까지 정산한다.
- RAG transport는 취소 알림을 수신하고 도메인 handler는 직렬로 실행한다. EOF에서는 대기·실행 중 변경 요청을 취소하며, 이미 받은 읽기 요청은 처리할 수 있다. 비대화형 collector의 stdin은 `DEVNULL`로 분리한다.

### 현재 근거와 과거 기록

- 읽기에 실패한 파일은 현재 본문을 확인할 수 없는 상태다. 삭제됐다고 추론하지 않는다.
- 검색 중 읽지 못한 파일이 있으면 결과 0건을 전체 범위의 부재 증거로 사용하지 않는다.
- 저장 snapshot·operation의 기존 session/domain/time은 재조회해도 바뀌지 않는다.
- headless의 assistant 판단은 assistant 역할을 유지한다. system 지침으로 승격하지 않는다.

## 4. 저장 형식과 호환

| 대상 | 적용 시 동작 |
|---|---|
| Unity Bridge protocol | `2`. 저장 증거의 `origin`과 현재 RPC의 `delivery`를 구분한다. 새 adapter는 protocol 1도 읽을 수 있지만 이전 Bridge에는 origin 보존 보장이 없다. 이전 adapter는 새 Bridge를 discovery에서 거부하므로 adapter와 Bridge를 함께 갱신해야 한다. |
| Unity 구형 원장·snapshot | 기록된 origin을 재사용한다. 필드가 없으면 unknown을 유지하며 현재 세션 값을 과거 origin으로 채우지 않는다. |
| RAG receipt | key의 의미 버전을 바꾼다. 이전 조건으로 발급된 receipt가 새로운 검색을 억제하지 않는다. |
| RAG 구형 index | 프로젝트별 수집 provenance가 없으면 freshness는 unknown이다. 실제 해당 프로젝트를 다시 수집해야 새 근거가 생긴다. |
| RAG `stat-v1` provenance | 기존 수집기의 확장자·제외 정책을 재사용한 collector 입력 전체의 파일 목록·크기·mtime·ctime snapshot이다. C++ 소스 외의 프로젝트 텍스트·asset 경로 metadata 입력도 포함한다. 전체 내용 hash가 아니며 파일시스템 시각 정보를 보존한 변경까지 증명하지 않는다. 탐색 제한이나 IO 문제로 불완전하면 fresh로 승격하지 않는다. |
| archive 보존 | 기존 backlog는 파일명·stat 순회 후 크기·수·시각으로 먼저 정리한다. 보존할 manifest의 본문만 읽는다. active/parent 보호로 soft quota를 초과할 수 있고, 링크·알 수 없는 파일이 있는 scope는 자동 삭제하지 않는다. |
| package output | 같은 부모 디렉터리에 backup을 만든 뒤 새 output을 게시한다. 게시 실패 시 복원하고 복원 실패 시 복구 경로를 보존한다. |

설정 기본값이나 사용자 프로젝트의 소스는 이번 작업에서 변경하지 않았다. 이 기록은 저장소 구현에 관한 것이며 설치된 LM Studio plugin과 실제 Unity 프로젝트가 갱신됐다는 뜻은 아니다.

## 5. A18 감사 정정

기존 경로 containment helper는 이미 realpath를 사용하므로 **프로젝트 밖을 가리키는 junction 교체는 기존 코드도 거부했다.** 그 보호를 생략한 fake filesystem 재현으로 외부 경계 우회를 주장해서는 안 된다.

실제 Windows 임시 디렉터리와 junction으로 확인한 결함은 **같은 프로젝트 안에서 다른 물리 대상으로 바뀐 경로**였다. 대상 내용의 hash까지 같으면 기존 복구가 그 다른 파일에 backup을 쓸 수 있었다. 이번 보정은 저널의 고정된 canonical target과 현재 target을 다시 비교한다. `locksHeld` 양쪽 경로와 프로젝트 내부·외부 교체를 회귀 테스트로 다룬다.

## 6. 검증과 적용 범위

검증 명령, 결과, 생략 사유와 근거 분류는 [구현 근거 packet](evidence/architecture-contract-refactor-20261003.json)에 기록한다. 이전 감사의 재현 스크립트는 수정 전 결함을 확인하는 자료이므로 제품 회귀 테스트의 성공 판정으로 사용하지 않는다.

Compactor는 전체 497개 통과 후 마지막 보정을 관련 회귀 30개·33개 및 실제 handler 2개·계약 8개로 재검증했고 최종 TypeScript build도 성공했다. 이 숫자는 서로 겹치므로 합산하지 않는다. Unreal은 전체 271개 중 270개 통과·플랫폼 skip 1개, Unity Node는 전체 50개 중 46개 통과·환경 skip 4개다. Unity의 후속 persistence·worker 보정은 해당 경계를 다시 실행했다. Headless 및 실제 C# owner의 격리 계약 테스트는 6개 통과했다.

Unity Editor·Runtime 소스 18개는 설치된 Unity 6000.3.14f1의 DLL 149개를 참조한 격리 컴파일에서 오류·경고 0개였다. 이는 실제 Editor를 실행하거나 domain reload/PlayMode를 검증한 결과와 구분한다.

RAG·패키지·설치 관련 통합 회귀는 237개 통과했다. 마지막 freshness 입력 범위 보정 후 관련 회귀와 열린 stdio 연결의 packaged refresh/search 58개, 최종 응답 예산·import·CI·신규 회귀 45개도 통과했다. 실행 범위가 겹치므로 합산하지 않는다. CI 구성은 4개 suite에 중복 없는 테스트 파일 59개가 등록된 것을 검증했다.

이전 실패는 고정 revision/CI 개수 기대값, EOF 계약을 반영하지 않은 fixture, 그리고 live collector 응답 대기로 구분했다. collector stdin을 `DEVNULL`로 분리하고 fixture stderr를 병렬 수집한 뒤 같은 live smoke가 통과했다. Windows 내부 blocking 메커니즘은 계측하지 않았으며, 두 변경이 함께 들어간 전후 비교를 하나의 원인에 대한 독립 증명으로 취급하지 않는다.

실제 LM Studio 모델 응답의 품질 개선 폭, Editor 재시작·domain reload·PlayMode에서의 최종 동작, 대형 RAG corpus의 성능은 별도 실행 근거가 필요하다. 이번 변경은 모델이 받는 근거와 실행 계약의 일관성을 보정하며 모델의 모든 판단을 보증하지 않는다.

## 7. 남은 검증 산출물 정리

이번 테스트에서 생성한 `artifacts/architecture-audit-20261003/pytest-rag-refactor-temp` 삭제는 경로 확인 뒤에도 자동 승인 검토가 `blocked by policy`로 거부했다. 우회 삭제는 하지 않았고 폴더를 남겼다. 제품 구현·검증 실패가 아니라 임시 산출물 정리 제한이다. 보고서·로그·기존 사용자 파일은 삭제하지 않았다.
