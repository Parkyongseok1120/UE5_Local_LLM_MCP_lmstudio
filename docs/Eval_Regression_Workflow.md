# Eval Regression Workflow

## Command

```powershell
.\rag.ps1 eval-regression
.\rag.ps1 eval-regression -Live   # includes Tier-B live steps
```

Runs `scripts/run_eval_regression.py`.

## Output

```
Reports/eval/latest.json
Reports/eval/latest.md
Reports/eval/history/<timestamp>.json
Reports/eval/deltas/<timestamp>.json
Reports/eval/failures/<step>/<timestamp>/   # on fail
```

## Regression rules

- Compares to previous `Reports/eval/latest.json` before overwrite
- **Fails** if any step regresses vs baseline pass set
- **Fails** if Pass@K rate drops >10% vs baseline metrics
- Treats Tier/KPI values as internal workflow metrics, not external model rankings
- Requires separate real-project Pass@1/Pass@3 validation before stronger claims

## CI

`.github/workflows/eval-regression.yml` runs Tier-A bundle on push (no UBT). **Unreal Editor is optional** — not part of CI or claim 9.0.

Phase 16: pre-export JSONL ingest works everywhere; Editor Python export only on high-spec machines when needed.

After changes to search/rerank/orchestrator, run locally before merge:

```powershell
.\rag.ps1 eval-regression
.\rag.ps1 report-tier-kpi
```

For unseen real-project validation:

```powershell
.\rag.ps1 summarize-real-project-eval -Question config\real_project_eval_template.json
```

Use a filled copy of `config\real_project_eval_template.json`, not the template itself, for real reporting.

## 9B compact acceptance floor

Documented in [Roadmap_9B_Domain_Expansion.md](Roadmap_9B_Domain_Expansion.md). Minimum bar after domain-expansion work:

```text
Pass@K >= 36/36
Pass@1 >= 35/36  (target 36/36 after component include fix)
wrong-file = 0, Build.cs FP = 0, no-op = 0
write validation p95 <= 2s
```

Focused gate before/after major 9B changes:

| Gate | Command |
|------|---------|
| Fast | `python scripts/run_9b_regression_gate.py` |
| Component x5 live | `python scripts/eval_pass_at_k.py --live --require-live --case-ids local_component_registration_missing_include` (repeat manually or via artifact dir) |
| Full 36 live | `python scripts/eval_pass_at_k.py --live --require-live --config config/rag_eval_real_project_holdout_cases.local.json` |
| KPI report | `python scripts/report_eval_kpi.py --kpi data/baseline/live_holdout/20260711-090534-qwen35-9b/kpi.json` |
