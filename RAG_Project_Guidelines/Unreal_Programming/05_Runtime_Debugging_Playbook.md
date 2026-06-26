# Unreal Runtime Debugging Playbook

## Keywords

Unreal runtime_debug, Editor log, crash, assert, ensure, access violation, callstack, UObject lifetime, garbage collection, null pointer, BeginPlay, Tick, replication debug, PIE, packaged build, async task, game thread

Korean query aliases: 런타임 디버깅, 에디터 로그 분석, 크래시 분석, assert 실패, ensure 실패, 널 포인터, UObject GC, BeginPlay 문제, Tick 문제, 리플리케이션 디버깅, 패키징 빌드 문제

## Purpose

Use this document when the code compiles but the game crashes, asserts, ensures, behaves incorrectly in PIE, or fails only at runtime. The answer should cite the log/callstack first, then connect it to lifecycle, ownership, replication, or threading rules.

## Runtime Evidence Order

1. Exact fatal line, assert line, ensure line, or access violation line.
2. Callstack frames inside the project module.
3. The UObject/Actor/Component lifecycle point: constructor, PostInitProperties, OnRegister, BeginPlay, Tick, EndPlay, BeginDestroy.
4. The owning object and whether it is valid, replicated, pending kill, or garbage collected.
5. Recent code changes and config changes.

## Playbook: Null UObject Or Access Violation

Likely causes:

- UObject pointer was not stored in a `UPROPERTY`.
- Actor/component was destroyed or pending kill.
- Code runs before BeginPlay or before dependency initialization.
- Asset reference is not assigned in defaults.
- A Blueprint subclass overrides defaults unexpectedly.

Fix sequence:

1. Identify the first project frame in the callstack.
2. Check pointer ownership and lifetime.
3. If the pointer is a UObject reference that should be retained, store it in `UPROPERTY`.
4. Add validity guards only after identifying why the pointer can be null.
5. Prefer moving initialization to the correct lifecycle function rather than hiding the crash.

## Playbook: Constructor Vs BeginPlay Bug

Constructor should:

- Create default subobjects.
- Set default values.
- Avoid accessing world, player controller, runtime assets, or spawned actors.

BeginPlay should:

- Resolve runtime dependencies.
- Bind to runtime systems.
- Start timers or gameplay behavior.

Common advice:

- If `GetWorld()` is null in a constructor, move the logic.
- If spawned actors or player state are needed, use BeginPlay or later.

## Playbook: Component Registration Or Attachment Bug

Likely causes:

- Runtime-created component was not registered.
- Attachment was done before the root component existed.
- Component was created with the wrong outer.

Fix sequence:

1. For default components, use `CreateDefaultSubobject` in the constructor.
2. For runtime components, use `NewObject`, attach, then `RegisterComponent`.
3. Store runtime components with `UPROPERTY` if they must persist.

## Playbook: Replication Runtime Bug

Likely causes:

- Actor does not replicate.
- RPC is called from a client that does not own the actor.
- Property was not added to `GetLifetimeReplicatedProps`.
- State is changed only on the client.
- `OnRep` expectation is wrong.

Fix sequence:

1. Confirm server authority for state changes.
2. Confirm ownership for Server RPC.
3. Confirm `bReplicates`, replicated properties, and lifetime props.
4. Use logs with role, authority, owner, and net mode.

## Playbook: Async Or Threading Bug

Likely causes:

- UObject touched from a non-game thread.
- Async callback captures a raw UObject pointer.
- Object is destroyed before async work completes.

Fix sequence:

1. Identify which thread invokes the callback.
2. Bounce UObject work back to the game thread.
3. Capture `TWeakObjectPtr` and validate before use.
4. Avoid doing gameplay state mutation in worker threads.

## Playbook: PIE Works But Packaged Build Fails

Likely causes:

- Editor-only module or class used in runtime code.
- Asset is referenced only by editor path and not cooked.
- Config differs between PIE and packaged build.
- Case-sensitive path issue.

Fix sequence:

1. Check module type: Runtime vs Editor.
2. Check logs from packaged build, not only PIE.
3. Verify assets are referenced or cooked.
4. Remove Editor dependencies from Runtime modules.

