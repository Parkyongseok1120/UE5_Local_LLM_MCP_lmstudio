# Small Model Shortcut (2-Turn Workflow)

Use this guide when running **7–14B models** (e.g. Qwen3-8B) via LM Studio with Unreal58-RAG. Large 27B+ models should use the full 3-turn plan → critique → execute flow.

## Profile setup

1. Set the sampling profile:

   ```powershell
   $env:UNREAL_RAG_MODEL_PROFILE = "qwen3_8b"
   ```

   Or edit `config/lmstudio_sampling.json` → `"activeProfile": "qwen3_8b"`.

2. Confirm scale (assembly budget is halved for 8B):

   ```powershell
   python scripts/load_sampling_preset.py --show-profile
   ```

   Expect `assemblyBudgetScale: 0.5` — RAG context assembly stays within ~8k context.

## 2-turn contract

| Turn | Role | Thinking | Max files | MCP modes |
|------|------|----------|-----------|-----------|
| **1** | Compact plan + RAG evidence | OFF | 0 (no writes) | `refactor_r0`, `unreal_rag_search` hybrid **off**, top_k **4** |
| **2** | Patch + build | OFF | **≤2** | `agent_edit` / `execute`, compile loop if needed |

Skip the critique turn unless you have spare context. Small models degrade on long multi-turn chains.

## RAG search settings

- `hybrid: false` — FTS-only saves tokens and latency
- `top_k: 4` — fewer chunks, tighter assembly
- Prefer `mode=codegen` or `refactor_r0` for Turn 1
- Filter by `source` when you know the bucket (e.g. `unreal_source`, `project_guideline`)

## Slice rules

- **≤2 files** per execute turn (profile preset caps `maxTokens` at 2048)
- One subsystem or component at a time
- Run `unreal_refactor_plan_validate` on Turn 1 output before Turn 2 if using refactor modes

## When not to use this shortcut

- Multi-file refactors (>2 files)
- Genre-scoped prototypes (action_combat Must Have checks)
- Runtime/config checklist work (needs full context)
- Korean + English hybrid review (use 27B or EXAONE hybrid instead)

## Quick smoke test

```powershell
$env:UNREAL_RAG_MODEL_PROFILE = "qwen3_8b"
.\rag.ps1 query -Mode refactor_r0 -Question "UActorComponent BeginPlay override pattern"
.\rag.ps1 wrapper -Mode agent_edit -Question "Add a simple UActorComponent that logs BeginPlay" -SkipBuild
```

## Profile reference

| Profile | Context | assemblyBudgetScale | Turns |
|---------|---------|---------------------|-------|
| `qwen3_8b` | 8192 | 0.5 | 2-turn shortcut |
| `qwen3_6_27b` | 32768 | 1.0 | 3-turn (plan/critique/execute) |
| `generic_large` | 49152 | 1.25 | 3-turn + deep hybrid |

See also: `config/lmstudio_sampling.json`, `docs/LMStudio_Unreal_Agent_Setup.md`.
