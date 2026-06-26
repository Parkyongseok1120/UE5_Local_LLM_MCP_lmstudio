# Narrative Rhythm Racing Tower Defense Prototype Adapters

## Narrative Adventure Prototype Adapter

### 핵심 재미

선택, 대화, 탐색이 의미 있는 결과로 이어지는지 검증한다.

### 핵심 루프

Explore -> Talk/Choose -> Update State -> Unlock/Branch -> Continue

### Must Have

- 대화 시스템
- 선택지
- 플래그/상태 저장
- 간단한 분기
- 상호작용 오브젝트

### 추천 책임

| 시스템 | 책임 | 가지면 안 되는 책임 |
|---|---|---|
| DialogueSystem | 대화 흐름 | 월드 상태 직접 변경 |
| ChoiceResolver | 선택 결과 적용 | UI 렌더링 |
| StoryStateComponent | 플래그 SSOT | 대화 텍스트 관리 |
| InteractionComponent | NPC/오브젝트 상호작용 | 스토리 분기 직접 소유 |
| QuestComponent | 목표 진행도 | 대화 UI 처리 |

### 위험

- 대화 텍스트와 조건 로직이 섞임
- 스토리 플래그가 여러 곳에 분산됨
- 선택지가 실제 결과를 만들지 않음

## Rhythm Game Prototype Adapter

### 핵심 재미

음악 타이밍에 맞춘 입력 판정과 피드백이 정확하고 즐거운지 검증한다.

### 핵심 루프

Read Note -> Input -> Timing Judge -> Feedback -> Score/Combo

### Must Have

- 음악 재생
- 노트 스폰
- 입력 타이밍 판정
- 점수/콤보
- 판정 피드백

### 추천 책임

| 시스템 | 책임 | 가지면 안 되는 책임 |
|---|---|---|
| MusicClock | 시간 기준 SSOT | 점수 계산 |
| NoteSpawner | 노트 생성 | 입력 판정 |
| InputJudge | 입력 타이밍 판정 | 음악 재생 제어 |
| ScoreComponent | 점수/콤보 SSOT | 노트 생성 |
| FeedbackComponent | 판정 연출 | 판정 결과 계산 |

### 위험

- 프레임 시간으로만 판정
- 음악 시간과 게임 시간이 분리되지 않음
- 점수와 판정이 UI에 들어감

## Racing Prototype Adapter

### 핵심 재미

가속, 조향, 감속, 코너링의 조작감과 속도감을 검증한다.

### 핵심 루프

Accelerate -> Steer -> Corner -> Recover -> Overtake/Finish

### Must Have

- 차량 이동
- 조향
- 체크포인트
- 랩 또는 목표 지점
- 속도 피드백

### 추천 책임

| 시스템 | 책임 | 가지면 안 되는 책임 |
|---|---|---|
| VehicleMovement | 물리/조향 | 랩 판정 |
| RaceRuleManager | 체크포인트/랩 | 차량 물리 직접 조작 |
| CheckpointComponent | 통과 감지 | 전체 경기 상태 직접 결정 |
| CameraRig | 속도감/추적 카메라 | 차량 입력 처리 |
| FeedbackComponent | 속도선, 사운드, 진동 | 랩 상태 소유 |

### 위험

- 차량 조작감보다 트랙/콘텐츠를 먼저 만듦
- 체크포인트 상태가 차량마다 분산됨
- 물리와 승패 판정이 섞임

## Tower Defense Prototype Adapter

### 핵심 재미

제한된 자원으로 방어 배치를 선택하고, 적 웨이브를 막는 재미를 검증한다.

### 핵심 루프

Build -> Wave Spawn -> Towers Attack -> Enemies Advance -> Earn/Upgrade

### Must Have

- 경로
- 적 웨이브
- 타워 1~2종
- 자원
- 기지 체력
- 업그레이드 1개

### 추천 책임

| 시스템 | 책임 | 가지면 안 되는 책임 |
|---|---|---|
| WaveManager | 웨이브 스폰 | 타워 공격 처리 |
| TowerComponent | 타겟 탐색/공격 요청 | 자원 차감 직접 처리 |
| EnemyUnit | 이동/체력 | 웨이브 생성 |
| ResourceComponent | 자원 SSOT | 타워 공격 판정 |
| BaseHealthComponent | 기지 체력 | 적 이동 처리 |

### 위험

- 타워가 자원과 웨이브까지 관리
- 웨이브 데이터가 코드에 하드코딩됨
- 적 경로와 AI가 과도하게 복잡해짐
