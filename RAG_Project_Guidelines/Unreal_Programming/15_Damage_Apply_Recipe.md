# Damage Apply Recipe (UE 5.8) — Codegen

## Scope

This document is for **compile-ready codegen**. Design reviews use `10_Damage_Responsibility_Rules.md` instead.

## Minimal ApplyDamage (prototype)

```cpp
#include "Kismet/GameplayStatics.h"
#include "GameFramework/Actor.h"

void UCombatComponent::ApplyDamageToActor(AActor* Target, float Amount, AActor* InstigatorActor)
{
    if (!Target || Amount <= 0.f) return;
    UGameplayStatics::ApplyDamage(Target, Amount, InstigatorActor ? InstigatorActor->GetInstigatorController() : nullptr, InstigatorActor);
}
```

## TakeDamage override (when needed)

```cpp
float AMyCharacter::TakeDamage(float DamageAmount, FDamageEvent const& DamageEvent,
    AController* EventInstigator, AActor* DamageCauser)
{
    const float Applied = Super::TakeDamage(DamageAmount, DamageEvent, EventInstigator, DamageCauser);
    // project-specific mitigation here
    return Applied;
}
```

## Rules

- Confirm signature against UE 5.8 project module API before shipping.
- Keep `DamageCauser`/`Instigator` ownership explicit in multiplayer.
- Prefer a single `ApplyDamage` gateway component for prototypes.
