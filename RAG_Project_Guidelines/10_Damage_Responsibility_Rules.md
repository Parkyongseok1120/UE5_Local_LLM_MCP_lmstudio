# Damage Responsibility Rules

## Purpose

Damage code is a common place where AI-generated Unreal code breaks responsibility separation. This document defines the ownership boundary between Weapon, Damage Request, Target, Shield, Armor, WeakPoint, Invincible, and Health.

검색 키워드: damage responsibility, WeaponComponent, Target, Shield, Armor, WeakPoint, Invincible, Health, ApplyDamage, damage request, target owns health

## Core Rule

WeaponComponent does not own target state. Target or target-owned components decide how incoming damage changes target state.

WeaponComponent can ask. Target decides.

## WeaponComponent Responsibilities

WeaponComponent may handle:

- fire input validation
- fire rate and cooldown checks
- ammo consumption request through an owner API
- trace/projectile spawn/hit detection
- base damage calculation from weapon data
- critical hit or distance multiplier calculation if weapon-owned
- construction of a damage request/result context
- calling a target-facing mutation API such as `ApplyDamage`

WeaponComponent must not:

- directly subtract target Health
- directly subtract target Shield or Armor
- directly flip target Invincible flags
- directly broadcast target state delegates
- directly mutate target weak point state
- assume target has a specific Health variable unless confirmed by interface/query and RAG evidence

## Target Responsibilities

Target or target-owned components decide:

- whether the target can receive damage
- Shield absorption
- Armor mitigation
- WeakPoint rules
- Invincible/immune handling
- actual Health reduction
- death state transition
- hit reaction state
- authoritative replication result
- state change Delegate/Event broadcast

## Preferred Flow

```text
Pseudocode only:
WeaponComponent validates fire
WeaponComponent detects hit
WeaponComponent builds DamageRequest
WeaponComponent calls Target.ApplyDamage(DamageRequest)
Target validates damage authority and immunity
Target applies shield/armor/weakpoint rules
Target mutates Health if needed
Target restores internal consistency
Target broadcasts OnHealthChanged or OnDamageApplied delegate
Weapon/UI/FX react to result or event
```

## Unreal Damage API Caution

설계 리뷰에서는 `AActor::TakeDamage`, `UGameplayStatics::ApplyDamage`, `FDamageEvent`, `Instigator`, `DamageCauser`를 컴파일 가능한 코드처럼 쓰지 않는다. 이 API들은 Unreal 버전, 프로젝트 damage policy, Controller/Actor ownership, DamageCauser 타입에 따라 확인이 필요하다.

특히 ActorComponent의 `this`를 DamageCauser로 넘기는 식의 예시는 피한다. DamageCauser가 Actor를 요구하는 API라면 Weapon actor, projectile actor, owner actor 등 프로젝트 정책에 맞는 Actor를 넘겨야 할 수 있다.

확인 전 권장 표현:

```text
Pseudocode only:
WeaponComponent creates DamageRequest
DamageRequest contains source actor, instigator/controller if needed, hit data, base damage
Target receives DamageRequest
Target resolves shield, armor, weakpoint, vulnerability, invincibility, and health
```

컴파일 가능한 Unreal Damage API 예시는 공식 API/엔진 소스/프로젝트 기존 사용 예시를 확인한 뒤에만 작성한다.

## API Naming

Prefer:

- `ApplyDamage`
- `ApplyDamageRequest`
- `ResolveIncomingDamage`
- `CanReceiveDamage`
- `GetDamageReceiver`

Avoid:

- `SetTargetHealth`
- `SetHealthAfterHit`
- `WeaponSetShield`
- `ForceTargetDamage`

## Interface Boundary

If an interface is needed, keep it as a request/query contract:

- `CanReceiveDamage`
- `ApplyDamage`
- `GetDamageReceiver`

Do not put target state events in the interface:

- no `OnHealthChanged`
- no `OnShieldChanged`
- no `OnDamageCompleted`

Those are delegates/events on the target-owned state object.
