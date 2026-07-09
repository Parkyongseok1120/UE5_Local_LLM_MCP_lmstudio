# Model Profiles and Agent Policy

Profiles live in `config/lmstudio_sampling.json`.

Switch via `activeProfile` or `UNREAL_RAG_MODEL_PROFILE`.

```powershell
$env:UNREAL_RAG_MODEL_PROFILE = "gpt_oss_20b"
python scripts/load_sampling_preset.py --show-profile
```

## Project Target

The project target is now a **Sonnet 4.5-oriented workflow track**. The latest Qwen 3.6 27B run reaches the target on this narrow internal 36-case UE 5.8 holdout, but this remains a RAG/MCP/UBT workflow result, not a claim that the local model is generally Sonnet 4.5-grade.

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
| mcpEssentialTools | Recommend Essential Tools mode for LM Studio chat |
| recommendedSystemPrompt | Path to compact system prompt |
| mcpToolDiscipline | e.g. `one_tool_per_turn` |

## Profiles

| Profile | Use case |
|---------|----------|
| `qwen3_6_27b` | **Primary** — wrapper + MCP chat; current 36-case live Pass@1/Pass@K KPI track; Essential Tools |
| `qwen3_5_9b` | Qwen 3.5 9B compact MCP; ctx 24576, Essential Tools |
| `qwen3_5_9b_deepseek_v4_flash` | Community flash GGUF; compact MCP |
| `qwen3_8b` | Small compact; ctx 24576, 2-file cap |
| `gpt_oss_20b` | **Variable stability** — ctx 32768, 2-file cap; experimental MCP |
| `gpt_oss_20b_claude_opus_sonnet_reasoning_i1` | Community GPT OSS 20B reasoning i1; ctx 32768 |
| `gpt_oss_small` | GPT OSS below 20B; ctx 32768, 2-file cap |
| `gpt_oss_120b` | Large GPT OSS; ctx 32768, 2-file cap |
| `qwen_coder_large` | Codegen-heavy; 2-file cap |
| `conservative_compile_fix` | Low-temperature compile-fix fallback; ctx 24576 |
| `review_only` | Inspect-only; maxFilesPerEdit=0, ctx 24576 |

**Context rules:** minimum **24576** for all profiles; **`gpt_oss_*`** at **32768**.

## Practical Tuning Direction

**Recommended MCP chat track:** `qwen3_6_27b` or `qwen3_5_9b` + Essential Tools + session bootstrap — see [LMStudio_MCP_Tool_Discipline.md](LMStudio_MCP_Tool_Discipline.md).

**Capability grades (internal, fixture/live KPI):**

| Area | Grade |
|------|-------|
| RAG / project understanding | Very good |
| UE code review | Good |
| Single-file compile-fix | Good |
| Build.cs / multi-file fixes | Strong on current measured holdout: 36/36 live Pass@1, 12/12 multifile Pass@1, 0 Build.cs false positives |
| MCP native tool-call | Experimental — verify with `bench_lmstudio_mcp.py` |
| Overall agent automation | Experimental — do not trust unattended edits |

**Compact alternative:** `qwen3_5_9b` — generally more stable than GPT OSS 20B.

**Main local track (wrapper + Pass@1/Pass@K):** `qwen3_6_27b` when VRAM allows. Latest saved 36-case live holdout: `20260709-144441-pass1-target` with **36/36 Pass@1** and **36/36 Pass@K**.

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
- symbol-first and range-first reads before full-file reads
- low-temperature execute/compile-patch turns
- real-project Pass@1/Pass@3 measurement

See [Qwen36_27B_Upgrade_Plan.md](Qwen36_27B_Upgrade_Plan.md) for the current upgrade runbook.

## Resolve Policy

```powershell
python scripts/load_sampling_preset.py --show-profile
python scripts/load_sampling_preset.py --sampling-profile gpt_oss_20b --show-profile
python scripts/load_sampling_preset.py --sampling-profile qwen3_5_9b_deepseek_v4_flash --show-profile
python scripts/load_sampling_preset.py --sampling-profile gpt_oss_20b_claude_opus_sonnet_reasoning_i1 --show-profile
python scripts/load_sampling_preset.py --sampling-profile qwen3_6_27b --show-profile
python scripts/load_sampling_preset.py --sampling-profile qwen3_6_27b --mode compile_fix
```

## Community Fine-Tune Notes

Community GGUF fine-tunes are supported as separate profiles because they often need different decoding behavior from their base family. These profiles are optimization targets, not quality guarantees. Always verify with UBT or Editor-side validation before claiming a fix is complete.
