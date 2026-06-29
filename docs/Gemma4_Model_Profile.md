# Gemma 4 — Model Profiles (v2 Agentic + 26B A4B + QAT)

## Primary: Gemma4-12B v2 Agentic

Profile id: **`gemma4_12b_v2_agentic`**  
Preset: [`config/lmstudio_sampling.json`](../config/lmstudio_sampling.json)  
System prompt: [`prompts/lmstudio_gemma4_compact_system.md`](../prompts/lmstudio_gemma4_compact_system.md)  
Inference: [Gemma4_Llama_Server.md](Gemma4_Llama_Server.md) (llama-server Q6_K + MTP)

### Activate

```powershell
$env:UNREAL_RAG_MODEL_PROFILE = "gemma4_12b_v2_agentic"
python scripts/load_sampling_preset.py --show-profile
```

Aliases: `gemma4-v2`, `gemma4-12b-v2`, `gemma4-12b-v2-agentic`, `gemma-4-12b-v2`

### Policy

| Field | Value |
|-------|-------|
| contextLength | 32768 |
| maxFilesPerEdit | 2 |
| thinking | **hybrid** (plan/analyze on, execute off) |
| quantDefault | Q6_K |
| inferenceBackend | llama-server |
| llamaCppPin | b9553 |

### LM Studio (OpenAI compatible → localhost:18080)

1. Reasoning parser: Start `<|channel>thought` / End `<channel|>`
2. `--jinja` on llama-server (required)
3. `repeat_penalty` **1.1**
4. MCP `MCP_ESSENTIAL_TOOLS=1`

---

## Secondary: Gemma 4 26B A4B

Profile id: **`gemma_4_26b_a4b_it_q4_k_m`**  
System prompt: same Gemma compact file

### Activate

```powershell
$env:UNREAL_RAG_MODEL_PROFILE = "gemma_4_26b_a4b_it_q4_k_m"
```

Aliases: `gemma-4-26b-a4b-it`, `gemma4-26b-a4b-it`

### Policy

| Field | Value |
|-------|-------|
| contextLength | 32768 |
| maxFilesPerEdit | 2 |
| thinking | **hybrid** |
| quantDefault | Q4_K_M |

### LM Studio settings

Same reasoning parser as v2. Use thinking **hybrid** per turn presets — not globally off.

---

## Optional: Gemma 4 12B QAT (Google official)

Profile id: **`gemma_4_12b_qat`** — LM Studio one-click when v2 Agentic is not used.

| Field | Value |
|-------|-------|
| contextLength | 32768 |
| maxFilesPerEdit | 2 |
| thinking | hybrid |

Official: [lmstudio.ai/models/google/gemma-4-12b-qat](https://lmstudio.ai/models/google/gemma-4-12b-qat)

---

## Wrapper sampling

```powershell
python scripts/load_sampling_preset.py --sampling-profile gemma4_12b_v2_agentic --mode agent_edit
python scripts/load_sampling_preset.py --sampling-profile gemma_4_26b_a4b_it_q4_k_m --turn plan
```

`chat_lmstudio` passes `enable_thinking: true` when preset `thinking` is `on`.

## Expectations

- **v2 Agentic:** best local MCP track — read-before-act, multi-step tools
- **26B A4B:** heavier VRAM; similar hybrid contract
- **English-centric** — Unreal facts from RAG + `read_file`
- Always verify with `build_unreal_project` before claiming success

See [LMStudio_MCP_Tool_Discipline.md](LMStudio_MCP_Tool_Discipline.md).
