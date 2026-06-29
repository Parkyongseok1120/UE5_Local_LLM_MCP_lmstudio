# LM Studio MCP Tool Discipline

Guide for **LM Studio basic chat** with `unreal-rag` + `unreal-agent` MCP. This is the chat path, not the automated wrapper path.

## Wrapper vs Chat

| Path | Orchestrator | Enforcement |
|------|--------------|-------------|
| `lmstudio_unreal_wrapper.py` | Yes; injects plan JSON | JSON schema, edit limits, static validation, UBT loop |
| LM Studio chat | Yes, if the model calls `unreal_agent_plan` | System prompt, Essential Tools, tool descriptions, returned `writeGate` |

Weak local models fail when too many tools are exposed. Use **Essential Tools** mode for chat.

## Essential Tools Mode

Set this in both MCP server env blocks in `%USERPROFILE%\.lmstudio\mcp.json`:

```json
"MCP_ESSENTIAL_TOOLS": "1"
```

Re-run installer or:

```powershell
python scripts/patch_mcp_config.py
```

Restart LM Studio after changes.

### unreal-rag (8 tools)

- `unreal_get_active_project`
- `unreal_set_active_project`
- `unreal_rag_health`
- `unreal_agent_plan` - call first after `unreal_get_active_project`
- `unreal_rag_search`
- `unreal_symbol_lookup`
- `unreal_agent_session`
- `unreal_rag_capabilities`

### unreal-agent (10 tools)

- `get_workspace_info`, `get_active_project`
- `list_directory`, `read_file`, `read_file_range`
- `replace_in_file`, `write_file`, `search_files`
- `build_unreal_project`, `read_unreal_logs`

## Required Chat Order

1. `unreal_get_active_project`
2. `unreal_agent_plan`
3. Follow returned `toolPolicy`
4. Obey returned `writeGate`
5. Use returned `checkpoints` before moving to the next tool
6. Stop according to returned `stopConditions`

For edit tasks:

- Do not write when `writeGate.writesAllowed=false`.
- Read every target file before `replace_in_file` or `write_file`.
- Prefer `replace_in_file` with `expectedOccurrences=1` for existing files.
- Use `write_file` mainly for new or small files.
- Run `build_unreal_project` after C++ or `Build.cs` edits.
- On UBT failure, search only the current error context with `mode=compile_fix`, then patch the smallest failing surface.

## Session Bootstrap

Paste [`prompts/lmstudio_session_bootstrap.md`](../prompts/lmstudio_session_bootstrap.md) as the **first user message** every chat.

## Model and System Prompt

| Profile / model | System prompt |
|-----------------|---------------|
| `gemma4_12b_v2_agentic` (primary) | [`lmstudio_gemma4_compact_system.md`](../prompts/lmstudio_gemma4_compact_system.md) + base |
| `gemma_4_26b_a4b_it_q4_k_m`, `gemma_4_12b_qat` | Same Gemma compact |
| `gpt_oss_*` | [`lmstudio_gpt_oss_compact_system.md`](../prompts/lmstudio_gpt_oss_compact_system.md) + base |
| `qwen3_5_9b`, `qwen3_8b` | [`lmstudio_qwen35_9b_compact_system.md`](../prompts/lmstudio_qwen35_9b_compact_system.md) + base |
| `qwen3_6_27b` | [`lmstudio_unreal_agent_system.md`](../prompts/lmstudio_unreal_agent_system.md) |

Always include [`lmstudio_compact_mcp_base.md`](../prompts/lmstudio_compact_mcp_base.md) for one-tool-per-turn and read-before-write discipline.

## Model-Specific Notes

### Gemma4-12B v2 Agentic

- Thinking hybrid: plan/analyze ON (`temp 1.0`), execute/patch OFF (`temp 0.1`).
- llama-server: Q6_K + MTP, `--jinja`, `repeat_penalty 1.1`, llama.cpp b9553. See [Gemma4_Llama_Server.md](Gemma4_Llama_Server.md).
- Keep Unreal API names, symbols, and paths in English.

### GPT OSS 20B

- JSON argument drift is common; prefer one file per patch turn even though the profile allows 2.
- Context is 32768 in sampling profile.

### Qwen 3.5 9B

- Keep API names and paths in English; Korean summaries are OK.
- Context should be at least 24576 for compact profiles.

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| Model answers without tools | Resend session bootstrap; check Essential Tools ON |
| Wrong paths (`Documents` vs project) | Call `unreal_get_active_project`; use returned root |
| Writes on review/runtime tasks | Re-call `unreal_agent_plan`; obey `writeGate.writesAllowed=false` |
| Hallucinated analysis | Force `read_file` before claims or edits |
| Repeated no-op patch | Re-read file, patch only missing current text, set `expectedOccurrences=1` |
| Tool not in list | Essential mode hides advanced tools; use wrapper/Cline for clangd/graph |

## Sampling Metadata

Profiles may include:

- `mcpEssentialTools: true`
- `recommendedSystemPrompt: "prompts/..."`
- `mcpToolDiscipline: "one_tool_per_turn"`

Inspect:

```powershell
python scripts/load_sampling_preset.py --sampling-profile gemma4_12b_v2_agentic --show-profile
```
