# Core Responsibility Decomposition

## 검색 키워드

responsibility separation, ownership, actor, initiator, target, receiver, process owner, state owner, result resolver, mutation owner, event owner, API boundary

## 목적

모든 설계 리뷰는 먼저 책임을 분해한다. 특정 엔진, 장르, 프로젝트 이름보다 아래 역할을 먼저 식별한다.

| 항목 | 설명 |
|---|---|
| Actor / Initiator | 행동을 시작하는 주체 |
| Target / Receiver | 행동의 영향을 받는 대상 |
| State Owner | 변경 가능한 상태를 소유하는 객체 |
| Process Owner | 진행률, 타이머, 중간 상태, 취소 가능성을 소유하는 객체 |
| Result Resolver | 성공/실패, 결과량, 적용 가능 여부를 판정하는 객체 또는 규칙 |
| Mutation Owner | 최종 상태 변경을 수행하는 객체 |
| Event Owner | 상태 변경 또는 프로세스 완료 이벤트를 발생시키는 객체 |
| External API | 외부에서 호출 가능한 안전한 함수 |
| Forbidden Data | 외부에서 직접 수정하면 안 되는 데이터 |

## 핵심 원칙

State Owner와 Process Owner는 다를 수 있다.

예:

- 공격 프로세스는 Weapon이 소유할 수 있고, 체력 상태는 Target이 소유할 수 있다.
- 다운로드 프로세스는 Downloader가 소유할 수 있고, 파일 상태는 FileRecord가 소유할 수 있다.
- 채널링 프로세스는 Initiator가 소유할 수 있고, 활성화 상태는 Target이 소유할 수 있다.

Process Owner가 Target의 내부 상태를 직접 수정하면 안 된다. Process Owner는 Target의 public API 또는 interface를 호출해야 한다.
