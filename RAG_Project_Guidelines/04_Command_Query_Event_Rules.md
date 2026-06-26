# Command Query Event Rules

## Critical Rule: Prefer Intent-Revealing Mutation APIs

일반 setter인 `SetX(Value)`는 기본값으로 제안하지 않는다. 상태 변경 API는 왜 바뀌는지와 어떤 검증을 거치는지가 이름에 드러나야 한다.

검색 키워드: mutation API, avoid generic setter, ApplyDamage, RestoreShield, ConsumeAmmo, Request, Try, Command naming

우선 제안:

- `ApplyDamage`, `RestoreShield`, `ConsumeAmmo`, `ReloadMagazine`
- `ApplyActionAttempt`, `ResolveActionAttempt`, `RequestInteract`
- `TrySpendResource`, `GrantReward`, `EquipItem`, `UnequipItem`
- `StartCapture`, `CancelCapture`, `CompleteObjective`

피해야 할 기본 형태:

- `SetHealth(NewHealth)`
- `SetShield(NewShield)`
- `SetAmmo(NewAmmo)`
- `SetIsHacked(true)`
- `SetQuestState(State)`
- `SetTargetHealth(NewHealth)`

예외:

- 단순 설정값, editor-only tuning value, initialization-only value, DTO 변환처럼 도메인 규칙이 없는 경우는 setter를 쓸 수 있다.
- setter를 쓰더라도 외부에서 원본 상태를 마음대로 덮어쓸 수 없게 접근 범위와 호출 시점을 제한한다.
- 디버그/에디터 테스트용 setter는 `Debug_SetX` 또는 `EditorOnly_SetX`처럼 목적을 이름에 표시한다. 이 API를 일반 런타임 gameplay mutation으로 제안하지 않는다.
- Gameplay Ability System의 `GAMEPLAYATTRIBUTE_VALUE_SETTER`나 `ATTRIBUTE_ACCESSORS`로 생성되는 `SetHealth`류 함수는 AttributeSet 내부 지원 API로 본다. 이것을 외부 gameplay mutation API로 권장하지 말고, 실제 체력 변경은 GameplayEffect, damage execution, `ApplyDamage` 같은 의도 기반 흐름으로 설계한다.

검증:

- Mutation API가 authority, resource, cooldown, target validity, clamp, event broadcast를 한 곳에서 처리하는가?
- API 이름만 보고 상태가 바뀌는 이유를 알 수 있는가?
- UI나 외부 객체가 원본 상태를 `SetX`로 우회 변경하지 못하는가?

## External Command vs Internal Mutation

의도가 드러나는 이름이라도 외부에서 원본 상태를 검증 없이 바꿀 수 있으면 안전한 API가 아니다.

외부 API로 적합:

- `TryFire`
- `RequestAction`
- `ApplyActionAttempt`
- `ApplyDamageRequest`
- `ConsumeAmmoForShot`

내부 mutation으로 제한:

- `BreakShieldInternal`
- `BeginVulnerabilityInternal`
- `ApplyHeatFromShot`
- `CoolDownHeatInternal`

주의가 필요한 이름:

- `BreakShield`
- `AddHeat`
- `CoolDown`
- `AddShield`
- `RemoveHealth`

이 함수들이 public `BlueprintCallable`로 열리면 일반 setter와 같은 우회 변경 통로가 될 수 있다. 설계 리뷰에서는 외부 호출 API와 소유자 내부 mutation 단계를 분리해서 표기한다.

## Progress Action Command Direction

수행 주체가 프로세스를 소유한다면 `RequestX`는 수행 주체의 public API로 두는 편이 자연스럽다.

예:

```text
Pseudocode only:
Controller/Input requests Performer.RequestAction(Target)
Performer owns Progress/Timer
Performer sends ActionRequest to receivable target
Target resolves and mutates target-owned state
```

대상 인터페이스에 `RequestX`와 `ApplyXSuccess`를 동시에 넣으면 "대상이 프로세스를 시작하는가"와 "외부 프로세스 결과를 적용하는가"가 섞일 수 있다. 이 경우 `ApplyXAttempt` 또는 `ResolveXAttempt`처럼 대상이 최종 효과를 재검증하는 이름을 우선한다.

## 데미지 책임 분리

- WeaponComponent는 사격 가능 여부, 명중 판정, 데미지량 계산, 데미지 적용 요청을 담당할 수 있다.
- Target 또는 Target 소유 Component는 Shield, Armor, WeakPoint, Invincible 처리와 실제 Health 감소를 결정한다.
- WeaponComponent가 Target의 Health/Shield/Armor/WeakPoint/Invincible 내부 상태를 직접 수정하지 않는다.
- Weapon은 `ApplyDamage` 같은 의도 기반 요청을 보내고, Target 소유자가 검증, 상태 변경, 내부 정합성 정리, 이벤트 발행을 수행한다.

## 목적

Unreal 코드에서 상태 변경, 상태 조회, 사건 알림을 섞으면 책임과 버그 위치를 찾기 어렵다. 로컬 AI는 Command, Query, Event를 분리해서 함수 이름과 호출 흐름에 드러내야 한다.

검색 키워드: command query event, CQRS, Unreal event, delegate, gameplay message, RPC, OnRep, Try, Request, Get, Broadcast

## 정의

- Command: 상태를 바꾸거나 외부 효과를 발생시킨다.
- Query: 상태를 읽고 값을 반환하며 부작용이 없다.
- Event: 이미 일어난 일을 알린다. 상태 변경 요청이 아니다.

## 이름 규칙

| 종류 | 권장 접두어 | 예시 |
| --- | --- | --- |
| Command | `Try`, `Request`, `Apply`, `Set`, `Start`, `Stop` | `TrySpendAmmo`, `RequestInteract`, `ApplyDamageResult` |
| Query | `Get`, `Find`, `Has`, `Can`, `Is` | `GetCurrentHealth`, `CanFire`, `FindInteractable` |
| Event | `On`, `Handle`, `Broadcast` | `OnHealthChanged`, `HandleRep_Ammo`, `BroadcastInventoryChanged` |

## Command 규칙

1. Command는 검증 지점을 가진다.
   - 권한, cooldown, resource, 대상 유효성, gameplay tag 조건을 확인한다.
   - 실패 가능성이 있으면 bool, enum result, struct result 중 하나로 표현한다.

2. Command는 이벤트를 직접 호출자에게 강요하지 않는다.
   - 상태를 바꾼 소유자가 변경 이벤트를 발행한다.
   - UI나 사운드는 이벤트를 구독해서 반응한다.

3. 네트워크 Command는 신뢰 경계를 표시한다.
   - 클라이언트 RPC는 요청이다.
   - 서버가 검증하고 서버 상태를 바꾼다.
   - Multicast는 연출 동기화에 사용하고 원본 상태 판정에 쓰지 않는다.

## Query 규칙

1. Query는 부작용이 없어야 한다.
   - `Get` 함수 안에서 lazy spawn, asset load, delegate bind, state repair를 하지 않는다.
   - 캐시 갱신이 필요하면 별도 Command나 명시적 refresh API로 분리한다.

2. Query는 호출 비용을 숨기지 않는다.
   - 비싼 탐색은 `Find` 또는 `Query` 이름을 사용하고 호출 빈도를 고려한다.
   - Tick에서 `GetAllActorsOfClass`, asset registry scan, path load를 반복하지 않는다.

3. Query 결과의 유효성을 명확히 한다.
   - nullptr 가능성, 빈 배열, stale cache 가능성을 호출자가 알 수 있게 한다.

## Event 규칙

1. Event는 과거형 사실을 전달한다.
   - `OnHealthChanged`는 이미 체력이 바뀐 뒤 발행된다.
   - Event handler에서 같은 상태를 다시 판정하거나 원본을 덮어쓰지 않는다.

2. Delegate 바인딩과 해제를 짝지어 설계한다.
   - BeginPlay/EndPlay, NativeConstruct/NativeDestruct, OnRegister/OnUnregister를 맞춘다.
   - Lambda가 `this`를 캡처하면 수명과 해제 조건을 확인한다.

3. OnRep는 복제 이벤트다.
   - OnRep에서 UI, 사운드, cosmetic 업데이트는 가능하다.
   - OnRep에서 서버 권한 gameplay 판정을 다시 수행하지 않는다.

## 추천 흐름

```text
Input/UI
  -> Request/Try Command
  -> Owner validates and mutates SSOT
  -> Owner broadcasts Event or replicated OnRep fires
  -> UI/FX/Audio reacts through Query/Event
```

## 로컬 AI 생성 코드 체크

- 함수 이름만 보고 Command/Query/Event 구분이 되는가?
- Query가 상태를 바꾸고 있지 않은가?
- Event handler가 원본 상태를 훔쳐서 변경하지 않는가?
- RPC와 local command가 같은 검증 로직을 공유하는가?
- 실패 결과가 호출자에게 전달되는가?
