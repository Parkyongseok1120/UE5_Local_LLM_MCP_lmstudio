# Evaluation Claim Guardrail

Current Tier/KPI numbers are internal UE RAG/MCP/UBT scorecard results, not external standardized model benchmarks. Do not claim that Qwen, GPT OSS, or any other local model is generally Sonnet-grade.

Safer wording: inside this Unreal-specific validation loop, Qwen 3.6 27B currently behaves like a strong local compile-fix agent with practical results in the lower-to-mid Sonnet 4 workflow band for narrow UE C++ compile-fix tasks. Multifile Pass@1 reached **12/12** after v1.2.5; overall live Pass@1 is **30/36** — still below Sonnet 4.5 expectations on module-edge and editor-runtime boundary cases.

The forward target remains a Sonnet 4.5-oriented local Unreal workflow. This is a target, not a current model-grade claim. See [docs/Sonnet45_Target_Plan.md](Sonnet45_Target_Plan.md).

Sonnet 5 is tracked only as a gap-analysis target for future workflow improvements around long-context agentic coding, tool use, retry judgment, and project memory. This does not claim Sonnet 5 equivalence; see [docs/Sonnet5_Gap_Plan.md](Sonnet5_Gap_Plan.md).

## Eval tier separation (v1.2.5)

Three KPI channels must not be conflated:

| Tier | Command | What it measures |
|------|---------|------------------|
| A — dry-run | `eval_pass_at_k.py --dry-run` | Golden oracle applied + UBT; no LLM, no autofix pipeline |
| A.5 — autofix-only | `eval_pass_at_k.py --autofix-only` | Broken fixture + static autofix pipeline + UBT; no LLM |
| B — live | `eval_pass_at_k.py --live` | Full wrapper + LM Studio + retry/guards |

Dry-run **36/36** does not imply live **36/36** or autofix-only pass rates.

Compact-model optimization tracks include Qwen 3.5 9B, Qwen3.5-9B-DeepSeek-V4-Flash-GGUF, GPT OSS 20B, and gpt-oss-20b-claude-opus-sonnet-reasoning-i1-GGUF community fine-tunes. See [docs/Model_Profiles.md](Model_Profiles.md).

## Observed Local Model Ranking

This ranking is **not a global model benchmark**. It is the observed behavior inside this repository's Unreal-specific loop:

```text
LM Studio
+ MCP Essential Tools
+ strict project-filtered RAG
+ Unreal symbol / range lookup
+ static validation
+ UBT compile wrapper
```

Within that loop, the current practical ranking is:

| Model / workflow proxy | Evidence level | Measured or estimated behavior in this UE loop | Claude / GPT workflow proxy (not a global benchmark) |
|---|---|---|---|
| `qwen3_6_27b` / `qwen3.6-27b-heretic-uncensored-finetune-neo-code-di-imatrix-max` | **Measured** — 36-case UE 5.8 live holdout `20260709-042338` (v1.2.5) | Pass@K **34/36** (94.4%), Pass@1 **30/36** (83.3%). Multifile tier **12/12 Pass@1**. Dry-run **36/36** (`20260709-035933`). Failures: `navigation_system` module no-op, `editor_runtime_guard_boundary` Build.cs misuse. | **Lower–mid Sonnet 4** for narrow compile-fix + multifile autofix loops; **below Sonnet 4** on module-edge no-ops and editor-runtime guards. |
| Claude Sonnet 4 (cloud, estimated) | Not head-to-head measured on this 36-case suite | Estimated **32–35/36 Pass@1** with the same RAG/MCP/UBT wrapper if tool discipline holds; likely fewer no-op/module-fix stalls. | **Mid Sonnet 4** workflow reference for this narrow UE automation loop. |
| Claude Sonnet 4.5 (cloud, estimated) | Gap-analysis target only | Estimated **34–36/36 Pass@1** on this suite with better retry judgment and fewer Build.cs false positives. | **Upper Sonnet 4 / entry Sonnet 5** workflow target for this repo. |
| GPT-5 / GPT-5.x class (cloud, estimated) | Not measured | Estimated similar band to Sonnet 4.5 for agentic tool loops; may excel on long-context refactor but unproven here. | **Sonnet 4.5-class** agentic coding proxy; treat as forward target, not current local equivalence. |
| `qwen3_5_9b_deepseek_v4_flash` | Profiled/observed, not rerun on latest 36-case live suite | Best compact track when VRAM is limited. Usually follows JSON/tool/patch discipline better than base GPT OSS 20B. | **Upper Sonnet 3.7** for narrow compile-fix loops; below Sonnet 4 for refactor. Needs fresh 36-case live proof. |
| `qwen3_5_9b` | Profiled/observed, not rerun on latest 36-case live suite | Stable compact baseline for Essential Tools, small patch loops, and focused compile-fix tasks. | **Mid–upper Sonnet 3.7** for narrow UE compile-fix; below Sonnet 4 on multi-file refactor. |
| `qwen3_8b` | Profiled only | Smaller compact fallback. Useful for RAG Q&A and small fixes with strict prompts. | **Sonnet 3.5 – lower Sonnet 3.7** for narrow tasks. |
| `gpt_oss_20b_claude_opus_sonnet_reasoning_i1` | Profiled/experimental | Community reasoning fine-tune profile. Better theoretical reasoning budget than base GPT OSS 20B, but still needs stable MCP/JSON verification. | **Upper Sonnet 3.7** estimate if tool discipline holds; not proven on 36-case live. |
| `gpt_oss_20b` | Profiled/observed variable stability | Useful, but more variable in MCP/JSON/tool-call loops; prefer Qwen 9B or Qwen 27B when available. | **Around Sonnet 3.5** for this workflow. |
| `gpt_oss_small` | Profiled only | Lightweight fallback for simple inspect/patch tasks. | **Below Sonnet 3.5** for this workflow. |
| `gpt_oss_120b`, `qwen_coder_large`, `generic_large` | Configured, not currently proven in this local 36-case report | Potentially stronger if local hardware can run them well, but this repo has no current 36-case live KPI for them. | Unknown until measured; do not infer quality from parameter count alone. |

**Proxy scale key (workflow-only, not MMLU/SWE-bench):**

| Proxy band | Typical behavior in this UE RAG/MCP/UBT loop |
|---|---|
| Sonnet 3.5 | Single-file fixes with heavy prompting; weak multifile / MCP discipline |
| Sonnet 3.7 | Reliable compile-fix on simple cases; struggles on module-edge + editor guards |
| Sonnet 4 (lower–mid) | **Qwen 3.6 27B measured band** — strong autofix + multifile; occasional no-op / wrong Build.cs |
| Sonnet 4 (mid) | Estimated cloud Sonnet 4 with same tooling |
| Sonnet 4.5 | Repo forward target — fewer validation dead-ends, better first-shot module fixes |
| GPT-5.x class | Estimated Sonnet 4.5+ agentic coding; not measured here |

In short: **Qwen 3.6 27B is currently the only profile with a saved 36-case UE 5.8 live holdout result in this README.** Qwen 3.5 9B-family models remain valuable because this agent stack rewards tool-call, patch, symbol-lookup, and validation discipline. This does **not** mean a smaller model is generally smarter than a larger model; it means it may fit this Unreal RAG/MCP/UBT automation loop better.

Old name: **Unreal58-RAG**. Officially tested on **UE 5.8**. Other 5.x versions can work, but build your own index from **your** licensed UE install (BYOI).

> **BYOI** = Bring Your Own Index. This repo ships **tooling only**: not Epic source, not a pre-built `rag.sqlite`.

The first goal is to make local models hallucinate less on Unreal C++, especially `Build.cs`, include, UHT, project-specific code, and asset metadata.

```text
Unreal knowledge / API evidence = RAG
Answer tone / format / habits     = LoRA (optional, later)
Workflow (search / files / build) = MCP
```
