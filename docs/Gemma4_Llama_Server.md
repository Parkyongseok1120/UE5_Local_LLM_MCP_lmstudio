# Gemma4-12B v2 Agentic — llama-server + LM Studio

Primary local MCP stack for **Gemma4-12B v2 Coding + Agentic Edition** (`gemma4-v2-Q6_K.gguf`).

Profile: `gemma4_12b_v2_agentic` in [`config/lmstudio_sampling.json`](../config/lmstudio_sampling.json).

## Quick start

```powershell
.\scripts\start_gemma4_v2_llama_server.ps1
```

Then in LM Studio: **OpenAI Compatible** → `http://localhost:18080/v1`.

## Command (reference)

```bat
llama-server -m gemma4-v2-Q6_K.gguf ^
  --model-draft MTP\gemma-4-12B-it-MTP-Q8_0.gguf ^
  --spec-type draft-mtp --spec-draft-n-max 4 ^
  -ngl 99 -ngld 99 -fa on --jinja ^
  --ctx-size 32768 --repeat-penalty 1.1 ^
  --host 0.0.0.0 --port 18080
```

## Environment variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `GEMMA4_V2_MODEL` | `gemma4-v2-Q6_K.gguf` | Main GGUF path |
| `GEMMA4_MTP_DRAFT` | `MTP\gemma-4-12B-it-MTP-Q8_0.gguf` | MTP draft model |
| `LLAMA_SERVER_EXE` | `llama-server` on PATH | llama.cpp server binary |
| `GEMMA4_CTX_SIZE` | `32768` | Context (do not go below 24576) |
| `GEMMA4_PORT` | `18080` | OpenAI API port |

## llama.cpp version pin

- **Verified:** build **b9553** (commit `9e3b928fd`)
- **Regression:** newer builds (b9702+) may crash loading MTP draft (`invalid vector subscript`)
- Pin b9553 when using draft MTP

## LM Studio settings

1. System prompt: [`prompts/lmstudio_gemma4_compact_system.md`](../prompts/lmstudio_gemma4_compact_system.md)
2. **Reasoning parsing:** Start `<|channel>thought` / End `<channel|>`
3. Jinja: `{%- set enable_thinking = true %}` (hybrid — off on execute turns via profile)
4. MCP: `MCP_ESSENTIAL_TOOLS=1` on both servers

## OOM mitigation

If Q6_K + MTP + 32k context OOM:

1. Try **Q4_K_M** quant for main model
2. Disable draft (`-NoDraft` on start script)
3. Do **not** set `--ctx-size` below **24576**

Do not use KV cache Q8 (`-ctk q8_0`) with MTP — reported 0% acceptance; keep F16 KV.

## Related

- [Gemma4_Model_Profile.md](Gemma4_Model_Profile.md) — 26B A4B + QAT
- [LMStudio_MCP_Tool_Discipline.md](LMStudio_MCP_Tool_Discipline.md) — chat tool rules
- [Community_Finetune_Model_Optimization.md](Community_Finetune_Model_Optimization.md) — v2 Agentic section
