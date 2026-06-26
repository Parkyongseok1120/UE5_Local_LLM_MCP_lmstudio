# Process State Result Pattern

## 검색 키워드

process owner, state owner, result resolver, mutation owner, event owner, success failure judgment, result application

## 목적

프로세스 진행, 결과 판정, 상태 변경을 분리한다. 성공/실패 판정을 반드시 Process Owner나 State Owner 중 하나로 고정하지 않는다.

## 기본 구조

| 책임 | 설명 |
|---|---|
| Process Owner | 진행률, 타이머, 취소, 재시도, 중간 상태를 관리 |
| State Owner | 실제 상태 값을 소유 |
| Result Resolver | 결과를 계산하거나 적용 가능 여부를 판단 |
| Mutation Owner | 최종 상태 변경을 수행 |
| Event Owner | 프로세스 완료 또는 상태 변경 이벤트를 발생 |

## Result Resolver Decision Rule

성공/실패 판정은 다음 데이터를 조합할 수 있다.

- Initiator의 능력치
- Target의 저항값
- 현재 상태
- 외부 환경
- 난수/확률
- 난이도 설정
- 디자인 규칙

금지되는 단정:

- 성공/실패는 무조건 Target이 판단한다.
- 성공/실패는 무조건 Performer가 판단한다.
- State Owner가 상태를 소유하므로 성공/실패도 무조건 State Owner가 판단한다.

## 답변 요구사항

성공/실패 판정을 말할 때는 다음 중 하나로 분류하고 이유를 설명한다.

- Performer-owned resolution
- Target-owned resolution
- Shared resolution
- Separate Resolver / System-owned resolution

## 이벤트 분리

프로세스 이벤트는 Process Owner가 발생시킨다. 상태 이벤트는 State Owner가 발생시킨다.
