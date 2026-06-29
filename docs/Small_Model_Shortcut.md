# Small Model Shortcut

Use this guide for GPT OSS below 20B, Qwen 8B-class models, Qwen 3.5 9B, and other compact local models. The goal is not to add a new agent framework; it is to force the existing MCP/RAG/wrapper tools into a smaller, repeatable loop.

## Recommended Default

Use Qwen 3.5 9B when VRAM is tight. It is usually more stable than base GPT OSS 20B for MCP tool calls and patch loops.

```powershell
$env:UNREAL_RAG_MODEL_PROFILE = "qwen3_5_9b"
```

Other compact profiles:

```powershell
$env:UNREAL_RAG_MODEL_PROFILE = "qwen3_8b"
$env:UNREAL_RAG_MODEL_PROFILE = "qwen3_5_9b_deepseek_v4_flash"
$env:UNREAL_RAG_MODEL_PROFILE = "gpt_oss_20b"
$env:UNREAL_RAG_MODEL_PROFILE = "gpt_oss_20b_claude_opus_sonnet_reasoning_i1"
$env:UNREAL_RAG_MODEL_PROFILE = "gpt_oss_small"
```

Confirm:

```powershell
python scripts/load_sampling_preset.py --show-profile
```

## Compact Contract

| Step | Role | Writes | Retrieval | Gate |
|------|------|--------|-----------|------|
| 1 | Active project | no | none | `unreal_get_active_project` returns the correct project |
| 2 | Agent plan | no | none | obey `toolPolicy`, `writeGate`, `checkpoints`, `stopConditions` |
| 3 | Evidence | no | top_k 4-8 | use FTS/default search first; hybrid only if profile says deep search |
| 4 | File read | no | target files only | every edited file must be read first |
| 5 | Minimal patch | yes if allowed | current file state only | one or two files; prefer `replace_in_file` |
| 6 | Build/verify | no | UBT/log output | never claim compile success without build evidence |
| 7 | Failure retry | yes if allowed | delta top_k 2-4 | search only the current error context |

Skip broad critique turns unless the model has enough context. Compact models degrade when the prompt contains unrelated docs, old errors, or repeated already-applied changes.

## Rules

- Use Essential Tools mode for LM Studio chat.
- Call `unreal_agent_plan` before edits.
- Do not write when `writeGate.writesAllowed=false`.
- Prefer patches over full file rewrites.
- Keep edits to one subsystem or one compile surface.
- Avoid broad refactor modes on compact profiles.
- Use FTS/default search first; use hybrid only for larger/deep-search profiles.
- Treat a pass without UBT/Editor validation as proposed, not proven.
- On a failed build, patch the first actionable error surface only.

## Profile Reference

Values should match `config/lmstudio_sampling.json`.

| Profile | Context | top_k | delta top_k | Max files | Attempts |
|---------|---------|-------|-------------|-----------|----------|
| `gpt_oss_small` | 32768 | 4 | 2 | 2 | 3 |
| `qwen3_8b` | 24576 | 5 | 3 | 2 | 3 |
| `qwen3_5_9b` | 24576 | 5 | 3 | 2 | 4 |
| `qwen3_5_9b_deepseek_v4_flash` | 24576 | 6 | 3 | 2 | 4 |
| `gpt_oss_20b` | 32768 | 5 | 3 | 2 | 3 |
| `gpt_oss_20b_claude_opus_sonnet_reasoning_i1` | 32768 | 8 | 4 | 2 | 5 |

Base GPT OSS 20B still has variable JSON/tool stability. Prefer one-file patch turns for that profile even when the config allows two files.

The compact profiles are Sonnet 4.5-oriented workflow targets, not Sonnet 4.5 claims.
