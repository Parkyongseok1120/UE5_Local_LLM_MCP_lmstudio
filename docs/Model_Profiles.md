# Model Profiles and Agent Policy

Profiles live in `config/lmstudio_sampling.json`.

Switch via `activeProfile` or `UNREAL_RAG_MODEL_PROFILE`.

## agentPolicy fields

| Field | Purpose |
|-------|---------|
| ragBudgetScale | Context assembly scale |
| maxFilesPerEdit | Wrapper + orchestrator file cap |
| preferPatch | Patch vs full-file default |
| planningRequired | Require plan before execute |
| deepSearch | Allow hybrid / larger retrieval |
| compileFixMaxAttempts | Wrapper retry cap |
| allowRefactorModes | Enable refactor_r* modes |
| jsonRepairStrict | Strict JSON bundle parsing |
| historyTurns | Message history cap hint |

## Profiles

| Profile | Use case |
|---------|----------|
| qwen3_6_27b | Default Sonnet-track; patch-first, 3 files |
| qwen3_8b | Small 2-turn shortcut |
| gpt_oss_20b | Strict schema, 2 files |
| gpt_oss_120b | Deeper context, 4 files |
| qwen_coder_large | Codegen-heavy |
| conservative_compile_fix | Low-temp compile fix |
| review_only | maxFilesPerEdit=0 |

Resolve policy:

```powershell
python scripts/load_sampling_preset.py --show-profile
```
