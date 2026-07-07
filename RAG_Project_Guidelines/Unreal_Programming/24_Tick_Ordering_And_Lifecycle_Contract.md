# Unreal Tick Ordering And Lifecycle Contract

## Keywords

Tick, TickComponent, PrimaryActorTick, PrimaryComponentTick, TickGroup, TG_PrePhysics, TG_DuringPhysics, TG_PostPhysics, TG_PostUpdateWork, tick prerequisite, AddTickPrerequisiteActor, AddTickPrerequisiteComponent, SetComponentTickEnabled, SetActorTickEnabled, bCanEverTick, bStartWithTickEnabled, tick order, tick dependency, per-frame update, timer vs tick, DeltaTime, order of operations, runtime behavior vs compile

Korean query aliases: 틱, 틱 순서, 틱 그룹, 틱 의존성, 선행 틱, 틱 활성화, 매 프레임 업데이트, 타이머 대 틱, 델타타임, 실행 순서, 컴파일은 되는데 동작이 이상함

## Purpose

Use this document when code compiles but behaves wrong because of **when** it runs each frame: an actor reading another actor's transform before it updated, physics vs gameplay ordering, or a component that never ticks. Small local models tend to "solve" ordering bugs by adding tick to everything or by guessing an order. This contract gives the mechanical rules to cite.

"Compiles" is not "runs as intended." A build that succeeds says nothing about tick order, and a `runtime_debug` answer must reason about the frame timeline, not just the API surface (see `05_Runtime_Debugging_Playbook.md` and `21_Edit_Verification_Proof_Levels.md`).

## Default: prefer events/timers over Tick

Per `12_Process_Ownership_Rules.md` and `11_Prototype_Component_Subsystem_Recipes.md`:

- Tick is **off by default**. `PrimaryComponentTick.bCanEverTick = false` / `PrimaryActorTick.bCanEverTick = false` unless per-frame logic is genuinely required.
- Prefer timers, delegates, latent/ability tasks, or explicit "active process" updates over per-frame polling.
- If Tick is required: enable it only while the process is active, and disable it on stop/cancel/finish. State the enable/disable contract explicitly.
- Subsystems should not use component-style Tick patterns; use timers/delegates or a world tick hook with a clear reason.

## Tick ordering mechanics

When order matters, do **not** guess. Control it explicitly:

1. **TickGroup** decides the phase within a frame. Common groups in order:
   - `TG_PrePhysics` — default gameplay tick; runs before physics simulation.
   - `TG_DuringPhysics` — runs alongside physics (do not read post-physics results here).
   - `TG_PostPhysics` — after physics; correct place to read simulated transforms.
   - `TG_PostUpdateWork` — late, after most updates (e.g. camera that must see final poses).
   Set via `PrimaryActorTick.TickGroup` / `PrimaryComponentTick.TickGroup`. Verify exact enum names before use.
2. **Tick prerequisites** enforce "A ticks before B" within the same group:
   - `AddTickPrerequisiteActor(OtherActor)` / `AddTickPrerequisiteComponent(OtherComp)` on the dependent object.
   - Use this instead of hoping the default order is correct. Removing a prerequisite when the dependency ends avoids stale ordering.
3. **Enable/disable at runtime** with `SetActorTickEnabled` / `SetComponentTickEnabled` and `bStartWithTickEnabled`. A component that "never runs" is often just never tick-enabled or its owner does not tick.

## Common "compiles but wrong" tick bugs

- **Reading another actor before it updated:** move the reader to a later TickGroup (`TG_PostPhysics`/`TG_PostUpdateWork`) or add a tick prerequisite on the producer. Do not add a frame of latency by caching last-frame values unless intentional.
- **Physics results read too early:** reading `GetComponentTransform()` of a simulated body in `TG_PrePhysics` gives last frame's result. Read in `TG_PostPhysics`.
- **Camera/spring-arm jitter:** camera logic must run after the followed target's movement — later TickGroup or prerequisite. See `16_TPS_Camera_SpringArm_Recipe.md`.
- **Order-dependent init in Tick:** initialization that assumes another system is ready belongs in the correct lifecycle function (BeginPlay/OnRegister), not guarded per-frame in Tick.
- **Everything ticks:** broad per-target Tick to drive a single active process. Use a timer or enable Tick only on the active object (see `11_AI_Review_Failure_Patterns.md` Failure Pattern 21).

## Lifecycle boundary reminder

Tick runs after BeginPlay. Do not resolve runtime dependencies in the constructor. If Tick reads something null, the fix is usually correct lifecycle placement or ordering, not a null guard that hides the timing bug (see `05_Runtime_Debugging_Playbook.md`).

## Response contract

1. State whether Tick is actually required, or whether a timer/event/prerequisite is the correct tool.
2. If ordering is the issue, name the TickGroup and/or prerequisite that fixes it — verify enum/API names first.
3. Give the enable/disable contract for any Tick you add.
4. Distinguish "will compile" from "will run in the intended order"; keep proof level at `Proposed` until PIE-verified.
