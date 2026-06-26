# SSOT and Responsibility Rules

## 목적

Unreal 프로젝트에서 상태와 책임이 여러 클래스에 흩어지면 AI가 생성한 코드는 빠르게 꼬인다. 이 문서는 어떤 객체가 어떤 상태를 소유해야 하는지, 그리고 Single Source of Truth를 어떻게 지킬지 정한다.

검색 키워드: SSOT, single source of truth, responsibility, ownership, GameMode, GameState, PlayerState, Component, Subsystem, DataAsset, SaveGame, Replication

## 기본 규칙

1. 모든 상태에는 원본 소유자가 하나만 있어야 한다.
   - 변경 가능한 런타임 상태는 반드시 소유자와 변경 API를 가진다.
   - 다른 객체는 원본을 조회하거나 이벤트를 구독한다.
   - 복제, UI 표시, 캐시는 원본이 아니다.

2. 상태 변경은 소유자를 통해서만 한다.
   - 외부 객체가 UPROPERTY를 직접 수정하게 두지 않는다.
   - `Set`, `Try`, `Request`, `Apply` 같은 명령 API를 통해 검증과 이벤트 발행을 한 곳에 모은다.
   - public mutable 배열, 맵, 구조체 참조를 그대로 반환하지 않는다.

3. UI와 입력은 상태 소유자가 아니다.
   - Widget은 표시와 사용자 의도 전달만 담당한다.
   - PlayerController/InputComponent는 입력을 명령으로 변환한다.
   - 실제 게임 규칙과 상태 판정은 Pawn, Component, Ability, GameState, PlayerState 등 도메인 소유자가 한다.

4. DataAsset과 Config는 기본값의 원본이다.
   - 런타임 중 변하는 값은 DataAsset 자체에 쓰지 않는다.
   - 인스턴스별 변경값은 Component, Actor, SaveGame, PlayerState 등에 둔다.
   - 튜닝 데이터는 코드 상수보다 DataAsset 또는 Config를 우선한다.

5. 복제 상태의 원본은 권한 소유자다.
   - 서버 권한 게임플레이에서는 서버 상태가 원본이다.
   - OnRep 함수는 클라이언트 표시 동기화용이며 원본 판정을 다시 수행하지 않는다.
   - 클라이언트 예측이 필요하면 예측값과 서버 확정값을 이름과 흐름으로 분리한다.

## Unreal 클래스별 책임 기준

| 위치 | 주 책임 | 피해야 할 것 |
| --- | --- | --- |
| GameMode | 서버 전용 경기 규칙, 스폰 규칙, 승패 판정 | 클라이언트 UI 상태, 복제되어야 하는 점수 표시 |
| GameState | 모든 클라이언트가 알아야 하는 경기 상태 | 플레이어 개인 입력, 로컬 옵션 |
| PlayerState | 플레이어별 점수, 팀, 장기 상태, 복제 상태 | Pawn 수명에 묶인 임시 물리 상태 |
| PlayerController | 입력 변환, possession, 로컬 UI 연결 | 데미지, 인벤토리, 스탯의 원본 소유 |
| Pawn/Character | 이동, 물리적 표현, Pawn 수명 상태 | 계정/매치 전체에 지속되는 데이터 |
| ActorComponent | 특정 Actor에 붙는 재사용 기능과 그 기능의 상태 | 전역 싱글턴 역할, unrelated feature 묶음 |
| WorldSubsystem | 월드 단위 서비스, 월드 수명 캐시 | 플레이어별 개인 상태 |
| GameInstanceSubsystem | 프로세스/세션 단위 서비스 | 월드 Actor 직접 장기 보관 |
| DataAsset | 불변 튜닝 데이터와 규칙 정의 | 런타임 변경 상태 저장 |
| SaveGame | 디스크 저장 대상 스냅샷 | 라이브 UObject 참조 직접 저장 |
| Widget | 표시, 사용자 액션 전달, 애니메이션 | 게임플레이 상태 원본 소유 |

## SSOT 결정 질문

- 이 값은 누가 최종 판정하는가?
- 이 값은 네트워크로 복제되는가?
- 이 값은 저장되어야 하는가?
- 이 값은 Pawn 사망/리스폰 후에도 유지되는가?
- 이 값을 UI, 애니메이션, 오디오가 어떻게 알게 되는가?
- 캐시가 필요하다면 언제 무효화되는가?

## Transient Selection Ownership

`ActiveTarget`, `HoveredTarget`, `FocusedInteractable`처럼 로컬 입력과 UI 선택에 묶인 임시 참조는 PlayerController가 소유해도 된다. PlayerController가 입력을 명령으로 변환하고 현재 대상 참조를 중재하는 것은 프로토타입에서 허용되는 단순 구조다.

다음 조건이 생기면 별도 TargetingComponent, Pawn/Character, PlayerState, 또는 복제 상태로 분리하는 것을 검토한다.

- 대상 선택이 네트워크로 복제되어야 한다.
- Pawn 사망, possession 변경, 리스폰 후에도 유지되어야 한다.
- Weapon, 보조 캐릭터, UI, AI 등 여러 시스템이 같은 targeting 규칙과 우선순위를 공유한다.
- line trace, lock-on 유지, 우선순위 계산, 가시성 판정이 커져서 독립 테스트가 필요하다.
- 저장/로드 또는 리플레이에서 대상 선택 상태가 의미를 가진다.

무조건 PlayerState나 Manager로 옮기라는 제안은 과설계일 수 있다. 먼저 값의 수명, 복제 여부, 공유 범위, 테스트 필요성을 기준으로 판단한다.

## Process SSOT and State SSOT

상태 SSOT와 프로세스 SSOT를 같은 객체로 가정하지 않는다.

- 상태 SSOT: 최종 상태 값을 소유하고 검증하는 객체다. 예: 대상의 Shield, Vulnerable, Health, Captured, Unlocked.
- 프로세스 SSOT: 진행률, 타이머, 시도 상태, 취소 가능성을 소유하는 객체다. 예: 수행자의 ActionProgress, WeaponComponent의 HeatCooldown.

Event 위치도 이 구분을 따른다.

- 프로세스 진행 알림은 프로세스 소유 객체가 발행할 수 있다.
- 상태 변경 알림은 상태 소유 객체가 발행한다.
- 한 객체가 다른 객체의 상태 delegate를 직접 Broadcast하지 않는다.

## 금지 규칙

- Health를 Character, Widget, PlayerState에 각각 따로 저장하지 않는다.
- Inventory를 UI 배열과 Component 배열로 이중 관리하지 않는다.
- Ability cooldown을 UI 타이머가 임의로 계산해서 원본처럼 쓰지 않는다.
- DataAsset에 런타임 획득 수량, 현재 탄약, 현재 체력 같은 값을 쓰지 않는다.
- 복제 변수와 로컬 변수를 같은 의미로 동시에 두지 않는다.
