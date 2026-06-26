# Prototype Component / Subsystem — LM Studio User Prompt

Paste as the **first user message** or pin in a dedicated chat preset.

## Scope

Deliver **one compiling unit** only:
- `UActorComponent` **or** one Subsystem (`UWorldSubsystem` / `UGameInstanceSubsystem`)
- No manager zoo, no multi-file framework

## Tool order

1. `unreal_get_active_project`
2. `unreal_rag_search` with `mode=prototype_component` or `mode=prototype_subsystem`
3. `unreal_symbol_lookup` for the base type (`UActorComponent`, etc.)
4. `read_file` → minimal patch → `build_unreal_project`

## Rules

- Subsystem: **no** `CreateDefaultSubobject`, **no** `GetWorld` in constructor
- Component: **no** `GetWorld` / timers in constructor — use `BeginPlay`
- Tick off unless explicitly required
- Never claim compile success without build log

## Sampling (Qwen 27B)

- temperature: **0.1–0.2**
- max tokens: enough for one `.h` + one `.cpp`
- stop after build passes or static gate blocks
