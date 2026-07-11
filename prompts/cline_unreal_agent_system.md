# Cline + Unreal Agent System Prompt

Use this as a **Cline system prompt** or pinned rules supplement (`.clinerules` is loaded automatically).

## Default workflow

1. `unreal_task_start` — create a task session; note `taskSessionId` and `authToken` for writes.
2. `unreal_get_active_project` + `unreal_project_status` — confirm RAG readiness before heavy edits.
3. `unreal_agent_plan` — read `writeGate` and `suggestedToolCalls` before any write.
4. **Read** with agent MCP (`read_file` / `read_file_range`), then **patch** with `replace_in_file` (`expectedOccurrences=1`).
5. **Build in Rider** when possible (Build → Build Project). Use agent `build_unreal_project` only when agent mode is enabled.
6. `unreal_task_status` — poll `phase` / `userMessage`; `unreal_task_cancel` stops linked compile jobs.

## Tool map (RAG vs agent MCP)

| Goal | RAG MCP | Agent MCP |
|------|---------|-----------|
| Active project | `unreal_set_active_project` | `set_active_project` |
| Search / evidence | `unreal_rag_search`, `unreal_agent_session` | `search_files`, `read_file` |
| Plan | `unreal_agent_plan` | — |
| Edit | — | `replace_in_file`, `apply_edit_bundle` |
| Validate / build | — | `static_validate_project`, `build_unreal_project` |
| Task lifecycle | `unreal_task_*` | pass `taskSessionId` + `authToken` on writes |

## Safety

- Do not use `run_command` for builds when Rider is available.
- Never retry the same `unreal_rag_search` query when `repeatDetected=true`.
- When `bootstrapCache.skipBootstrapPrompt=true` in `get_workspace_info`, skip multi-turn bootstrap and start with `unreal_task_start`.

## Handoff

On repeated errors or context pressure, call `write_session_handoff` and start a fresh chat using `.agent/handoff/latest.md`.
