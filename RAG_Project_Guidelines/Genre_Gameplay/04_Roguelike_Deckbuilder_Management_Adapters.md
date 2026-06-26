# Roguelike Deckbuilder Management Prototype Adapters

## Roguelike Prototype Adapter

### 핵심 재미

짧은 반복 플레이에서 무작위 선택과 성장 조합이 재미있는지 검증한다.

### 핵심 루프

Enter Room -> Fight/Choose -> Gain Reward -> Build Synergy -> Next Room -> Fail/Win

### Must Have

- 방 3~5개
- 적 1~2종
- 보상 선택 2~3개
- 플레이어 스탯 변화
- 사망/재시작

### 추천 책임

| 시스템 | 책임 | 가지면 안 되는 책임 |
|---|---|---|
| RunManager | 현재 런 상태 | 개별 전투 로직 |
| RoomManager | 방 생성/전환 | 플레이어 스탯 직접 수정 |
| RewardSystem | 보상 후보 생성 | 아이템 효과 직접 실행 |
| StatComponent | 스탯 SSOT | 보상 UI 처리 |
| ModifierComponent | 효과 적용/제거 | 방 생성 관리 |

### 위험

- 랜덤 생성부터 과하게 만듦
- 보상 효과가 코드 곳곳에 흩어짐
- Run 상태와 영구 성장 상태가 섞임

## Deckbuilder Prototype Adapter

### 핵심 재미

카드를 선택하고, 조합하고, 자원 제한 안에서 최적 행동을 찾는 재미를 검증한다.

### 핵심 루프

Draw -> Choose Card -> Pay Cost -> Resolve Effect -> Discard/End Turn

### Must Have

- 카드 5~10장
- 드로우/핸드/덱/버림 pile
- 비용 시스템
- 카드 효과 3종
- 적 또는 목표 상태

### 추천 책임

| 시스템 | 책임 | 가지면 안 되는 책임 |
|---|---|---|
| DeckComponent | 덱/핸드/버림 상태 | 카드 효과 직접 해석 |
| CardData | 카드 비용/효과 데이터 | 런타임 상태 소유 |
| CardResolver | 카드 효과 실행 | UI 입력 처리 |
| TurnManager | 턴 흐름 | 카드 개별 효과 하드코딩 |
| TargetComponent | 대상 선택 | 카드 비용 검증 |

### 위험

- 카드 효과가 UI 버튼에 들어감
- 카드 데이터와 실행 로직이 섞임
- 턴 상태가 여러 곳에 분산됨

## Management Simulation Prototype Adapter

### 핵심 재미

제한된 자원을 배분하고, 시간이 지나며 결과가 누적되는 재미를 검증한다.

### 핵심 루프

Assign Resource -> Simulate Time -> Resolve Outcome -> Earn/Spend -> Expand

### Must Have

- 자원 2~3종
- 생산/소비 루프 1개
- 시간 진행
- 결과 피드백
- 간단한 업그레이드

### 추천 책임

| 시스템 | 책임 | 가지면 안 되는 책임 |
|---|---|---|
| EconomySystem | 자원 SSOT | UI 입력 처리 |
| ProductionUnit | 생산 규칙 | 전체 경제 직접 수정 |
| TimeSystem | 시간 진행 | 개별 생산 결과 판단 |
| UpgradeSystem | 업그레이드 적용 | 자원 UI 렌더링 |
| SimulationResolver | 틱 결과 계산 | 플레이어 입력 처리 |

### 위험

- 모든 자원을 전역 변수로 둠
- 시간 진행과 UI가 섞임
- 생산/소비 규칙이 하드코딩됨
