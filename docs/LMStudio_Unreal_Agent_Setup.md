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
- `unreal-rag` — search, health, compile loop
- `unreal-agent` — read/write files, UBT build

After path changes:

```powershell
powershell -File $HOME\.lmstudio\scripts\patch_mcp_runtime_paths.ps1
```

Restart LM Studio so MCP servers reload.

## 3. System prompt

Paste [`prompts/lmstudio_unreal_agent_system.md`](../prompts/lmstudio_unreal_agent_system.md) into LM Studio **System Prompt**.

## 4. Session start (every chat)

1. `unreal_get_active_project`
2. If wrong project: `unreal_set_active_project` or `unreal_open_project_picker`
3. `unreal_rag_health` once
4. For implementation: `unreal_rag_search` **before** `write_file`

## 5. Standard loop

```
RAG search -> read_file -> write_file -> build_unreal_project -> read log on failure
```

Rules:
- Do **not** paste full `.cpp` in chat when MCP write is available.
- Do **not** say "done" without build output.
- `write_file` runs static validation when `VALIDATE_ON_WRITE=1` (blocks bad includes like `Game/Framework/`).

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
| `qwen3_6_27b` | Main local track; broader retrieval and 5-attempt compile loop |
| `gpt_oss_20b` | Compact 20B track; strict JSON and two-file patch cap |
| `gpt_oss_small` | GPT OSS below 20B; one-file patch cap |
| `qwen3_8b` | Small Qwen track; two-turn shortcut |

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
