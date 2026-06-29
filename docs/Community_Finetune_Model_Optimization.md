# Community Fine-Tune Model Optimization

This guide covers the compact community fine-tune tracks:

- **`qwen3_6_27b`** — primary wrapper + Pass@K KPI
- `qwen3_5_9b`
- `qwen3_5_9b_deepseek_v4_flash`
- `gpt_oss_20b`
- `gpt_oss_20b_claude_opus_sonnet_reasoning_i1`

These profiles are tuned to push small and medium models as far as possible inside the Unreal RAG/MCP/UBT workflow. They are not quality guarantees and should be validated with unseen real-project cases.

## Profile Selection

```powershell
$env:UNREAL_RAG_MODEL_PROFILE = "qwen3_6_27b"
$env:UNREAL_RAG_MODEL_PROFILE = "qwen3_5_9b"
$env:UNREAL_RAG_MODEL_PROFILE = "qwen3_5_9b_deepseek_v4_flash"
$env:UNREAL_RAG_MODEL_PROFILE = "gpt_oss_20b"
$env:UNREAL_RAG_MODEL_PROFILE = "gpt_oss_20b_claude_opus_sonnet_reasoning_i1"
```

Confirm:

```powershell
python scripts/load_sampling_preset.py --show-profile
```

MCP chat: `MCP_ESSENTIAL_TOOLS=1` + [session bootstrap](../prompts/lmstudio_session_bootstrap.md). See [LMStudio_MCP_Tool_Discipline.md](LMStudio_MCP_Tool_Discipline.md).

## Qwen 3.6 27B (primary)

- Profile: `qwen3_6_27b`
- System prompt: [`lmstudio_qwen36_27b_compact_system.md`](../prompts/lmstudio_qwen36_27b_compact_system.md) + compact base
- Pass@K live compile-fix is the primary agent KPI
- For `module_fix` / `Build.cs` / `GameplayTags`: patch `*.Build.cs`, not explanation-only replies

## Qwen 3.5 9B / DeepSeek V4 Flash

- Compact MCP alternative when VRAM is tight
- Thinking OFF; one tool per turn
- Generally more stable tool-call behavior than base GPT OSS 20B

## GPT OSS community fine-tunes

- Variable JSON/tool stability — prefer one-file patch turns
- Context 32768 for all `gpt_oss_*` profiles

## Validation

```powershell
python scripts/eval_pass_at_k.py --live --require-live
python scripts/bench_lmstudio_mcp.py
```

See also [Model_Profiles.md](Model_Profiles.md) and [Small_Model_Shortcut.md](Small_Model_Shortcut.md).
