# Genre To Codegen Bridge (UE 5.8)

## Purpose

Map **Genre Adapter** design constraints to **Unreal Domain** C++ recipes before writing code.

## Layer order

1. Core architecture rules
2. Unreal Domain recipes (02–15)
3. Genre Adapter (Genre_Gameplay/*)
4. Active project local patterns

## Genre → first C++ unit

| Genre | First compile unit | Domain recipes |
|-------|-------------------|----------------|
| Shooter / TPS | `UWeaponComponent` or PC input + trace | Enhanced Input, Camera/SpringArm |
| Action combat | `UCombatComponent` | Damage flow, montage later |
| Platformer | `UPlatformerMoveComponent` | CharacterMovement, jump |
| Roguelike | `URunStateSubsystem` | SaveGame, tags |
| Strategy | `UTurnManagerSubsystem` | minimal UI later |

## Mixed genres

Combine adapters (max 3). Example: Extraction = shooter + survival pressure. Prototype **one** pressure loop only.

## Codegen guardrails

- Includes: `GameFramework/`, never `Game/Framework/`
- Input: `SetupInputComponent` on PC or owning pawn component
- Damage: use project policy; see `15_Damage_Apply_Recipe.md` for compile-safe minimal API
