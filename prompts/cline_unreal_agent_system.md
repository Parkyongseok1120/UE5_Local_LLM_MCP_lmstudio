# Cline + Unreal Agent System Prompt (stable install)

Use with `.clinerules`. Default path uses **Essential tools only** (`MCP_ESSENTIAL_TOOLS=1`).

## Default workflow

1. `unreal_get_active_project` — confirm active `.uproject` (set via RAG MCP `unreal_set_active_project` if needed).
2. `unreal_project_status` — check RAG/index readiness before heavy edits.
3. `unreal_agent_plan` — read `writeGate` and `suggestedToolCalls` before any write.
4. **Read** with agent MCP (`read_file` / `read_file_range`), then **patch** with `replace_in_file` (`expectedOccurrences=1`).
5. If validate-on-write timed out, run agent MCP `static_validate_project` before build.
6. **Build in Rider** when possible (Build → Build Project). Use agent `build_unreal_project` only when agent mode is enabled.

## Tool map (RAG vs agent MCP)

| Goal | RAG MCP | Agent MCP |
|------|---------|-----------|
| Active project | `unreal_set_active_project` | reads shared config via `get_active_project` |
| Search / evidence | `unreal_rag_search`, `unreal_agent_session` | `search_files`, `read_file` |
| Plan | `unreal_agent_plan` | — |
| Edit | — | `replace_in_file`, `write_file` (new files only) |
| Validate / build | — | `static_validate_project`, `build_unreal_project` |

## Safety

- Do not use `run_command` for builds when Rider is available.
- Never retry the same `unreal_rag_search` query when `repeatDetected=true`.
- Use single-file edits only; multi-file bundles are disabled in stable installs.

## Handoff

On repeated errors or context pressure, call `write_session_handoff` and start a fresh chat using `.agent/handoff/latest.md`.
