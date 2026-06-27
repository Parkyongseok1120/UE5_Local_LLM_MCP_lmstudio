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

## CI

`.github/workflows/eval-regression.yml` runs Tier-A bundle on push (no UBT). **Unreal Editor is optional** — not part of CI or claim 9.0.

Phase 16: pre-export JSONL ingest works everywhere; Editor Python export only on high-spec machines when needed.

After changes to search/rerank/orchestrator, run locally before merge:

```powershell
.\rag.ps1 eval-regression
.\rag.ps1 report-tier-kpi
```
