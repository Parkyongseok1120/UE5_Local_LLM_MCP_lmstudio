# LM Studio Unreal Agent Setup

Use this guide for **LM Studio basic chat** with `unreal-rag` + `unreal-agent` MCP.

## 1. Prerequisites

```powershell
cd $HOME\.lmstudio\Unreal58-RAG
.\rag.ps1 doctor
.\installer\Verify-UnrealMcp.ps1
```

Both should PASS. Engine default: **UE 5.8**.

## 2. MCP configuration

File: `$HOME\.lmstudio\mcp.json`

Required servers:
- `unreal-rag` — search, health, agent plan
- `unreal-agent` — read/write files, UBT build

**LM Studio chat (weak models):** set on **both** servers:

```json
"MCP_ESSENTIAL_TOOLS": "1"
```

Installer and `scripts/patch_mcp_config.py` enable this by default. See [LMStudio_MCP_Tool_Discipline.md](LMStudio_MCP_Tool_Discipline.md).

After path changes:

```powershell
powershell -File $HOME\.lmstudio\scripts\patch_mcp_runtime_paths.ps1
```

Restart LM Studio so MCP servers reload.

## 3. System prompt

Base rules: [`prompts/lmstudio_compact_mcp_base.md`](../prompts/lmstudio_compact_mcp_base.md)

| Model | System prompt |
|-------|---------------|
| **Qwen 3.6 27B** (primary) | [`lmstudio_qwen36_27b_compact_system.md`](../prompts/lmstudio_qwen36_27b_compact_system.md) + base |
| GPT OSS | [`lmstudio_gpt_oss_compact_system.md`](../prompts/lmstudio_gpt_oss_compact_system.md) |
| Qwen 3.5 9B / 8B | [`lmstudio_qwen35_9b_compact_system.md`](../prompts/lmstudio_qwen35_9b_compact_system.md) |

Paste the matching file into LM Studio **System Prompt**.

**Qwen 3.6 / thinking models:** disable visible reasoning in LM Studio or use execute turns with thinking OFF. Visible chat must not include "thinking process" text — it breaks tool/JSON parsers.

## 4. Session start (every chat)

Paste [`prompts/lmstudio_session_bootstrap.md`](../prompts/lmstudio_session_bootstrap.md) as the **first user message**, or manually:

1. `unreal_get_active_project`
2. If wrong project: `unreal_set_active_project` or `unreal_open_project_picker`
3. `unreal_rag_health` once
4. `get_workspace_info` (unreal-agent)
5. For implementation: `unreal_agent_plan` then `unreal_rag_search` **before** any edit
6. Use `read_file_range` / `read_file`, then `replace_in_file`; `write_file` is only for brand-new files

Do not use `run_javascript`, `js-code-sandbox`, Deno file APIs, Node `fs`, or browser/code-sandbox tools for project file I/O. If LM Studio exposes the JavaScript/TypeScript Code Sandbox plugin, hide or disable it for Unreal coding chats.

Task templates: [`lmstudio_user_compile_fix.md`](../prompts/lmstudio_user_compile_fix.md), [`lmstudio_user_agent_edit.md`](../prompts/lmstudio_user_agent_edit.md)

## 5. Standard loop

```
unreal_agent_plan -> RAG search -> read_file_range/read_file -> replace_in_file -> build_unreal_project -> read log on failure
```

Rules:
- Do **not** paste full `.cpp` in chat when MCP write is available.
- Do **not** say "done" without build output.
- Existing source files are patch-only. `write_file` is for brand-new files; existing `.h`, `.cpp`, and `.cs` writes are blocked by default in `unreal-agent`.
- `replace_in_file` / `write_file` run static validation when `VALIDATE_ON_WRITE=1` (blocks bad includes like `Game/Framework/`).

## 6. Modes

| Task | RAG mode | Notes |
|------|----------|-------|
| New component | `prototype_component` | one UActorComponent |
| Compile error | `compile_fix` | paste error line |
| Runtime crash | `runtime_debug` | paste log/callstack |
| Refactor plan | `refactor_r0`..`r4` | plan only first |

User prompt presets: [`prompts/prototype_component.md`](../prompts/prototype_component.md), [`prompts/refactor_R0_R2.md`](../prompts/refactor_R0_R2.md)

## 7. Large codegen

Use `unreal_start_compile_loop` + poll `unreal_compile_loop_status` instead of manual multi-file paste.

## 8. Troubleshooting

| Symptom | Fix |
|---------|-----|
| RAG MCP fails to start | Run `patch_mcp_runtime_paths.ps1`; avoid WindowsApps python |
| write blocked | `ALLOW_WRITE=1` in unreal-agent env |
| Validation errors after write | Fix `BAD_INCLUDE_PATH`, missing `.generated.h`, RPC `_Implementation` |
| Slow search | Use `hybrid=false` on search for faster FTS-only (Phase H tuning) |

## 9. Rider + Cline (주력 IDE)

Primary: **JetBrains Rider** for Unreal C++ build/debug.  
Agent: **Cline** with MCP — see [`docs/Cline_Rider_Unreal_Agent_Setup.md`](Cline_Rider_Unreal_Agent_Setup.md).

Install MCP into Cline:

```powershell
.\installer\Install-ClineUnrealMcp.ps1
```

Legacy Continue: [`docs/Continue_Qwen_Unreal_Agent_Setup.md`](Continue_Qwen_Unreal_Agent_Setup.md) (not recommended).

## 10. Sonnet 4.5-oriented track

Sampling presets: [`config/lmstudio_sampling.json`](../config/lmstudio_sampling.json)

| Profile | Use |
|---------|-----|
| `qwen3_6_27b` | **Primary** — wrapper + MCP chat; Pass@K KPI |
| `qwen3_5_9b` | Compact MCP alternative; ctx 24576 |
| `gpt_oss_20b` | Experimental — ctx 32768, 2-file cap |
| `gpt_oss_small` | GPT OSS below 20B; ctx 32768 |
| `qwen3_8b` | Small Qwen; ctx 24576 |

N-turn prompts:
- [`prompts/lmstudio_reasoning_turn1_plan.md`](../prompts/lmstudio_reasoning_turn1_plan.md)
- [`prompts/lmstudio_reasoning_turn2_critique.md`](../prompts/lmstudio_reasoning_turn2_critique.md)
- [`prompts/lmstudio_reasoning_turn3_execute.md`](../prompts/lmstudio_reasoning_turn3_execute.md)

After UBT pass: `unreal_runtime_config_check`  
Genre gate: `unreal_genre_scope_validate`

Regression:

```powershell
.\rag.ps1 sonnet-tier-gate
.\rag.ps1 eval-reasoning
```

This is a target track, not a current Sonnet 4.5-grade claim. See [Sonnet45_Target_Plan.md](Sonnet45_Target_Plan.md).

Hybrid vs FTS A/B: see `qwen3_6_27b.abTuning` in sampling.json — run Phase 14 only after score ≥80.
