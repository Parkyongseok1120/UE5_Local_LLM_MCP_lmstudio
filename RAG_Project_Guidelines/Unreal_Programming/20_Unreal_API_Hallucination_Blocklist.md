# Unreal API Hallucination Blocklist

## Purpose

Use this checklist when reviewing or generating Unreal Engine answers with small local models. The goal is to stop plausible but unevidenced Unreal API claims before they become code, Blueprint plans, or material/shader instructions.

## Blocked Unless Exact Evidence Exists

Do not claim these are available unless a cited project file, engine symbol, official doc chunk, or exported metadata proves the exact path:

- Arbitrary `GetAttribute("...")` accessors in C++, shaders, or Material Graph.
- Automatic main directional light direction inside ordinary surface materials.
- Reading or rewriting final `SceneColor` from an ordinary surface material.
- `ResolvedView.PreExposure` as a normal Material Graph value.
- `WorldPosition.Z` as camera distance.
- Surface material access to GBuffer, CustomStencil, CustomDepth, or neighbor SceneDepth like a post-process shader.
- Direct `.uasset` Blueprint or Material graph mutation from filesystem write tools.
- Blueprint node execution or pin links inferred only from asset/class/variable names.
- Adding Build.cs module dependencies without include-owner evidence, symbol evidence, or a real compile/link/UHT error.
- Claiming Editor, PIE, shader compile, or UBT success without the corresponding log or tool result.
- Sequencer/MovieScene invented APIs: `bRestoreState` as a public player field, `SetRestoreState(...)`, `SetBindingTag(...)`, `AddBindingOverride(...)`, or treating `AActor::Tags` as a Sequencer binding tag. Restore-on-finish is a sequence/section Completion Mode (Restore State vs Keep State) plus `FMovieSceneSequencePlaybackSettings`; binding overrides use `FMovieSceneObjectBindingID`. Verify the exact symbol before use (see `23_Sequencer_Binding_And_Playback_Playbook.md`).
- Tick ordering guesses: asserting a `TickGroup` value, prerequisite API (`AddTickPrerequisiteActor/Component`), or tick-enable call without confirming the enum/function name (see `24_Tick_Ordering_And_Lifecycle_Contract.md`).
- `UCharacterMovementComponent::DisableGravity()` — this member does not exist. Prefer `MoveComp->GravityScale = 0.0f;` for gravity scaling, or deliberately select a movement mode such as `MoveComp->SetMovementMode(MOVE_Flying);` when that behavior is intended.
- `UWorld::GetURL()` / `World->GetURL()` — `UWorld` does not expose this member. For a current level name use `UGameplayStatics::GetCurrentLevelName(World, true)` or `World->GetMapName()`. Restart with `UGameplayStatics::OpenLevel` for local/PIE flow or authority-aware `ServerTravel` when network travel is intended.
- `GEngine->GetWorld()` / `GEngine->GetGameInstance()` — do not use the engine singleton as world context. Resolve `GetWorld()` from the owning actor/subsystem/component or pass an explicit `UWorld*`; then use `World->GetGameInstance()`.
- `SpawnActor<T>(..., &SpawnTransform, ...)` copied into a typed overload without checking the signature. Prefer `World->SpawnActor<T>(Class, SpawnTransform, Params)` with an explicit `FTransform` and `FActorSpawnParameters`. A transform-pointer overload may exist, but selecting it accidentally is fragile and should not be assumed.
- Project subsystem/component types used without their declaring header. Before `GetSubsystem<UMySubsystem>()` or a member call, include the matching project header and verify the type exists; do not create a placeholder API to silence the compiler.
- Editor-only world access (`GEditor`, `GetEditorWorldContext`, `FEditorDelegates`) in runtime game/dev-console code. Keep editor APIs in editor modules or `WITH_EDITOR` code paths; runtime level operations must start from the caller's `UWorld*`.
- Invented replication helpers (`ReplicateVariable`, `SetReplicated`) — use `GetLifetimeReplicatedProps` + `DOREPLIFETIME`.
- Invented GAS helpers (`GiveAbility` as free function, wrong `TryActivateAbility` signature) — grant/activate through the owning `UAbilitySystemComponent`.
- World-less `GetPlayerController()` / wrong `SpawnEmitterAtLocation` overload — pass explicit world context and verify `UGameplayStatics` signatures.
- UMG `CreateWidget` / `AddToViewport` without owning player — construct widgets with valid player/world context.
- Free `HasAuthority()` / `IsServer()` / global `GetNetMode()` — resolve authority from an `AActor` or `UWorld::GetNetMode()`.
- Wrong physics helpers (`SetGravityEnabled`, `EnablePhysicsSimulation` on wrong types) — use component-specific APIs such as `SetSimulatePhysics` / `SetEnableGravity`.

## Verified Replacement Snippets

```cpp
// Disable character gravity without inventing an API.
if (UCharacterMovementComponent* MoveComp = Character->GetCharacterMovement())
{
    MoveComp->GravityScale = 0.0f;
}

// Restart the current local/PIE level from a known world.
const FString LevelName = UGameplayStatics::GetCurrentLevelName(World, true);
UGameplayStatics::OpenLevel(World, FName(*LevelName));

// Spawn with the typed transform overload.
FActorSpawnParameters Params;
AEnemyCharacter* Spawned = World->SpawnActor<AEnemyCharacter>(
    EnemyClass, SpawnTransform, Params);
```

## Rewrite Pattern

When a blocked claim appears, rewrite it into one of these forms:

- `Evidence-backed`: cite the exact file, symbol, metadata export, or log line.
- `Approximation`: explain what can be approximated and how behavior differs.
- `Needs Editor export`: ask for Blueprint/Material metadata or screenshot evidence.
- `Post-process only`: keep the feature in post-process/global shader code.
- `Parameter-driven`: expose the missing runtime value through a Material Parameter Collection, DataAsset, config, or C++ binding.

## Small Model Response Rule

If evidence is missing, say what is missing. Do not fill the gap with a likely Unreal API name.