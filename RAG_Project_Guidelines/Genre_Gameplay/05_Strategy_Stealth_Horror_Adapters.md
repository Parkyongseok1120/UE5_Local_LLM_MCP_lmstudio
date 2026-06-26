# Strategy Stealth Horror Prototype Adapters

## Strategy Tactics Prototype Adapter

### 핵심 재미

유닛 선택, 명령, 위치, 상성 판단이 전략적 선택으로 이어지는지 검증한다.

### 핵심 루프

Select -> Command -> Move/Act -> Resolve Combat -> Reposition

### Must Have

- 유닛 선택
- 이동 명령
- 공격 명령
- 체력/피해
- 적 1종 또는 상대 유닛
- 간단한 승패 조건

### 추천 책임

| 시스템 | 책임 | 가지면 안 되는 책임 |
|---|---|---|
| SelectionComponent | 선택 상태 | 유닛 체력 변경 |
| CommandSystem | 명령 생성/전달 | 명령 결과 직접 강제 |
| Unit | 자기 상태/행동 수행 | 전체 게임 승패 판단 |
| CombatResolver | 공격 결과 계산 | UI 선택 처리 |
| GameRuleManager | 승패 조건 | 개별 유닛 이동 처리 |

### 위험

- PlayerController가 모든 유닛 로직을 직접 처리
- 선택 상태와 유닛 상태가 섞임
- 명령과 결과 적용이 분리되지 않음

## Stealth Prototype Adapter

### 핵심 재미

플레이어가 시야, 소리, 위치를 관리하며 들키지 않는 긴장감을 검증한다.

### 핵심 루프

Observe -> Move Carefully -> Avoid Detection -> Distract/Bypass -> Reach Goal

### Must Have

- 적 시야
- 플레이어 감지 상태
- 은폐/엄폐 요소
- 목표 지점
- 경고/발각 피드백

### 추천 책임

| 시스템 | 책임 | 가지면 안 되는 책임 |
|---|---|---|
| DetectionComponent | 시야/소리 감지 | 플레이어 이동 처리 |
| StealthStateComponent | 발각/경계 상태 | 전체 레벨 클리어 판단 |
| AIController | 순찰/추적 행동 | 감지 수치의 SSOT 중복 |
| NoiseEmitter | 소리 이벤트 발생 | AI 상태 직접 변경 |
| ObjectiveComponent | 목표 달성 조건 | 감지 로직 처리 |

### 위험

- AI가 플레이어 내부 상태를 직접 수정
- 시야/소리/경계 상태가 한 함수에 섞임
- 발각 피드백이 늦음

## Horror Prototype Adapter

### 핵심 재미

불확실성, 제한된 정보, 위험 예측으로 긴장감을 검증한다.

### 핵심 루프

Explore -> Notice Threat -> Hide/Run/Decide -> Survive -> Reveal

### Must Have

- 어두운 공간
- 위협 존재
- 제한된 시야 또는 자원
- 숨기/도망 중 1개
- 긴장 피드백

### 추천 책임

| 시스템 | 책임 | 가지면 안 되는 책임 |
|---|---|---|
| FearDirector | 긴장도 조절 | 플레이어 체력 직접 수정 |
| ThreatAI | 추적/배회 | 전체 연출 타이밍 단독 소유 |
| VisibilityComponent | 시야/조명 영향 | AI 행동 결정 |
| InteractionComponent | 문/아이템 상호작용 | 공포 이벤트 직접 강제 |
| AudioFeedback | 사운드 피드백 | 게임 규칙 판단 |

### 위험

- 점프스케어만 먼저 만듦
- 위협 AI와 연출 Director가 섞임
- 플레이어가 왜 죽었는지 알 수 없음
