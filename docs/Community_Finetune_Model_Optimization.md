# Community Fine-Tune Model Optimization

This guide covers the compact community fine-tune tracks:

- `qwen3_5_9b`
- `qwen3_5_9b_deepseek_v4_flash`
- `gpt_oss_20b`
- `gpt_oss_20b_claude_opus_sonnet_reasoning_i1`

These profiles are tuned to push small and medium models as far as possible inside the Unreal RAG/MCP/UBT workflow. They are not quality guarantees and should be validated with unseen real-project cases.

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

The target is Sonnet 4.5-oriented workflow behavior. Reaching that target requires proof on unseen real-project errors. The safest near-term goal is:

- GPT OSS 20B reasoning fine-tune: stronger first-shot compile diagnosis than base GPT OSS 20B
- Qwen3.5-9B DeepSeek V4 Flash fine-tune: better compact planning than base Qwen 9B
- both tracks: fewer no-op edits, fewer oversized rewrites, and better retry recovery

Do not call either model Sonnet 4.5-grade until the real-project validation plan supports it.
