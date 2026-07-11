# Release Baseline Audit

Generated as Phase 0 of the Release Readiness Plan.

## Git snapshot

| Field | Value |
|-------|-------|
| Commit | `069f2d2d2593be910f3b7ded0879b40e6d37710a` |
| Branch | Develop (uncommitted hardening delta on top) |
| Modified files | 15 tracked |
| New files | 10 untracked (tool_exposure, atomic_io, hardening tests) |

## Gate log (local, post–release-readiness patches)

| Gate | Result | Notes |
|------|--------|-------|
| Hardening pytest subset | **25 passed** | Includes installer gates, build plan fail, cache gen |
| `npm test` | **11 passed** | Includes atomic-io.test.js |
| `Verify-UnrealMcp.ps1 -RepoOnly` | **PASS** | BYOI index may WARN on clean clone |
| Full `pytest -q` | 844+ passed | 2 unrelated domain/orchestrator failures may remain |

## Change-impact map

| Area | Files |
|------|-------|
| Install / packaging | `installer/*`, portable build scripts |
| Shared config | `scripts/workspace_paths.py`, `Install-PathHelpers.ps1` |
| Cache generation | `scripts/project_switch_invalidate.py`, `scripts/unreal_rag_mcp.py` |
| Write paths | `lmstudio-unreal-agent-mcp/src/server.js`, `atomic-io.js`, `validation-dirty.js` |
| Tool authorization | `scripts/tool_exposure.py`, `tool-exposure.js` |
| CI / docs | `.github/workflows/ci.yml`, `docs/Stabilization_E2E_Matrix.md` |

## Scorecards

### Baseline A — README product 1.2.5 (tagged release narrative)

Pre-hardening release label; holdout evidence 36/36 Pass@K documented in README.

### Baseline B — Current working tree (069f2d2 + hardening delta)

| Area | Preliminary score |
|------|------------------|
| File write safety | 82 |
| MCP runtime | 76 |
| Project switch / cache | 72 |
| Install / packaging | 43 |
| Test / release reproducibility | 67 |
| **Composite (audit weights)** | **~69/100** |

Release Readiness Plan targets **85+** after Phases 1–6. See [`Release_Readiness_Scorecard.md`](Release_Readiness_Scorecard.md) (~82/100 after this tranche).
