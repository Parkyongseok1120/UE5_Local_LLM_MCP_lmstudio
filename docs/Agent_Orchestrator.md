# Agent Orchestrator

Small planner module — not a heavy framework.

## Flow

1. **classify** request -> `TaskKind`
2. **build evidence plan** -> RAG modes, gates, writes_allowed
3. **choose edit strategy** -> patch / no_edit / new_file
4. **tool policy** -> ordered tools from `config/tool_orchestration.json`

## CLI

```powershell
.\rag.ps1 agent-plan -Question "Fix missing generated.h in MyComponent" -Mode compile_fix
```

## MCP

`unreal_agent_plan` returns JSON plan (read-only).

## Wrapper

Enabled by default via `UNREAL_AGENT_ORCHESTRATE=1`. Disable with `--no-orchestrate` or env `UNREAL_AGENT_ORCHESTRATE=0`.

Plan is written to `agent_plan.json` in the wrapper run dir and prepended to prompts.

## Edit verification

`verify_edit_allowed(plan, files_count, patches_count)` blocks writes on inspect-only tasks.
