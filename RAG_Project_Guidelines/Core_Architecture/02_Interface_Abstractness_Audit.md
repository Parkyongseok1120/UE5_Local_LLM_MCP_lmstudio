# Interface Abstractness Audit

## 검색 키워드

interface abstraction, capability interface, implementation detail, contract pollution, over-specific interface, minimal interface, optional query

## 목적

인터페이스는 특정 구현체의 내부 개념이 아니라, 외부에서 기대할 수 있는 최소 계약을 표현해야 한다.

## 필수 검사

인터페이스에 함수를 넣기 전에 각 함수마다 다음을 검사한다.

| 함수 | 모든 구현체에 자연스러운가? | 특정 구현체 전용 개념인가? | 없으면 인터페이스가 불가능한가? | 별도 인터페이스로 뺄 수 있는가? | 최종 판단 |
|---|---|---|---|---|---|

## 제거 규칙

다음 중 하나라도 해당하면 기본 인터페이스에서 제거한다.

- 특정 구현체의 내부 상태를 노출한다.
- 특정 게임/프로젝트의 세부 시스템을 전제로 한다.
- 일부 구현체는 억지로 더미 값을 반환해야 한다.
- UI 편의를 위해 추가된 Query다.
- 더 작은 인터페이스로 분리할 수 있다.

## 출력 규칙

설계 리뷰 답변에서 인터페이스를 제안할 때는 다음을 포함한다.

- 최소 인터페이스
- 제외한 함수
- 제외 이유
- 필요 시 분리할 보조 인터페이스

## 핵심 규칙

Capability interface에는 capability의 최소 계약만 둔다. 구현체 전용 상태는 별도 interface, component, concrete class query로 분리한다.
