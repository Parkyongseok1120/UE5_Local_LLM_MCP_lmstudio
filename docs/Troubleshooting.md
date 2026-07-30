# Troubleshooting

## Capability scorecard is not release-ready

```powershell
.\rag.ps1 report-tier-kpi
```

The report keeps `compile_fix`, `architecture`, `semantic_refactor`,
`runtime_debug`, `negative_control`, and `orchestration_ux` separate. A
`metrics-only`, skipped, or static-only run remains `not_run` or
`static_verified`; it cannot satisfy a live release claim. Run the missing live
suite shown in `data/baseline/tier-kpi-latest.json`.

If a row says `live_verified` but `Claim allowed` is `NO`, inspect its
`identityIssues` and `evidenceIssues`. Live reports need an auditable timestamp,
commit, model, suite/config, a positive executed-case count, and the required
numeric measurements.

If `bench-mcp` FTS flakes, rerun `.\rag.ps1 bench-mcp` alone (min-of-2 timing + one retry).

## Unreal Editor (optional)

**Tier A/B gates do not require Unreal Editor.**

| Environment | Phase 16 Editor export |
|-------------|------------------------|
| VRAM-limited desktop | Skip; ingest pre-exported JSONL via `collect-editor-metadata` |
| High-spec workstation | Run `tools/ue_export/*` in Editor Python, then ingest |

UBT command-line builds and LM Studio live eval are always allowed in the gate.

## Python not found

Install Python 3.10+ or use the Codex bundled runtime path checked by `rag.ps1` (`Find-Python`).

## Generic API queries return only project chunks

Set `UNREAL_RAG_PROJECT_ROUTING=v1` (default) and use MCP `scope=engine` or `mixed`. Legacy: `UNREAL_RAG_LEGACY_PROJECT_FILTER=0`.

## Build.cs index drift after parser fix

```powershell
.\rag.ps1 collect-symbols --tier public
.\rag.ps1 collect-module-graph
.\rag.ps1 build-incremental
```

## Wrapper rewrites entire large files

Use sampling profile with `preferPatchOverFullFile: true` or ensure model returns `patches[]` for files over ~200 lines.

## Eval harness failures

```powershell
.\rag.ps1 eval-harness
```

Reports land in `Reports/<timestamp>/summary.json`.

## Release / install verification

```powershell
.\rag.ps1 verify-release
.\rag.ps1 doctor
```

Output: `data/baseline/verify-release-latest.json`

## Regression gate

```powershell
.\rag.ps1 eval-regression
```

Compare deltas in `Reports/eval/deltas/`. See [Eval_Regression_Workflow.md](Eval_Regression_Workflow.md).

## Agent wrote files unexpectedly

Rerun `python install.py --profile standard --yes` to restore SAFE read-only defaults.
