# Design Validation Gates

## Purpose

This document defines hard validation gates for Unreal Engine C++ design reviews and AI-generated code. If any gate fails, the answer must be corrected before presenting the design as safe.

검색 키워드: validation gate, self contradiction, declaration consistency, interface event separation, generic setter ban, damage responsibility, RAG citation, source label, document section citation

## 1. Self-Contradiction Gate

The answer must not reintroduce a pattern it just banned.

Hard rules:

- If the answer says not to put Event/Delegate functions in an interface, the code example must not put `OnXChanged`, `OnXStarted`, `OnXCompleted`, `OnDamageApplied`, or similar notification functions in a UINTERFACE.
- If the design says events are owned by the state owner, the code must broadcast from the state owner, not from an unrelated external object.
- If the design says to use `ApplyDamage`, `ConsumeAmmo`, `ApplyActionAttempt`, or `ResolveActionAttempt`, the code must not silently switch to `SetHealth`, `SetAmmo`, or `SetIsHacked`.
- If a rule is marked as prohibited, examples, pseudocode, and review suggestions must not use that prohibited pattern as the main path.

## 2. Declaration Consistency Gate

Every symbol used in code must be declared in the shown snippet or explicitly marked as project-specific/external.

Before claiming `Compile-ready Unreal C++`, check:

- Every called function was declared earlier or is a real Unreal/project API cited by RAG.
- Every member variable used in `.cpp` exists in the `.h`.
- Every Delegate type and Delegate member is declared before binding or broadcasting.
- Every TimerHandle used by `GetWorldTimerManager()` is declared.
- Every UFUNCTION referenced by BlueprintNativeEvent has the correct declaration and `_Implementation` body.
- Every UPROPERTY used for GC, Blueprint, replication, or editor editing is declared with the intended specifiers.
- Every include, forward declaration, and Build.cs dependency needed by the shown types is accounted for.

If any item is omitted because the example is intentionally partial, label the block `Pseudocode only` or state `not implemented in this snippet`.

## 3. Interface / Event Separation Gate

Interface is a contract for asking or requesting something from a target. Delegate/Event is a notification that something already happened.

Interface may contain:

- `CanX`
- `IsX`
- `GetX`
- `FindX`
- `TryX`
- `RequestX`
- `ApplyX`

Interface must not contain:

- `OnXChanged`
- `OnXStarted`
- `OnXCompleted`
- `OnXFailed`
- `BroadcastX`
- Delegate subscription functions unless there is a strong project-specific reason and ownership is explicitly reviewed.

State change notifications belong to the state-owning object as Delegate/Event, Gameplay Message, or replication notification. UI, FX, Audio, and other observers bind to the owner event.

## 4. Generic Setter Ban

General gameplay setters are disallowed by default.

Avoid:

- `SetHealth(Value)`
- `SetShield(Value)`
- `SetAmmo(Value)`
- `SetIsHacked(Value)`
- `SetQuestState(Value)`

Prefer intent-revealing mutation APIs:

- `ApplyDamage`
- `RestoreShield`
- `ConsumeAmmo`
- `ReloadMagazine`
- `ApplyActionAttempt`
- `ResolveActionAttempt`
- `ApplyActionFailure`
- `GrantReward`
- `CompleteObjective`

Debug or editor-only setters must be named as such:

- `Debug_SetHealth`
- `Debug_SetAmmo`
- `EditorOnly_SetQuestState`

These setters must not be presented as normal runtime gameplay APIs.

## 5. Code Example Mode Gate

Design review code examples default to `Pseudocode only`.

Use `Compile-ready Unreal C++` only after checking:

- `.h` and `.cpp` split
- includes and forward declarations
- `.generated.h` placement
- `UPROPERTY` / `UFUNCTION` / `UINTERFACE` / Delegate declarations
- TimerHandle declarations
- BlueprintNativeEvent declaration and `_Implementation` rules
- real Unreal API signatures
- Build.cs module dependencies

If the Unreal API signature is not certain, say `확인 필요` and keep the code as pseudocode.

## 6. Damage Responsibility Gate

Damage flow must preserve target ownership.

WeaponComponent may handle:

- fire request validation
- ammo/resource consumption request
- hit detection
- base damage calculation
- damage application request

Target or target-owned components decide:

- Shield handling
- Armor handling
- WeakPoint handling
- Invincible/immune handling
- actual Health reduction
- death/knockback/state transitions
- state change event broadcast

WeaponComponent must not directly modify the target's internal Health, Shield, Armor, WeakPoint, or Invincible state. It sends an intent such as `ApplyDamage` or a damage request; the target owner resolves the result.

## 7. RAG Citation Gate

Do not cite RAG evidence as only `Source 1`, `Source 2`, or a bare index number.

When citing, include:

- evidence type: User RAG guideline, Epic official documentation, Unreal Engine source, or local project source
- document/file name
- section name when available
- locator/path when useful

Examples:

- `User RAG guideline: Interface and API Design Rules > Critical Rule: Do Not Mix Interface and Event`
- `User RAG guideline: Design Validation Gates > Damage Responsibility Gate`
- `Unreal Engine source: LyraAttributeSet.h > ATTRIBUTE_ACCESSORS comment`
- `Epic official documentation: [document title] > [section title]`

Never present a user-authored RAG guideline as if it were Epic official documentation. User guidelines are project rules; Epic docs and engine source are engine evidence.

## 8. State Change Event Gate

State change event order:

1. State owner receives a validated mutation request.
2. State owner updates internal state.
3. State owner restores internal consistency, clamps values, handles derived state, and records result.
4. State owner broadcasts Delegate/Event or relies on OnRep/Gameplay Message.

External objects must not directly broadcast another object's delegate. Observers react after the owner has completed the mutation.
