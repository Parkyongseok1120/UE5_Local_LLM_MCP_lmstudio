# Rider + Cline Smoke Checklist

Use this after `installer\Install-ClineUnrealMcp.ps1 -All` (or `Install-UnrealMcp.ps1 -InstallCline`).

## Prerequisites

- [ ] Windows 10/11, UE 5.4+ installed
- [ ] Node.js 20+, Python 3.10+
- [ ] JetBrains Rider with Unreal Engine plugin
- [ ] Cline extension (VS Code or CLI)

## Install verification

```powershell
.\installer\Verify-UnrealMcp.ps1
python scripts\rag_doctor.py
```

- [ ] `unreal-rag` and `unreal-agent` appear in Cline MCP settings with **no** `{REPO_ROOT}` / `{PYTHON_EXE}` placeholders
- [ ] `.clinerules` present in RAG repo root

## MCP health

In Cline chat:

1. [ ] `unreal_rag_health` — index exists, chunk count > 0
2. [ ] `unreal_get_active_project` — returns your `.uproject` or clear message
3. [ ] `unreal_project_status` — `ready` or clear `syncReason`

## Read / validate

4. [ ] Agent MCP `read_file` on a known `Source/*.cpp` path
5. [ ] Agent MCP `static_validate_project` — returns pass or actionable errors

## Task lifecycle

6. [ ] `unreal_task_start` with a short request — returns `phase=planning`, `taskSessionId`, `authToken`
7. [ ] `unreal_task_status` — same task shows `userMessage` and `cancellable=true`
8. [ ] `unreal_task_cancel` — status becomes `cancelled`; if a background job was started, it stops

## Build (Rider preferred)

9. [ ] Build from **Rider** (Build → Build Project) after a trivial comment edit
10. [ ] Optional: Agent MCP `build_unreal_project` when `ALLOW_UNREAL_BUILD=1` — shows progress notification and `phase` in JSON

## Project switch

11. [ ] `unreal_set_active_project` with `prepare=true` — returns `readiness` / `cacheInvalidation`
12. [ ] Agent MCP `set_active_project` — same project, no stale RAG after switch

## Pass criteria

All checked items succeed without manual JSON editing. If Cline shows unresolved paths, re-run:

```powershell
.\installer\Install-ClineUnrealMcp.ps1 -All [-EnableAgentMode]
```
