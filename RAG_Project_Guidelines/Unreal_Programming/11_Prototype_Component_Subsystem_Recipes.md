# Prototype Recipes: Component and Subsystem

## Keywords

prototype component, prototype subsystem, UActorComponent, UWorldSubsystem, UGameInstanceSubsystem, BeginPlay, Initialize, Deinitialize, PrimaryComponentTick, BlueprintSpawnableComponent, minimal compiling skeleton

Korean: 프로토타입 컴포넌트, 프로토타입 서브시스템, 새 컴포넌트, 월드 서브시스템, 게임인스턴스 서브시스템

## Purpose

Use this document when the model must deliver **one compiling prototype unit** — either a single `UActorComponent` or a single Subsystem — not a framework or manager zoo.

## Prototype scope gate

- One reflected type per request unless the user explicitly asks for two.
- Maximum files in one turn: `.h`, `.cpp`, `Build.cs` touch only if a new module dependency is required.
- Tick is off by default for Components unless the request needs per-frame logic.
- Subsystems must not create subobjects with `CreateDefaultSubobject`.
- Do not put `GetWorld()`, `GEngine`, or `SpawnActor` in constructors.

## Recipe: Prototype UActorComponent

Files:

- `Source/<Module>/Public/<Feature>/<Name>Component.h`
- `Source/<Module>/Private/<Feature>/<Name>Component.cpp`

Rules:

1. Include `Components/ActorComponent.h` before `*.generated.h`.
2. `PrimaryComponentTick.bCanEverTick = false` unless tick is required.
3. State that belongs to the actor feature lives in the Component, not duplicated on the Actor.
4. Enhanced Input bindings belong in the Component or Controller — pick one owner and document it.

## Recipe: Prototype UWorldSubsystem

Use when state/logic is **world-scoped** and should survive across levels in the same world.

Files:

- `Source/<Module>/Public/<Feature>/<Name>Subsystem.h`
- `Source/<Module>/Private/<Feature>/<Name>Subsystem.cpp`

Rules:

1. Base class: `UWorldSubsystem`.
2. Override `ShouldCreateSubsystem` only when needed.
3. Use `Initialize(FSubsystemCollectionBase&)` / `Deinitialize()` — not `BeginPlay`.
4. No `CreateDefaultSubobject`, no `AActor` members without clear lifetime rules.
5. Do not use `Tick`; use timers, delegates, or world events.

## Recipe: Prototype UGameInstanceSubsystem

Use when state must persist for the **game instance** (menus, session, account-level services).

Rules:

1. Base class: `UGameInstanceSubsystem`.
2. Same lifecycle rules as World Subsystem.
3. Do not store raw `UWorld*` across world transitions without `TWeakObjectPtr` and invalidation.

## Build verification checklist

- [ ] `*.generated.h` is last include in reflected headers
- [ ] No reflected type inside a C++ namespace
- [ ] Every new `.cpp` function is declared in the header
- [ ] `Build.cs` lists modules for every non-Core include used in the header
- [ ] UBT was run or the answer states the exact build command still needed

## Common prototype failures

| Symptom | Likely fix |
|---------|------------|
| UHT generated.h error | Move generated.h to last include |
| Cannot open include | Add module to Build.cs |
| Subsystem never runs | Wrong subsystem type for lifetime; check `ShouldCreateSubsystem` |
| GC crash later | Add `UPROPERTY()` to UObject members |
