# Prototype Planning Failure Patterns

## 검색 키워드

prototype planning failure, production roadmap drift, feature copy, MVP scope, first playable loop, network first prototype, reference game planning

## 목적

프로토타입 계획 답변이 상용 게임 전체 로드맵처럼 부풀어 오르는 문제를 막는다.

사용자가 "A 같은 게임을 만들고 싶다", "프로토타입 계획을 세워 달라"고 말하면, 답변은 먼저 First Playable Loop를 설계해야 한다. 장기 로드맵, 네트워크 구조, 상용 기능 목록은 사용자가 명시적으로 요구했을 때만 확장한다.

## Failure Pattern 1: Production Roadmap Drift

증상:

- 1~2주 프로토타입이 아니라 6~10주 이상 장기 개발 계획부터 제시한다.
- Phase 1부터 전체 매치 구조, 대형 맵, 복잡한 UI, 네트워크, 최적화를 포함한다.
- "작동 가능한 작은 장면" 대신 "상용 게임 기능 목록"을 계획한다.

수정:

- 첫 답변은 30초 안에 반복 가능한 테스트 씬으로 줄인다.
- 장기 로드맵은 "나중에 확장" 섹션으로 밀어낸다.
- Phase 1은 조작, 핵심 상호작용, 즉각 피드백, 성공/실패 조건만 포함한다.

## Failure Pattern 2: Reference Feature Copy

증상:

- 레퍼런스 게임의 대표 기능을 Must Have로 그대로 복사한다.
- "레퍼런스에 있으니까"라는 이유로 무기 다수, 차량, 낙하산, 매치메이킹, 랭킹, 인벤토리, 대형 맵을 넣는다.
- 핵심 재미가 아니라 기능 수로 유사성을 만들려고 한다.

수정:

- 레퍼런스를 기능 목록이 아니라 핵심 압박, 핵심 선택, 즉각 피드백으로 추상화한다.
- Must Have는 3~5개로 제한한다.
- 레퍼런스의 상징적 기능이라도 첫 루프 검증에 필요 없으면 Should Have 또는 Later로 보낸다.

## Failure Pattern 3: Network-First Prototype

증상:

- 사용자가 멀티플레이를 요구하지 않았는데 Replication, RPC, client prediction, reconciliation, interest management를 Phase 1~2에 넣는다.
- 총 쏘는 맛, 피격 피드백, 생존 압박을 검증하기 전에 서버 권한 구조를 설계한다.
- 네트워크 위험이 핵심 재미 검증 위험보다 먼저 나온다.

수정:

- 첫 프로토타입은 로컬, 싱글플레이, 봇, 더미 타겟, 시뮬레이션으로 핵심 루프를 검증한다.
- 네트워크는 해당 장르의 최종 제품에 중요하더라도 "확장 단계"로 분리한다.
- 멀티플레이를 반드시 말해야 한다면 "아직 구현하지 말고 나중에 서버 권한으로 옮길 상태"만 짧게 표시한다.

## Failure Pattern 4: Framework-First Planning

증상:

- Lyra, GAS, 대형 Manager, Ability System, 복잡한 모듈 구조를 핵심 루프보다 먼저 제안한다.
- "레퍼런스 코드가 있으니 따라간다"가 설계 근거가 된다.
- 프로토타입 첫날 구현 단위가 플레이 가능한 장면이 아니라 프레임워크 세팅이 된다.

수정:

- 프레임워크는 핵심 루프 검증 뒤에 선택한다.
- 첫 구현 단위는 "플레이어가 조작하고 결과를 즉시 보는 장면"이어야 한다.
- GAS, Lyra식 구조, 대형 매니저는 사용자가 요구하거나 복잡도가 실제로 필요해졌을 때만 도입한다.

## Failure Pattern 5: Wrong Risk Priority

증상:

- 초기 위험을 네트워크 부하, 대규모 맵 최적화, 안티치트, 라이브 서비스로 잡는다.
- 정작 첫 프로토타입의 위험인 조작감, 피드백, 반복 루프, 범위 폭발, 구현 순서 문제를 놓친다.

수정:

- 위험 요소는 먼저 "첫 주에 재미 검증을 막는 위험"을 적는다.
- 이후 "제품화 단계의 위험"을 별도 섹션으로 짧게 분리한다.

## Failure Pattern 6: Contradictory MVP

증상:

- "부활 불가"라고 적고 Phase 1에 리스폰을 넣는다.
- "프로토타입은 단순하게"라고 말하면서 Must Have가 10개 이상이다.
- "GAS는 과하다"고 말하고 앞에서는 GAS를 핵심 레퍼런스로 삼는다.

수정:

- 최종 답변 전 Must Have, Phase 1, 제외 항목이 서로 충돌하지 않는지 확인한다.
- 같은 기능을 Must Have와 Later 양쪽에 넣지 않는다.
- 금지/제외한 기술을 구현 계획의 핵심으로 다시 제안하지 않는다.

## Generic Self-Audit

계획 답변 전 다음을 확인한다.

- 이 답변이 Production Roadmap이 아니라 First Playable Loop인가?
- Must Have가 3~5개 안에 들어오는가?
- 레퍼런스 기능을 복사하지 않고 핵심 압박/선택/피드백으로 압축했는가?
- 사용자가 요구하지 않은 Multiplayer, Replication, RPC, GAS, 대형 Manager를 초기 계획에 넣지 않았는가?
- Phase 1이 30초 안에 반복 가능한 테스트 씬인가?
- 위험 요소가 첫 재미 검증을 막는 문제부터 다루는가?
