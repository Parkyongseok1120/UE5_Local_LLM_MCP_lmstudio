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

## Forbidden Tools

Do not use LM Studio's JavaScript/code sandbox for Unreal project file work:

- `run_javascript`
- `lmstudio/js-code-sandbox`
- `Deno.readTextFile` / `Deno.writeTextFile`
- Node `fs` / CommonJS `require`

That sandbox has its own working directory and is not rooted at the active `.uproject`. Project file I/O must go through `unreal-agent`: `read_file_range`, `read_file`, `replace_in_file`, and `write_file` only for brand-new files.

If LM Studio auto-approves this sandbox, remove these patterns from `%USERPROFILE%\.lmstudio\settings.json` `chat.skipToolConfirmationPatterns`:

```json
"lmstudio/js-code-sandbox:run_javascript",
"lmstudio/js-code-sandbox:*"
```

Restart LM Studio after changing that setting. If the plugin is still shown to the model, hide or disable the JavaScript/TypeScript Code Sandbox plugin in LM Studio for Unreal coding chats.

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
- Use `write_file` only for brand-new files. Existing `.h`, `.hpp`, `.cpp`, `.c`, `.cc`, `.cxx`, and `.cs` files are patch-only.
- Run `build_unreal_project` after C++ or `Build.cs` edits.
- On UBT failure, search only the current error context with `mode=compile_fix`, then patch the smallest failing surface.

## Session Bootstrap

Paste [`prompts/lmstudio_session_bootstrap.md`](../prompts/lmstudio_session_bootstrap.md) as the **first user message** every chat.

## Model and System Prompt

| Profile / model | System prompt |
|-----------------|---------------|
| `qwen3_6_27b` (primary) | [`lmstudio_qwen36_27b_compact_system.md`](../prompts/lmstudio_qwen36_27b_compact_system.md) + base |
| `gpt_oss_*` | [`lmstudio_gpt_oss_compact_system.md`](../prompts/lmstudio_gpt_oss_compact_system.md) + base |
| `qwen3_5_9b`, `qwen3_8b` | [`lmstudio_qwen35_9b_compact_system.md`](../prompts/lmstudio_qwen35_9b_compact_system.md) + base |

Always include [`lmstudio_compact_mcp_base.md`](../prompts/lmstudio_compact_mcp_base.md) for one-tool-per-turn and read-before-write discipline.

## Model-Specific Notes

### Qwen 3.6 27B

- Primary wrapper + Pass@K KPI model.
- Enable Essential Tools; use compact system prompt + base rules.
- **Thinking leak:** disable visible reasoning in LM Studio or use execute/`compile_fix_patch` turns with thinking OFF. Do not print "thinking process" in visible chat.
- For `module_fix` / `GameplayTags` / `Build.cs` errors: read full `*.Build.cs` from project state, then patch the file — do not answer with explanation only.

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
| Model calls `run_javascript` / `js-code-sandbox` | Start a new chat with the bootstrap prompt, remove sandbox auto-approval, and hide/disable the sandbox plugin if available |
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
python scripts/load_sampling_preset.py --sampling-profile qwen3_6_27b --show-profile
python scripts/bench_lmstudio_mcp.py
```
