# Teardown Symmetry and Lifecycle

Every runtime subscription needs a matching teardown in the same owning object.

| Start / activate | Teardown |
|---|---|
| `AddDynamic` / `BindUObject` | `RemoveDynamic` / `RemoveAll` / clear delegate |
| `SetTimer` | `ClearTimer` / `ClearAllTimersForObject` |
| `SetComponentTickEnabled(true)` | disable in `EndPlay` or when idle |
| Montage play + end delegate | stop montage + clear end delegate |
| Cinematic play | `StopCinematic` in `FinishSkill` / `EndPlay` |

Always call matching `Super::BeginPlay`, `Super::EndPlay`, `Super::Tick`, etc.

## Validator links

- `TIMER_SET_WITHOUT_CLEAR`
- `DELEGATE_BIND_WITHOUT_UNBIND`
- `INTERRUPT_PARAM_IGNORED`
- `MISSING_SUPER_LIFECYCLE_CALL`

## Skill / montage example

Handle `bInterrupted` in montage end callbacks; stop montage and cinematic in `FinishSkill()`.
