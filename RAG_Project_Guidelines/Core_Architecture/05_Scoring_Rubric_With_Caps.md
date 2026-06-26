# Scoring Rubric With Caps

## 검색 키워드

scoring rubric, score cap, hard failure, self audit, design review score, quality gate

## 기본 평가 항목

| 항목 | 설명 |
|---|---|
| Responsibility Separation | 책임이 적절히 나뉘었는가 |
| State/Process Ownership | 상태와 프로세스 소유권을 구분했는가 |
| Interface Minimality | 인터페이스가 최소 계약인가 |
| API Intent Clarity | Mutation API가 의도를 드러내는가 |
| Event Ownership | 이벤트 발생 위치가 올바른가 |
| Implementation Safety | 도메인 구현 규칙과 충돌하지 않는가 |
| Risk Explanation Depth | 왜 위험한지 확장 관점까지 설명했는가 |
| Scope Control | 요청하지 않은 대형 구조를 넣지 않았는가 |

## 점수 제한 규칙

- 도메인 API 확실성 없음: 최대 8.0
- 코드 예시가 의사코드인데 컴파일 가능처럼 말함: 최대 7.5
- 요청하지 않은 네트워크/대형 구조 제안: 최대 6.5
- Interface에 구현체 전용 개념을 넣음: 최대 7.5
- State SSOT와 Process SSOT를 혼동함: 최대 7.0
- Event를 Interface에 넣음: 최대 7.0
- 일반 setter를 안전한 API처럼 제안함: 최대 7.5
- 자기모순이 있음: 최대 7.0

## 사용 규칙

자체 평가 점수는 보고용 장식이 아니다. 점수 제한에 걸리는 항목이 있으면 Final 답변 전에 수정한다.
