# GAS Minimal Prototype Recipe

Minimal Gameplay Ability System bootstrap — verify Lyra/sample project headers before expanding.

## Module deps (`Build.cs`)

```csharp
PrivateDependencyModuleNames.AddRange(new[] { "GameplayAbilities", "GameplayTags", "GameplayTasks" });
```

## Character setup

- Add `UAbilitySystemComponent` (often on pawn/character).
- Add `UAttributeSet` subclass with reflected attributes.
- Grant abilities via `AbilitySystemComponent->GiveAbility(FGameplayAbilitySpec(...))`.
- Activate with `TryActivateAbility(SpecHandle)` on the **same ASC**.

## Anti-patterns (denylist)

- Free-function `GiveAbility` / wrong `TryActivateAbility` signature
- Invented `UAbilitySystemGlobals` helpers

See `20_Unreal_API_Hallucination_Blocklist.md`.
