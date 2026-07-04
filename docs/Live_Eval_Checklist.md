# Live Eval Checklist

Use this checklist before making claims from local compile-fix evals.

1. Ensure UnrealBuildTool is available and points to the intended engine install.
2. Ensure the active project is correct in `%USERPROFILE%\.lmstudio\config\unreal-workspace.json`.
3. If testing symbol sidecars, build the symbol graph first:

```powershell
python scripts/build_symbol_graph.py
```

4. Run metric-only smoke first:

```powershell
python scripts/eval_pass_at_k.py --metrics-only --config config/rag_eval_pass_at_k_cases.json
```

5. Validate the public-safe real-project holdout fixtures:

```powershell
python scripts/validate_holdout_cases.py --config config/rag_eval_real_project_holdout_cases.json
```

6. Run dry-run eval only when UBT is available.
7. Run live eval only when LM Studio Local Server and UBT are ready.
8. Save KPI JSON, `retry_state.json`, and `rag_telemetry.jsonl`.
9. Generate an observed telemetry report with suite labels:

```powershell
python scripts/report_eval_kpi.py data/baseline/pass-at-k-kpi.json --run-dir data/wrapper_runs/<run> --suite-name real-project-holdout-v0 --suite-type live-ubt
```

10. Compare against a previous baseline before discussing deltas.
11. Do not claim improvement from one small run.

Recommended minimum before public claims:

- core eval
- ceiling eval
- at least 10 real-project holdout cases

These are workflow observations, not model-grade equivalence claims.

## Live Holdout Baseline Run

Use this flow for a conservative UE 5.8 local holdout baseline. Do not compare fixture-only results as if they were live UBT results, and do not claim improvement from a single run. This is not a Project_MJS or UE 5.7.x baseline.

`config/rag_eval_real_project_holdout_cases.json` is public-safe and fixture-style. It is useful for validation, taxonomy coverage, module resolver coverage, and metrics-only smoke, but it is not enough for true live UBT compile-fix claims. For an actual local live baseline, copy `config/rag_eval_real_project_holdout_cases.local.example.json` to the ignored `config/rag_eval_real_project_holdout_cases.local.json` and fill in real private `fixtureDir` and `projectFile` values. Do not commit local project paths.

For the first local run, use Unreal Engine 5.8 and the UE 5.8 UnrealBuildTool path. Do not search for UE 5.7 or warn about UE 5.7 mismatch for this baseline. Each fixture directory should contain a small UE 5.8 C++ test project copy with one intentional failure, not the original working project.

1. Validate holdout cases:

```powershell
python scripts/validate_holdout_cases.py --config config/rag_eval_real_project_holdout_cases.json
```

2. Build symbol graph if testing symbol sidecars:

```powershell
python scripts/build_symbol_graph.py
```

3. Run metrics-only holdout smoke:

```powershell
python scripts/eval_pass_at_k.py --metrics-only --config config/rag_eval_real_project_holdout_cases.json
```

4. Run live holdout eval when LM Studio and UnrealBuildTool are ready:

```powershell
python scripts/eval_pass_at_k.py --live --require-live --config config/rag_eval_real_project_holdout_cases.json --model <lmstudio_model_name> --ubt-path <UnrealBuildTool.exe> --wrapper-timeout 1800
```

The current public-safe holdout config is fixture-style. Cases without `fixtureDir` and `projectFile` are reported as not live-applicable instead of being counted as successful live fixes. Add private/local live fixtures outside public config if you need a true UBT baseline.

5. Store artifacts together:

```text
data/baseline/live_holdout/
  YYYYMMDD-HHMMSS/
    kpi.json
    report.md
    summary.json
    rag_telemetry.jsonl
    retry_state.json
    notes.md
```

Large live logs should stay out of git by default. Keep raw KPI JSON, `retry_state.json`, `rag_telemetry.jsonl`, Markdown report, and notes together for auditability.

6. Generate a live-labeled report:

```powershell
python scripts/report_eval_kpi.py <kpi_json_path> --suite-name real-project-holdout-v0 --suite-type live-ubt --run-dir <wrapper_run_dir> --out-md <report.md> --out-json <summary.json>
```

Use `docs/templates/live_holdout_notes_template.md` for the run notes. The claim status should remain: internal baseline only, not a public performance claim.

## First Live Baseline

Start with 5 local cases before expanding the suite:

- GameplayTags Build.cs missing dependency
- EnhancedInput Build.cs missing dependency
- `generated.h` not last
- header/cpp signature mismatch
- LNK2019 missing cpp definition

Command flow:

```powershell
python scripts/validate_holdout_cases.py --config config/rag_eval_real_project_holdout_cases.json
python scripts/validate_holdout_cases.py --config config/rag_eval_real_project_holdout_cases.local.json --allow-local-paths
python scripts/build_symbol_graph.py
python scripts/eval_pass_at_k.py --metrics-only --config config/rag_eval_real_project_holdout_cases.local.json
python scripts/eval_pass_at_k.py --live --require-live --config config/rag_eval_real_project_holdout_cases.local.json --model <lmstudio_model_name> --ubt-path <UnrealBuildTool.exe> --wrapper-timeout 1800
python scripts/report_eval_kpi.py <kpi_json_path> --suite-name real-project-holdout-local-v0 --suite-type live-ubt --run-dir <wrapper_run_dir> --out-md <report.md> --out-json <summary.json>
```

Do not compare fixture-only metrics to live UBT metrics as if they are the same. Save `kpi.json`, `report.md`, `summary.json`, `rag_telemetry.jsonl`, `retry_state.json`, and `notes.md` together under `data/baseline/live_holdout/<timestamp>/`.

### Create Local Holdout Config

Generate the ignored local config from the public-safe template:

```powershell
python scripts/bootstrap_local_holdout.py --project-file "<PATH_TO_PROJECT>.uproject" --fixture-root "<PATH_TO_FIXTURE_ROOT>"
```

Recommended local fixture root:

```text
data/local_holdout_fixtures/
  local_gameplaytags_missing_module/
  local_enhanced_input_missing_module/
  local_generated_h_not_last/
  local_header_cpp_signature_mismatch/
  local_lnk2019_missing_cpp_definition/
```

Each directory should contain a fixture-local `.uproject` with `EngineAssociation` set to `5.8`, a `Source/` tree, and `request.txt`. The eval runner copies `fixtureDir` to a temp directory and runs edits/builds against that temp copy.

Then run:

```powershell
python scripts/validate_holdout_cases.py --config config/rag_eval_real_project_holdout_cases.local.json --allow-local-paths
python scripts/build_symbol_graph.py
python scripts/eval_pass_at_k.py --metrics-only --config config/rag_eval_real_project_holdout_cases.local.json
python scripts/eval_pass_at_k.py --live --require-live --config config/rag_eval_real_project_holdout_cases.local.json --model qwen3.6-27b-heretic-uncensored-finetune-neo-code-di-imatrix-max --ubt-path "C:\Program Files\Epic Games\UE_5.8\Engine\Binaries\DotNET\UnrealBuildTool\UnrealBuildTool.exe" --wrapper-timeout 1800
```
