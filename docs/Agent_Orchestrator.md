# Agent Orchestrator

Small planner module, not a heavy framework. Its job is to make compact 9-20B models follow a stable order before they touch files.

## Flow

1. **classify** request -> `TaskKind`
2. **build evidence plan** -> RAG modes, gates, writes_allowed
3. **choose edit strategy** -> patch / no_edit / new_file
4. **tool policy** -> ordered tools from `config/tool_orchestration.json`
5. **write gate** -> whether writes are allowed, max edit count, read-before-write/build requirements
6. **checkpoints** -> conditions the model must satisfy before moving to the next tool
7. **stop/retry policy** -> when to stop, and how to retry compile failures

## CLI

```powershell
.\rag.ps1 agent-plan -Question "Fix missing generated.h in MyComponent" -Mode compile_fix
```

## MCP

`unreal_agent_plan` returns read-only JSON with:

- `taskKind`
- `evidencePlan`
- `editStrategy`
- `toolPolicy`
- `writeGate`
- `checkpoints`
- `stopConditions`
- `retryPolicy`

LM Studio chat should call it first after `unreal_get_active_project`.

## Wrapper

Enabled by default via `UNREAL_AGENT_ORCHESTRATE=1`. Disable with `--no-orchestrate` or env `UNREAL_AGENT_ORCHESTRATE=0`.

Plan is written to `agent_plan.json` in the wrapper run dir and prepended to prompts.

## Edit Verification

`verify_edit_allowed(plan, files_count, patches_count)` blocks:

- writes on inspect-only, answer-only, and runtime-debug tasks
- edits when `editStrategy=no_edit`
- bundles larger than `writeGate.maxFilesPerEdit`

The wrapper also rejects invalid JSON, no-op edits, unsupported paths, static validation failures, and failed UBT loops.
