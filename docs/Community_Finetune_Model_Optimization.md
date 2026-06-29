# Community Fine-Tune Model Optimization

This guide covers the compact community fine-tune tracks:

- **`gemma4_12b_v2_agentic`** — primary MCP chat (see below)
- `qwen3_5_9b`
- `qwen3_5_9b_deepseek_v4_flash`
- `gpt_oss_20b`
- `gpt_oss_20b_claude_opus_sonnet_reasoning_i1`

These profiles are tuned to push small and medium models as far as possible inside the Unreal RAG/MCP/UBT workflow. They are not quality guarantees and should be validated with unseen real-project cases.

## Gemma4-12B v2 Agentic (primary MCP)

Community **Coding + Agentic Edition** on `google/gemma-4-12B-it`. Profile: `gemma4_12b_v2_agentic`.

```powershell
$env:UNREAL_RAG_MODEL_PROFILE = "gemma4_12b_v2_agentic"
.\scripts\start_gemma4_v2_llama_server.ps1
```

- llama-server **Q6_K** + MTP draft, `--jinja`, `repeat_penalty 1.1`
- llama.cpp **b9553** pin for MTP
- Thinking **hybrid** (plan on, execute off)
- LM Studio OpenAI API → `http://localhost:18080/v1`
- MCP: `MCP_ESSENTIAL_TOOLS=1` + [session bootstrap](../prompts/lmstudio_session_bootstrap.md)

Details: [Gemma4_Llama_Server.md](Gemma4_Llama_Server.md), [LMStudio_MCP_Tool_Discipline.md](LMStudio_MCP_Tool_Discipline.md).

## Profile Selection

```powershell
$env:UNREAL_RAG_MODEL_PROFILE = "qwen3_5_9b"
$env:UNREAL_RAG_MODEL_PROFILE = "qwen3_5_9b_deepseek_v4_flash"
$env:UNREAL_RAG_MODEL_PROFILE = "gpt_oss_20b"
$env:UNREAL_RAG_MODEL_PROFILE = "gpt_oss_20b_claude_opus_sonnet_reasoning_i1"
```

Confirm:

```powershell
python scripts/load_sampling_preset.py --show-profile
```

## Why These Profiles Are Different

Small and medium models usually fail from:

- too much unrelated context
- output schema drift
- over-editing too many files
- repeating already-applied changes
- losing the current compile error during retries

The profiles counter this with:

- reduced RAG assembly and per-row context size
- lower temperature
- strict JSON patch contracts
- one- or two-file edit caps
- failure-specific retry context
- no broad refactor modes by default

## Recommended Eval Loop

Run the same cases for each profile:

```powershell
.\rag.ps1 eval-pass-at-k -Live -RequireLive
.\rag.ps1 eval-project-review -Live -RequireLive
.\rag.ps1 report-tier-kpi
```

Then run unseen real-project cases:

```powershell
.\rag.ps1 summarize-real-project-eval -Question path\to\filled-real-project-eval.json
```

Report:

- Pass@1
- Pass@3
- final Pass@K
- attempts used
- failure category
- whether UBT or Editor validation passed

## Practical Expectations

The target is Sonnet 4.5-oriented workflow behavior. Reaching that target requires proof on unseen real-project errors.

**Stability ranking (field use, MCP + UBT loop):**

1. `qwen3_6_27b` — main track when VRAM allows
2. `qwen3_5_9b` / `qwen3_5_9b_deepseek_v4_flash` — best compact default
3. `gpt_oss_20b_claude_opus_sonnet_reasoning_i1` — may beat base GPT OSS 20B; still experimental
4. `gpt_oss_20b` — **variable**; JSON/schema drift and no-op edits are common; profile tightened to one-file patches

Base GPT OSS 20B is not recommended as the primary agent model until your own Pass@1 on real compile errors is acceptable.

The safest near-term goals are:

- Qwen3.5-9B DeepSeek V4 Flash fine-tune: better compact planning than base Qwen 9B
- GPT OSS 20B reasoning fine-tune: may improve first-shot compile diagnosis vs base 20B — not proven stable vs Qwen
- all compact tracks: fewer no-op edits when scope is one file and prompts stay short

Do not call either model Sonnet 4.5-grade until the real-project validation plan supports it.
