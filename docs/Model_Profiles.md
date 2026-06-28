# Model Profiles and Agent Policy

Profiles live in `config/lmstudio_sampling.json`.

Switch via `activeProfile` or `UNREAL_RAG_MODEL_PROFILE`.

```powershell
$env:UNREAL_RAG_MODEL_PROFILE = "gpt_oss_20b"
python scripts/load_sampling_preset.py --show-profile
```

## Project Target

The project target is now a **Sonnet 4.5-oriented workflow track**. This is a target for the RAG/MCP/UBT system, not a claim that any local model is already Sonnet 4.5-grade.

## Agent Policy Fields

| Field | Purpose |
|-------|---------|
| ragBudgetScale | Context assembly scale |
| maxFilesPerEdit | Wrapper + orchestrator file cap |
| preferPatch | Patch vs full-file default |
| planningRequired | Require plan before execute |
| deepSearch | Allow broader retrieval and hybrid-friendly use |
| compileFixMaxAttempts | Wrapper retry cap when the CLI default is used |
| allowRefactorModes | Enable refactor_r* modes |
| jsonRepairStrict | Strict JSON bundle parsing |
| historyTurns | Message history cap hint |
| defaultTopK | Default RAG top_k when the CLI default is used |
| deltaTopK | Failure-specific retry RAG top_k |
| candidateLimitScale | Search candidate multiplier |
| targetTier | Internal quality target label |
| promptContract | Short prompt contract injected into wrapper runs |

## Profiles

| Profile | Use case |
|---------|----------|
| `qwen3_6_27b` | Main Sonnet 4.5-oriented local track; deeper retrieval, 5-attempt compile loop, 3-file patch cap |
| `qwen3_5_9b` | Qwen 3.5 9B compact track; top_k 5, two-file cap, strict patch loop |
| `qwen3_5_9b_deepseek_v4_flash` | Community Qwen3.5-9B-DeepSeek-V4-Flash GGUF track; top_k 6, flash reasoning-style compact patch loop |
| `gpt_oss_20b` | Compact Sonnet 4.5-oriented track; strict JSON, smaller patches, 4-attempt compile loop |
| `gpt_oss_20b_claude_opus_sonnet_reasoning_i1` | Community GPT OSS 20B Claude/Opus/Sonnet reasoning i1 GGUF track; stricter low-temp 5-attempt loop |
| `gpt_oss_small` | GPT OSS below 20B; one-file patch cap, minimal context, strict schema |
| `qwen3_8b` | Small 2-turn shortcut; two-file cap, no refactor modes |
| `gpt_oss_120b` | Large model track; deeper context, 4-file cap |
| `qwen_coder_large` | Codegen-heavy profile |
| `conservative_compile_fix` | Low-temperature compile-fix fallback |
| `review_only` | Inspect-only profile; maxFilesPerEdit=0 |

## Practical Tuning Direction

Small and 20B-class models improve most from:

- lower temperature
- strict JSON output
- smaller top_k
- fewer files per edit
- patch-first edits
- short retry context focused on the current build error
- no broad refactor modes

Qwen 3.6 27B improves most from:

- broader retrieval
- explicit critique/verification loop
- more failure-specific retry context
- strict no-op detection
- real-project Pass@1/Pass@3 measurement

## Resolve Policy

```powershell
python scripts/load_sampling_preset.py --show-profile
python scripts/load_sampling_preset.py --sampling-profile gpt_oss_20b --show-profile
python scripts/load_sampling_preset.py --sampling-profile qwen3_5_9b_deepseek_v4_flash --show-profile
python scripts/load_sampling_preset.py --sampling-profile gpt_oss_20b_claude_opus_sonnet_reasoning_i1 --show-profile
python scripts/load_sampling_preset.py --sampling-profile qwen3_6_27b --mode compile_fix
```

## Community Fine-Tune Notes

Community GGUF fine-tunes are supported as separate profiles because they often need different decoding behavior from their base family. These profiles are optimization targets, not quality guarantees. Always verify with UBT or Editor-side validation before claiming a fix is complete.
