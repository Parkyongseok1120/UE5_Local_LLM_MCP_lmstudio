# Eval harness

Run all local regression evals:

```powershell
.\rag.ps1 eval-harness
```

Output: `Reports/<timestamp>/summary.json` and per-step JSON.

Includes retrieval, reasoning, e2e compile fixture, Pass@K dry-run, Build.cs parser tests, routing and taxonomy tests.

## Tier A (no LM Studio)

```powershell
.\rag.ps1 sonnet-tier-gate
.\rag.ps1 eval-pass-at-k          # golden overlay + UBT
.\rag.ps1 report-tier-kpi
```

## Tier B (LM Studio required)

1. Load model in LM Studio and start **Local Server** (`http://localhost:1234`).
2. Preflight:

```powershell
.\rag.ps1 preflight-lmstudio
```

3. Live gate + Pass@K:

```powershell
.\rag.ps1 sonnet-tier-gate -Live
.\rag.ps1 eval-pass-at-k -Live
.\rag.ps1 report-tier-kpi
```

KPI scorecard: `data/baseline/tier-kpi-latest.json`

## Interpretation guardrail

The KPI output keeps `compile_fix`, `architecture`, `semantic_refactor`,
`runtime_debug`, `negative_control`, and `orchestration_ux` separate. Keep
Pass@1 separate from Pass@K. Do not publish a combined AI grade or a
cross-model equivalence claim from these internal suites.

For a live claim, each dedicated scorecard must contain a passing non-empty
measurement plus `generatedAt`, `commit`/`commitSha`, `model`/`modelId`, and
`suite`/`config`. Labels that merely contain the word `live` are not accepted
as live proof.

See [Evaluation_Risk_Register.md](Evaluation_Risk_Register.md) and [Real_Project_Validation_Plan.md](Real_Project_Validation_Plan.md).

## Regression gate (Phases 14-23)

```powershell
.\rag.ps1 eval-regression
.\rag.ps1 eval-regression -Live
```

Output: `Reports/eval/latest.json` with history and delta comparison. See [Eval_Regression_Workflow.md](Eval_Regression_Workflow.md).

Optional UBT smoke on soulslike fixture:

```powershell
.\rag.ps1 eval-e2e-compile -RunUbt
```
