# Qwen 3.6 27B Upgrade Plan

## Verdict

Yes. Qwen 3.6 27B can be upgraded along the same operating axis as the Qwen 3.5 9B track, but it should not be treated as a larger copy of the 9B profile.

The 9B improvements mainly reduce context noise and tool-call drift. The 27B profile benefits from those same guardrails, plus broader retrieval, a critique/verification turn, and live Pass@K measurement.

## Current External Signals

- Qwen lists Qwen3.6-27B as a 27B dense post-trained model available for local runtimes such as Transformers, vLLM, SGLang, KTransformers, and GGUF-derived workflows.
- LM Studio lists `qwen3.6-27b` as a dense 27B model with tool-use training, reasoning support, vision input, and a 16 GB minimum system-memory signal.
- LM Studio's model card highlights agentic coding and thinking preservation as core upgrades.
- LM Studio Community provides a GGUF package with a `Q4_K_M` path usable from LM Studio, llama.cpp, and other local runners.

Sources:

- https://huggingface.co/Qwen/Qwen3.6-27B
- https://lmstudio.ai/models/qwen/qwen3.6-27b
- https://huggingface.co/lmstudio-community/Qwen3.6-27B-GGUF

## Improvement Factors

| Factor | 9B Tactic | 27B Upgrade |
|--------|-----------|-------------|
| Context | Compact 24K/32K and narrow reads | Keep 32K default, A/B test 64K only when hardware allows |
| Retrieval | Smaller top_k, no deep search | Broader top_k and deep search, then trim before patch |
| Tool discipline | One tool per turn | Same one-tool rule, with better evidence gathering |
| Patch safety | Small patches, no full rewrites | Max 2 files, 60 changed lines, no-op guard |
| Compile fix | Classify first error | Same classification, plus one-root-cause-per-loop |
| File access | Symbol/range reads first | Symbol/range reads first, +/-40 lines for 27B |
| Reasoning | Thinking off for stability | Thinking on for plan/analyze, off for execute/patch |
| KPI | Compact eval and tool stability | Pass@1/Pass@3 live compile-fix and MCP bench |

## Executed Changes

1. Expanded Qwen 3.6 27B aliases for common LM Studio, GGUF, Unsloth, and MTP model identifiers.
2. Added a conservative 32K default with a documented 64K A/B-test variant.
3. Added 27B agent-policy flags for symbol-first reads, range reads, patch changed-line limits, and no-op guards.
4. Lowered execute and compile-patch sampling slightly to reduce over-editing.
5. Updated the Qwen 3.6 27B system prompt with 9B-proven safety rules.

## Runbook

```powershell
$env:UNREAL_RAG_MODEL_PROFILE = "qwen3_6_27b"
python scripts/load_sampling_preset.py --show-profile
python scripts/load_sampling_preset.py --sampling-profile qwen3_6_27b --mode compile_fix
```

In LM Studio:

- Load `qwen/qwen3.6-27b` or `lmstudio-community/Qwen3.6-27B-GGUF`.
- Prefer `Q4_K_M` first; test `Q5_K_M` only if memory and speed are acceptable.
- Keep context at 32768 for normal work. Test 65536 only for repository-review workloads.
- Enable MCP Essential Tools.
- Use `prompts/lmstudio_qwen36_27b_compact_system.md` plus `prompts/lmstudio_compact_mcp_base.md`.
- Keep reasoning on for plan/analyze turns and off for execute/patch turns.

## Validation Gates

```powershell
python -m pytest tests/test_sampling_profiles.py
python scripts/bench_lmstudio_mcp.py
python scripts/eval_pass_at_k.py --live --require-live
```

Pass criteria:

- Profile resolves from loaded LM Studio model names.
- `module_fix` uses the low-temperature compile-patch preset.
- MCP tool-call bench passes without malformed calls.
- Pass@K compile-fix improves or holds steady against the previous 27B baseline.

## Risk Notes

- Do not claim Qwen 3.6 27B is globally Sonnet-grade from these project-local results.
- MTP builds can vary by LM Studio/runtime version; keep non-MTP GGUF as the stability baseline.
- Raising context beyond 32K should be measured, not assumed. Larger context can increase retrieval noise and latency.
