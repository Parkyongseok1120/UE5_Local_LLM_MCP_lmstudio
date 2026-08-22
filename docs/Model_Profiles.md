# LM Studio Load and Chat Profiles

[`config/lmstudio_sampling.json`](../config/lmstudio_sampling.json) contains
static recommendations for a model that the user has already chosen in LM
Studio. A profile does not load or switch a model, classify a request, create a
plan, choose tools, order tools, control the context compactor, or decide when a
response is finished.

The model selected in LM Studio owns reasoning, tool selection and order,
stopping, and the final answer. All checked-in profiles use
[`prompts/lmstudio_direct_model_system.md`](../prompts/lmstudio_direct_model_system.md).

## Select a recommendation

The checked-in `activeProfile` is the fallback. A user can explicitly select a
different recommendation for a process:

```powershell
$env:UNREAL_RAG_MODEL_PROFILE = "qwen3_5_9b"
python scripts/load_sampling_preset.py --show-profile
```

The resolver can also map an LM Studio model id or GGUF filename to a known
profile:

```powershell
python scripts/load_sampling_preset.py --model "qwen/qwen3.8-27b" --show-profile
```

Alias resolution is only a lookup convenience. It does not call LM Studio,
change the loaded model, proxy a request, or select another model during a
chat.

## Schema

| Field | Meaning |
|---|---|
| `contextLength` | Recommended LM Studio load context |
| `contextLengthAlternatives` | Optional memory/capacity alternatives for the user to evaluate |
| `quantDefault` | Recommended starting quantization |
| `recommendedParallelRequests` | Recommended LM Studio server request concurrency |
| `recommendedSystemPrompt` | Direct system prompt path |
| `sampling` | One static chat recommendation; it does not change by task, phase, retry, or turn |
| `writeSafety.maxFilesPerEdit` | Compatibility limit consumed by the legacy compile wrapper |
| `writeSafety.preferPatchOverFullFile` | Compatibility safety preference consumed by the legacy compile wrapper |
| `notes` | Hardware or model-specific caution for the user |

`writeSafety` cannot authorize a write. Direct MCP write tools independently
enforce path scope, exact-read/CAS checks, atomic replacement, locks, deletion
approval, and output bounds.

The following controller surfaces are intentionally absent:

- mode-to-stage maps and turn presets;
- per-phase thinking, reasoning effort, or sampling changes;
- planner, retry, retrieval, and tool-order policy;
- model proxy or model routing metadata;
- context-compactor ownership or activation policy.

`--mode` and `--turn` remain accepted by the helper only as deprecated no-ops
for old callers. They produce a warning on stderr and never alter the resolved
sampling values.

## Included profiles

| Profile | Context | Quant | Parallel | Notes |
|---|---:|---|---:|---|
| `qwen3_8_27b` | 65536 | Q4_K_M | 1 | Default; 262144 is listed as a hardware-dependent alternative |
| `qwen3_6_27b` | 32768 | Q4_K_M | 1 | 65536 alternative |
| `qwen3_8b` | 24576 | Q4_K_M | 1 | Compact load |
| `qwen3_5_9b` | 24576 | Q4_K_M | 1 | 32768 alternative where supported |
| `qwen3_5_9b_deepseek_v4_flash` | 140032 | Q4_K_M | 1 | 65536 portable and 262144 native alternatives |
| `generic_large` | 49152 | Q5_K_M | 1 | Generic large-model starting point |
| `gpt_oss_20b` | 32768 | Q4_K_M | 1 | Validate the exact GGUF/tool-call behavior |
| `gpt_oss_20b_claude_opus_sonnet_reasoning_i1` | 32768 | Q4_K_M | 1 | Community model starting point |
| `gpt_oss_small` | 32768 | Q4_K_M | 1 | Compact GPT OSS starting point |
| `gpt_oss_120b` | 32768 | Q5_K_M | 1 | Large GPT OSS starting point |
| `qwen_coder_large` | 32768 | Q4_K_M | 1 | Generic coder starting point |

These are starting recommendations, not capability grades or quality
guarantees. Quantization, context size, GPU offload, flash attention, and
parallel requests must be validated on the actual machine and exact model
artifact.

## Inspect static sampling

```powershell
python scripts/load_sampling_preset.py --sampling-profile qwen3_8_27b
python scripts/load_sampling_preset.py --sampling-profile qwen3_8_27b --show-profile
```

Changing sampling in LM Studio remains a user choice. The MCP and optional
compactor do not rewrite these values while a task is running.

Historical Pass@K and model-comparison results, where still useful, are kept in
[`Model_Measurement_Results.md`](Model_Measurement_Results.md). They describe
specific model files, prompts, and test runs; they are not enforced by this
profile file.
