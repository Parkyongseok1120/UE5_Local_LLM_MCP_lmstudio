# Infra backlog (separate from Stage 6–7 game loop)

## Shared MCP stale `running` tasks

**Symptom:** `~/.lmstudio/state/unreal-agent/tasks/*/state.json` accumulated 61 entries with `status=running` (July Demo.cpp / plan loops + Aug Stage2 `43baa300…`). Shared LM Studio MCP then reported `TASK_ROUTE_BLOCKED` / `routeContextStatus=blocked`.

**Why it happens:** Tasks are created as `running` but chat disconnect, crash, force-kill, or abandoned LM Studio sessions do not reliably call `cancel_active_task` / complete. There is no TTL/GC sweep on MCP server start.

**Why campaign still progressed:** `supervisor_local_ai_turn.js` uses a fresh temp `AGENT_STATE_ROOT`, isolating from shared pollution.

**Mitigation applied (symptomatic only):** one-shot quarantine script marked stale tasks `cancelled` with backups under `quarantine/`. This is **not** a complete fix.

**Proper follow-up (do not block Stage 6–7):**
1. On MCP connect: auto-expire `running` tasks older than N hours or with dead lease.
2. On transport close: cancel owned tasks.
3. Optional: refuse to start new agent_edit while >K foreign running tasks without force.
