# Eval Metrics For Sonnet 5 Gap Tracking

These metrics compare local Unreal RAG/MCP/UBT workflow behavior against a Sonnet 5-style agent target. They are workflow metrics, not external model benchmarks.

| Metric | Definition | Why it matters |
|---|---|---|
| Pass@1 | Cases that reach green build on the first attempt | Measures first-shot edit judgment |
| Pass@K | Cases that pass within the allowed retry budget | Measures repair-loop usefulness |
| Average attempts to green build | Mean attempt count across evaluated cases | Captures retry efficiency, not just eventual success |
| Same error repeated rate | Fraction of failures where the same error recurs after an edit | Detects poor diagnosis or repeated wrong patches |
| No-op edit rate | Fraction of attempts where no changed path is recorded | Detects parser/tool drift or identical patch loops |
| Validation rejected count | Attempts blocked before or during wrapper validation | Detects prompt/guardrail conflicts before UBT runs |
| Pre-apply no-op count | Validation-rejected attempts with no effective applied edit | Detects blocked loops that would otherwise look invisible in UBT-only metrics |
| Wrong file edit rate | Fraction of attempts that edit files outside allowed targets | Measures tool-policy and routing discipline |
| Build.cs false positive rate | Module dependency patches made without module evidence | Detects overfitting to dependency fixes |
| Rollback count | Number of attempts that must undo or supersede a bad edit | Captures long-running edit safety |
| Time to green build | Wall-clock time from first attempt to passing build | Measures practical productivity |

For local Qwen workflows, these metrics matter more than broad chat quality claims. Sonnet 5-style agent behavior is mostly visible in long-context project memory, tool use, retry judgment, and safe convergence under build feedback.

When wrapper runs write `retry_state.json`, `eval_pass_at_k.py` can fold repeated-error, no-op, and validation-rejection data into KPI JSON as `sameErrorRepeatedCount`, `noOpEditCount`, `validationRejectedCount`, `preApplyNoOpCount`, and related case-id lists. Missing retry-state data is treated as absent, not as a failure.

## Metric-Only Smoke

Full compile-fix eval can require UnrealBuildTool, even in dry-run mode, because golden patches still need a real build to prove they compile.

Use metric-only smoke mode to validate KPI aggregation and retry-state parsing without Unreal Engine or LM Studio:

```powershell
python scripts/eval_pass_at_k.py --metrics-only --config config/rag_eval_pass_at_k_cases.json
```

`--metrics-only` does not invoke UBT, does not call LM Studio, and is not a substitute for live or dry-run compile-fix evaluation.

## Telemetry Reports

`rag_telemetry.jsonl` and `retry_state.json` are evidence for how the workflow behaved: which sidecars were used, whether errors repeated, and how many attempts were needed. They are not proof of model improvement by themselves.

Use `scripts/report_eval_kpi.py` to render an observed telemetry report and, when possible, compare against a previous KPI baseline. Add `--suite-name` and `--suite-type` so fixture-only, real-project-holdout, and live-ubt evidence are not mixed.

Example fixture-only holdout report:

```powershell
python scripts/report_eval_kpi.py data/baseline/pass-at-k-kpi.json --suite-name real-project-holdout-v0 --suite-type fixture-only --out-md data/baseline/holdout-fixture-report.md --out-json data/baseline/holdout-fixture-summary.json
```

Live eval plus baseline comparison is required before making compile-fix improvement claims. Fixture-only holdout validation is useful coverage, but it is not a substitute for live UBT compile-fix evaluation.

## Phase 2C Soft Steering Metrics

After the first UE 5.8 local live holdout baseline, the immediate failures were declaration/definition style failures rather than Build.cs module dependency failures. Phase 2C therefore tracks whether warning-only steering improves:

- header/cpp signature mismatch handling
- LNK2019 missing cpp definition handling
- Build.cs-first edits when module evidence is absent
- required-read behavior for header declarations and matching cpp definitions

These are soft steering observations only. Do not treat a single post-change run as proof of model improvement.
