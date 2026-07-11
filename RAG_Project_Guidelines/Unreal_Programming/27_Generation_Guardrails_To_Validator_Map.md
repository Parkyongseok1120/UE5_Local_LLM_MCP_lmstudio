# Generation Guardrails → Validator Map

Maps common codegen mistakes to static-validator finding codes and fixes. All Tier C findings are **advisory** (warning only).

| If you write… | Finding code | Fix |
|---|---|---|
| `TArray<UFoo*>` member without `UPROPERTY` | `UOBJECT_CONTAINER_WITHOUT_UPROPERTY` | Add `UPROPERTY()` on retained container members |
| `TObjectPtr<T>` member without `UPROPERTY` | `TOBJECTPTR_WITHOUT_UPROPERTY` | Add `UPROPERTY()` |
| Raw `UObject*` / `AActor*` member | `RAW_UOBJECT_MEMBER_WITHOUT_UPROPERTY` | Prefer `UPROPERTY(TObjectPtr<...>)` |
| `AddDynamic` / `BindUObject` without teardown | `DELEGATE_BIND_WITHOUT_UNBIND` | `RemoveDynamic` / `RemoveAll` in `EndPlay` |
| `SetTimer` without teardown | `TIMER_SET_WITHOUT_CLEAR` | `ClearTimer` / `ClearAllTimersForObject` |
| Ignore `bInterrupted` / `bWasCancelled` | `INTERRUPT_PARAM_IGNORED` | Branch on interrupt flag in callback |
| `Cast<T>()` then immediate `->` | `UNCHECKED_CAST_RESULT` | `if (IsValid(X))` before dereference |
| `UPROPERTY(Replicated)` in `.h` only | `REPLICATED_UPROPERTY_WITHOUT_DOREPLIFETIME` | Add `GetLifetimeReplicatedProps` + `DOREPLIFETIME` in `.cpp` |
| `new UMyObject` / `delete` on UObject | `RAW_NEW_DELETE_UOBJECT` | `NewObject<>` with outer; no manual delete |
| `GetWorld()` in `AActor` ctor | `ACTOR_CTOR_GETWORLD` | Defer to `BeginPlay` |
| Sync `LoadObject` in `Tick`/`BeginPlay` | `SYNC_LOAD_IN_GAMEPLAY` | `TSoftObjectPtr` + `FStreamableManager` |
| Hardcoded `"/Game/..."` path | `HARDCODED_ASSET_PATH` | Soft reference or `ConstructorHelpers` in ctor |
| `FVector(1.0f, …)` literals | `FVECTOR_FLOAT_PRECISION` | Use double literals or `FVector3d` |
| `UFUNCTION(BlueprintPure)` non-const | `BLUEPRINTPURE_MISSING_CONST` | Mark getter `const` |

See also: `28_Delegate_Lifecycle_Codegen_Recipe.md`, `29_Replication_RPC_Codegen_Recipe.md`, `33_Teardown_Symmetry_And_Lifecycle.md`.
