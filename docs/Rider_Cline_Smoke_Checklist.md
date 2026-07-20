# Rider + Cline Smoke Checklist (stable install)

Use after running the root integrated installer with the Cline component.

## Prerequisites

- [ ] Windows 10/11, UE 5.4+ installed
- [ ] Node.js 20+, Python 3.10+
- [ ] JetBrains Rider with Unreal Engine plugin
- [ ] Cline extension (VS Code or CLI)

## Install verification

```powershell
.\scripts\installer_support\Verify-UnrealMcp.ps1
python scripts\rag_doctor.py
```

- [ ] `unreal-rag` and `unreal-agent` in Cline MCP settings with **no** `{REPO_ROOT}` / `{PYTHON_EXE}` placeholders
- [ ] Existing non-Unreal MCP servers preserved after Cline reinstall
- [ ] `.clinerules` present in RAG repo root

## MCP health

1. [ ] `unreal_rag_health` — index exists, chunk count > 0
2. [ ] `unreal_get_active_project` — returns your `.uproject` or clear message
3. [ ] `unreal_set_active_project` — set project via RAG MCP (canonical path)
4. [ ] `unreal_project_status` — `ready` or clear `syncReason`

## Read / validate / edit

5. [ ] Agent MCP `read_file` on a known `Source/*.cpp` path
6. [ ] Agent MCP `replace_in_file` with `expectedOccurrences=1` on a trivial comment
7. [ ] Agent MCP `static_validate_project` — pass or actionable errors (Essential tool)

## Build (Rider preferred)

8. [ ] Build from **Rider** (Build → Build Project) after the trivial edit
9. [ ] Optional: Agent MCP `build_unreal_project` when `ALLOW_UNREAL_BUILD=1`

## Validation dirty recovery

10. [ ] If write validation times out, `static_validate_project` clears dirty gate and build proceeds

## Pass criteria

All checked items succeed without manual JSON editing. If Cline shows unresolved paths:

```powershell
python install.py --profile custom --components codex,lmstudio,unreal,cline --cline-settings C:\path\to\cline_mcp_settings.json --dry-run
```

Use `--dry-run` first to preview managed changes.
