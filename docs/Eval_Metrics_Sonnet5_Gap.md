# Eval Metrics For Sonnet 5 Gap Tracking

These metrics compare local Unreal RAG/MCP/UBT workflow behavior against a Sonnet 5-style agent target. They are workflow metrics, not external model benchmarks.

| Metric | Definition | Why it matters |
|---|---|---|
| Pass@1 | Cases that reach green build on the first attempt | Measures first-shot edit judgment |
| Pass@K | Cases that pass within the allowed retry budget | Measures repair-loop usefulness |
| Average attempts to green build | Mean attempt count across evaluated cases | Captures retry efficiency, not just eventual success |
| Same error repeated rate | Fraction of failures where the same error recurs after an edit | Detects poor diagnosis or repeated wrong patches |
| No-op edit rate | Fraction of attempts where no changed path is recorded | Detects parser/tool drift or identical patch loops |
| Wrong file edit rate | Fraction of attempts that edit files outside allowed targets | Measures tool-policy and routing discipline |
| Build.cs false positive rate | Module dependency patches made without module evidence | Detects overfitting to dependency fixes |
| Rollback count | Number of attempts that must undo or supersede a bad edit | Captures long-running edit safety |
| Time to green build | Wall-clock time from first attempt to passing build | Measures practical productivity |

For local Qwen workflows, these metrics matter more than broad chat quality claims. Sonnet 5-style agent behavior is mostly visible in long-context project memory, tool use, retry judgment, and safe convergence under build feedback.

When wrapper runs write `retry_state.json`, `eval_pass_at_k.py` can fold repeated-error and no-op data into KPI JSON as `sameErrorRepeatedCount`, `noOpEditCount`, `repeatedErrorCaseIds`, and `noOpCaseIds`. Missing retry-state data is treated as absent, not as a failure.

## Metric-Only Smoke

Full compile-fix eval can require UnrealBuildTool, even in dry-run mode, because golden patches still need a real build to prove they compile.

Use metric-only smoke mode to validate KPI aggregation and retry-state parsing without Unreal Engine or LM Studio:

```powershell
python scripts/eval_pass_at_k.py --metrics-only --config config/rag_eval_pass_at_k_cases.json
```

`--metrics-only` does not invoke UBT, does not call LM Studio, and is not a substitute for live or dry-run compile-fix evaluation.
