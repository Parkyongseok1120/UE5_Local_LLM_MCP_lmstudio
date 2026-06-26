# Platformer Puzzle Survival Prototype Adapters

## Platformer Prototype Adapter

### 핵심 재미

이동, 점프, 착지, 장애물 회피의 조작감이 좋은지 검증한다.

### 핵심 루프

Move -> Jump -> Land -> Avoid/Collect -> Continue

### Must Have

- 좌우 이동
- 점프
- 착지 판정
- 발판
- 낙사 또는 리셋
- 수집물 1종

### 추천 책임

| 시스템 | 책임 | 가지면 안 되는 책임 |
|---|---|---|
| CharacterMovement | 이동/점프 물리 | 점수 관리 |
| PlayerCharacter | 입력을 이동에 전달 | 레벨 상태 관리 |
| CollectibleComponent | 수집 가능 여부 | 전체 점수 UI 관리 |
| CheckpointComponent | 리스폰 위치 | 플레이어 이동 처리 |
| LevelRuleManager | 낙사/클리어 조건 | 개별 캐릭터 물리 처리 |

### 위험

- 점프감을 수치화하지 않음
- 체크포인트와 플레이어 상태가 섞임
- 수집물, 점수, UI가 한 객체에 몰림

## Puzzle Prototype Adapter

### 핵심 재미

플레이어가 규칙을 이해하고, 실험하고, 해결했을 때 납득 가능한지 검증한다.

### 핵심 루프

Observe -> Interact -> Rule Reaction -> Hypothesis -> Solve

### Must Have

- 상호작용 오브젝트
- 퍼즐 규칙 1개
- 성공/실패 피드백
- 리셋 기능
- 클리어 조건

### 추천 책임

| 시스템 | 책임 | 가지면 안 되는 책임 |
|---|---|---|
| PuzzleObject | 개별 상태 보유 | 전체 퍼즐 성공 판단 |
| PuzzleRuleComponent | 규칙 판정 | 플레이어 입력 처리 |
| PuzzleManager | 전체 클리어 조건 | 개별 오브젝트 내부 상태 직접 수정 |
| InteractionComponent | 상호작용 요청 | 퍼즐 결과 강제 변경 |
| FeedbackComponent | 성공/실패 연출 | 퍼즐 상태 소유 |

### 위험

- 퍼즐 규칙이 UI나 Player에 들어감
- 개별 오브젝트가 전체 퍼즐을 과도하게 앎
- 리셋 상태가 분산됨

## Survival Crafting Prototype Adapter

### 핵심 재미

자원을 찾고, 위험을 관리하고, 제작으로 선택지가 늘어나는 흐름을 검증한다.

### 핵심 루프

Explore -> Gather -> Manage Needs -> Craft -> Survive/Expand

### Must Have

- 자원 2~3종
- 인벤토리 최소 구현
- 제작 레시피 1~2개
- 허기/체력/스태미나 중 1개
- 위험 요소 1개

### 추천 책임

| 시스템 | 책임 | 가지면 안 되는 책임 |
|---|---|---|
| InventoryComponent | 아이템 보유 SSOT | 제작 규칙 전체 판단 |
| ResourceNode | 채집 가능 상태 | 플레이어 인벤토리 직접 조작 |
| CraftingComponent | 레시피 검증, 제작 요청 | 자원 노드 상태 관리 |
| NeedComponent | 허기/스태미나/체온 상태 | 인벤토리 UI 처리 |
| WorldInteractionComponent | 채집/상호작용 요청 | 아이템 데이터 소유 |

### 위험

- 인벤토리, 제작, 자원, UI가 한 클래스에 몰림
- 레시피가 코드에 하드코딩됨
- 생존 수치가 너무 많아짐
