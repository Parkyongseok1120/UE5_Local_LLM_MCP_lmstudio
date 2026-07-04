# Holdout Eval Guide

Real-project holdouts are public-safe, fixture-style cases derived from common Unreal C++ failure patterns. They are designed to test routing, retrieval, taxonomy, and metric aggregation without shipping private source paths or project code.

## Suites

- `core`: small regression suite for always-on CI checks.
- `ceiling`: synthetic cases that isolate specific workflow capabilities.
- `real-project-holdout`: public-safe cases modeled after real project failure shapes.
- `fixture-only`: validation that does not invoke UnrealBuildTool or LM Studio.
- `live-ubt`: compile-fix evaluation that actually runs UnrealBuildTool.

`config/rag_eval_real_project_holdout_cases.json` is a fixture-only foundation for `real-project-holdout-v0`. It validates expected files to read, expected patch targets, forbidden patch targets, module hints, and taxonomy coverage. It does not prove a generated patch compiles.

## Validation

Run the holdout config validator:

```powershell
python scripts/validate_holdout_cases.py --config config/rag_eval_real_project_holdout_cases.json
```

The validator checks required fields, duplicate IDs, public path hygiene, category names, list-shaped target fields, taxonomy routing for cases with `expectedErrorSubkind`, and module resolver coverage for cases with `expectedModules`.

## Reporting

Use suite labels when rendering KPI reports:

```powershell
python scripts/report_eval_kpi.py data/baseline/pass-at-k-kpi.json --suite-name real-project-holdout-v0 --suite-type fixture-only --out-md data/baseline/holdout-fixture-report.md --out-json data/baseline/holdout-fixture-summary.json
```

For live compile-fix runs, use `--suite-type live-ubt` only when UnrealBuildTool actually ran and completed. Fixture-only and metric-only reports validate aggregation and parsing, not patch correctness.

## Claim Bars

- Fixture-only success means the case definitions, taxonomy checks, module resolver checks, and KPI/report paths are healthy.
- Metric-only success means aggregation code can run quickly without UE or LM Studio.
- Live UBT success means the workflow passed compile validation in the tested environment.
- Improvement claims require a comparable baseline, saved KPI JSON, retry state, RAG telemetry, and a completed live UBT run.
- This suite is for local workflow gap tracking. It is not a Sonnet 5 equivalence claim.
