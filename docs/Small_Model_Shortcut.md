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

For 9B-focused RAG rebuilds, use compact chunk defaults:

```powershell
python scripts/build_rag_index.py --input data/unreal58/raw/*.jsonl --out-dir data/unreal58 --compact-profile
```

`--compact-profile` scales normal text chunks to `720/96` tokens while keeping symbol rows at `300/60` and module graph rows unchunked. Override with `--chunk-tokens`, `--overlap-tokens`, or `--compact-profile-scale` when benchmarking.

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

## Review And Asset Questions

For Qwen 3.5 9B, prefer short review prompts with one intent:

```powershell
.\rag.ps1 query -Mode auto -Question "프로젝트 코드리뷰 추가 개선사항 알려줘"
.\rag.ps1 query -Mode auto -Question "전체 프로젝트 구조 리뷰 해줘"
.\rag.ps1 query -Mode auto -Question "셰이더 관련 리뷰 해줘"
.\rag.ps1 query -Mode auto -Question "M_Blackhole_Core 머티리얼 노드 연결 파라미터 분석"
.\rag.ps1 query -Mode auto -Question "BP_PlayerController 블루프린트 구조 노드 핀 연결 확인"
```

The router maps these to `review`, `shader`, `material_analysis`, or `blueprint_verification`. Asset-like names such as `M_`, `MI_`, `BP_`, `ABP_`, and `/Game/...` get an exact metadata lookup boost before general FTS ranking.

`rag.ps1 query` prints compact retrieved context by default and hides the full system prompt to keep console output small. Add `-PrintPrompts` only when debugging prompt construction.
When `-Project` is omitted, query mode filters to the active `.uproject` from shared workspace config. Pass `-Project SomeProject` to inspect a different project inside the same index.

The LM Studio wrapper also compacts long retry history. When old chat turns exceed the profile history budget, it replaces dropped turns with a deterministic `Conversation compact summary` system message that keeps prior requests, validation/build failures, and touched file paths. This is project-side compaction and does not depend on Codex `.codex/config.toml`.

For Material or Blueprint graph claims, make sure the editor graph exporter plugin is installed and metadata is fresh:

```powershell
.\rag.ps1 install-editor-graph-plugin
.\rag.ps1 export-editor-metadata
```

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
- For UHT/UBT fixes, classify the first actionable error before editing: `UHT/reflection`, `include/module`, `linker`, `API signature`, `generated.h order`, or `syntax`.
- For code generation, keep the slice compile-ready and small: direct base-class header, `.generated.h` last, existing module style, and no new Build.cs dependency without evidence.

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
