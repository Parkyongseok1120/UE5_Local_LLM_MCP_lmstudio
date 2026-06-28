# Small Model Shortcut

Use this guide for GPT OSS below 20B, Qwen 8B-class models, and other compact local models. The goal is to make them useful in the same workflow as Qwen 3.6 27B, but with tighter limits.

## Profile Setup

For GPT OSS 20B:

```powershell
$env:UNREAL_RAG_MODEL_PROFILE = "gpt_oss_20b"
```

For GPT OSS 20B Claude/Opus/Sonnet reasoning i1 community GGUF:

```powershell
$env:UNREAL_RAG_MODEL_PROFILE = "gpt_oss_20b_claude_opus_sonnet_reasoning_i1"
```

For GPT OSS below 20B:

```powershell
$env:UNREAL_RAG_MODEL_PROFILE = "gpt_oss_small"
```

For Qwen 8B:

```powershell
$env:UNREAL_RAG_MODEL_PROFILE = "qwen3_8b"
```

For Qwen 3.5 9B:

```powershell
$env:UNREAL_RAG_MODEL_PROFILE = "qwen3_5_9b"
```

For Qwen3.5-9B-DeepSeek-V4-Flash-GGUF community fine-tune:

```powershell
$env:UNREAL_RAG_MODEL_PROFILE = "qwen3_5_9b_deepseek_v4_flash"
```

Confirm the active profile:

```powershell
python scripts/load_sampling_preset.py --show-profile
```

## Compact Contract

| Step | Role | Writes | Retrieval | Notes |
|------|------|--------|-----------|-------|
| 1 | Compact evidence plan | no | top_k 4-6 | Identify exact file/module/symbol |
| 2 | Minimal patch | yes | top_k 4-6 | One or two files only |
| 3 | Failure retry | yes | delta top_k 2-4 | Use only current build error context |

Skip broad critique turns unless the model has enough context. Compact models degrade when the prompt contains too many unrelated docs.

## Rules

- Prefer patches over full file rewrites.
- Keep edits to one subsystem or one compile surface.
- Avoid broad refactor modes.
- Use FTS/default search first; use hybrid only when a larger model/profile is active.
- Treat a pass without UBT/Editor validation as proposed, not proven.

## Profile Reference

| Profile | Context | top_k | delta top_k | Max files | Attempts |
|---------|---------|-------|-------------|-----------|----------|
| `gpt_oss_small` | 8192 | 4 | 2 | 1 | 3 |
| `qwen3_8b` | 8192 | 5 | 3 | 2 | 3 |
| `qwen3_5_9b` | 16384 | 5 | 3 | 2 | 4 |
| `qwen3_5_9b_deepseek_v4_flash` | 16384 | 6 | 3 | 2 | 4 |
| `gpt_oss_20b` | 16384 | 7 | 4 | 2 | 4 |
| `gpt_oss_20b_claude_opus_sonnet_reasoning_i1` | 16384 | 8 | 4 | 2 | 5 |
| `qwen3_6_27b` | 32768 | 10 | 5 | 3 | 5 |

The compact profiles are Sonnet 4.5-oriented workflow targets, not Sonnet 4.5 claims.
