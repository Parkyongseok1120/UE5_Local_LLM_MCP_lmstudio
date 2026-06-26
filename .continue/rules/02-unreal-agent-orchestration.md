---
name: Unreal Agent Orchestration Protocol
alwaysApply: true
description: GPT-style Unreal agent loop with shared activeProject, RAG evidence, agent edits, and mandatory build verification.
---

# Unreal Agent Orchestration Protocol

## Tool roles

- `unreal-rag` = evidence only (search, symbols, health, compile-loop jobs)
- `unreal-agent` = filesystem + terminal + UnrealBuildTool only
- `current-datetime` = call before any time-sensitive or web-backed claim
- Do not invent APIs. If RAG has no evidence, say verification is required.

## Session bootstrap

1. Call `unreal_get_active_project` (RAG) or `get_active_project` (agent).
2. If the user names a game/project, call `set_active_project` with `.uproject` path or hint.
3. Call `unreal_rag_health` once per session to confirm the index is loaded.

## Standard implementation loop

1. **Understand** — restate goal, owner class, and validation method in one short block.
2. **Search** — `unreal_symbol_lookup` for class/API names; `unreal_rag_search` with `mode=agent_edit` or `compile_fix`.
3. **Read** — agent `read_file` on target header/cpp/Build.cs before editing.
4. **Patch** — minimal diff only; no full-file rewrite unless asked.
5. **Validate** — `write_file` / `replace_in_file` with `VALIDATE_ON_WRITE=1` returns static findings; fix before rebuild.
6. **Build** — agent `build_unreal_project` (or discovered UBT task) after every code edit.
7. **Fix** — on failure: read compiler/UHT output, RAG search in `compile_fix` / `module_fix` / `reflection_fix`, patch, rebuild.
8. **Runtime** — on PIE failure: agent `read_unreal_logs`, then RAG `runtime_debug`.
9. **Finish** — only after build passes or the exact blocker is identified.

## Background compile loop

- For large generated changes, use `unreal_start_compile_loop` (not the deprecated alias).
- Poll `unreal_compile_loop_status` until `completed`, `failed`, or `cancelled`.
- Do not claim success from the initial jobId alone.

## Unreal accuracy gates

- `*.generated.h` last include in reflected headers
- No reflected types inside C++ namespaces
- `CreateDefaultSubobject` only in constructors
- UObject members need `UPROPERTY` / `TObjectPtr` / `TWeakObjectPtr`
- RPC needs `_Implementation` in cpp
- Build.cs module dependencies must match includes
- Never use `Game/Framework/` includes — use `GameFramework/`
- Prefer `unreal_agent_session` or `unreal_rag_search` with `hybrid=false` for faster retrieval

## Answer quality

- Separate **facts from RAG**, **project state from filesystem**, and **assumptions**.
- Prefer project-local patterns over Lyra/examples when `activeProject` is set.
- RAG evidence is not a substitute for compilation.
