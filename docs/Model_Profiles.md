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

The checked-in `activeProfile` is the fallback. Qwen 3.8 27B is the highly
recommended primary validated model for the current Direct stack. Its v1.3.2
live E2E run completed a long real-project RAG/read/report workflow without the
prior mid-session context truncation. A user can explicitly select that profile
for a process:

```powershell
$env:UNREAL_RAG_MODEL_PROFILE = "qwen3_8_27b"
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
enforce path scope, receipt-first snapshot/CAS checks, atomic replacement,
locks, deletion approval, and output bounds. Every existing-file mutation must
explicitly pass its receipt or a valid raw `expectedHash`; session state is not
selected automatically.

The following controller surfaces are intentionally absent:

- mode-to-stage maps and turn presets;
- per-phase thinking, reasoning effort, or sampling changes;
- planner, retry, retrieval, and tool-order policy;
- model proxy or model routing metadata;
- context-compactor ownership or activation policy.

`--mode` and `--turn` remain accepted by the helper only as deprecated no-ops
for old callers. They produce a warning on stderr and never alter the resolved
sampling values.

## Current recommendation and compatibility profiles

| Profile | Context | Quant | Parallel | Status |
|---|---:|---|---:|---|
| `qwen3_8_27b` | 65536 | Q4_K_M | 1 | **Highly recommended** primary validated profile; v1.3.2 live E2E PASS; 262144 remains a hardware-dependent alternative |

Muse Glimmer is under testing and is not yet a validated recommendation or a
published checked-in profile. Qwen 3.5, Qwen 3.6 27B, and GPT-OSS aliases and
profiles may remain in `lmstudio_sampling.json` so an existing installation can
be inspected or reproduced, but they are historical compatibility/evaluation
entries and are not currently recommended. Other generic or compact entries are
unvalidated compatibility starting points, not product capability grades.

Quantization, context size, GPU offload, flash attention, and parallel requests
must still be validated on the actual machine and exact model artifact.

## Inspect static sampling

```powershell
python scripts/load_sampling_preset.py --sampling-profile qwen3_8_27b
python scripts/load_sampling_preset.py --sampling-profile qwen3_8_27b --show-profile
```

Changing sampling in LM Studio remains a user choice. The MCP and optional
compactor do not rewrite these values during a chat. Sampling profiles also do
not activate the host-owned chat plugin: verify its top-level switch is OFF per
chat by default. Enable that single switch only when a long chat needs bounded
continuity; handler invocation is the compaction activation boundary.

Historical Pass@K and model-comparison results, where still useful, are kept in
[`Model_Measurement_Results.md`](Model_Measurement_Results.md). They describe
specific model files, prompts, and test runs; they are not enforced by this
profile file.
