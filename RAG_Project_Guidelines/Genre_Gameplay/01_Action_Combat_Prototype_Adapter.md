# Action Combat Prototype Adapter

## 적용 조건

3인칭 액션, 근접 전투, 갓오브워풍, 소울라이크, DMC풍, 콤보 액션, 보스전 프로토타입.

## 핵심 재미

플레이어가 적의 공격을 읽고, 공격/회피/방어/패리/스태거를 주고받는 손맛을 검증한다.

## 핵심 루프

Approach -> Target -> Attack/Defend -> Hit/Stagger -> Reposition -> Finish

## 최소 프로토타입 범위

Must Have:

- 3인칭 카메라
- 이동
- 라이트/헤비 공격
- 회피 또는 방어
- 적 1종
- 피격 반응
- 스태거 또는 경직
- 히트 피드백

Should Have:

- 락온 또는 소프트 타겟
- 간단한 콤보 1개
- 카메라 쉐이크 / 히트스톱

Later:

- 성장, 장비, 스킬트리, 보스 패턴 다수, 복잡한 AI Director

## 추천 책임 분리

| 시스템 | 책임 | 가지면 안 되는 책임 |
|---|---|---|
| Character | 이동, 카메라 기준 회전 | 콤보 전체 관리, 데미지 최종 적용 |
| CombatComponent | 공격 요청, 콤보, 입력 버퍼, 공격 상태 | 적 체력 직접 감소 |
| AttackTraceComponent | 공격 판정, 이미 맞은 대상 기록 | 데미지 최종 판정 |
| HealthComponent | 체력 SSOT, ApplyDamage, 사망 이벤트 | 공격 입력 처리 |
| StaggerComponent | 경직/스태거 SSOT | 플레이어 입력 처리 |
| TargetingComponent | 락온/소프트 타겟 | 적 상태 변경 |
| FeedbackComponent | 히트스톱, 카메라 쉐이크, VFX/SFX 요청 | 실제 데미지 계산 |

## 상태 / 프로세스 SSOT

| 상태 / 프로세스 | SSOT |
|---|---|
| 공격 입력/콤보 진행 | CombatComponent |
| 공격 판정 중복 히트 기록 | AttackTraceComponent |
| 체력 | HealthComponent |
| 스태거/경직 | StaggerComponent |
| 타겟 선택 | TargetingComponent 또는 PlayerController |

## 위험 요소

- Character God Object
- Timer 기반 공격 판정만 사용
- 콤보를 애니메이션/피드백보다 먼저 복잡하게 만듦
- 피드백을 후순위로 미룸

## 첫 구현 단위

작은 테스트 맵에서 플레이어 1명, 적 1명, 라이트 공격 1개, 회피 1개, 피격/스태거 피드백 1개를 반복 검증한다.
