# Release Readiness Scorecard

Audit-validated baseline was **57/100 No-Go** (portable path safety, write regressions, fixture pollution, namespace drift, red pytest). Stable Gate Stabilization closes those P0/P1 items in-tree.

| Area | Weight | Score | Grade | Notes |
|------|--------|------:|-------|-------|
| Data integrity / write safety | 20% | 86 | B | safe-write CAS, create-only wx, atomic-io PID temps, bundle rewrite |
| MCP availability / accuracy | 20% | 84 | B | Tool gates; source-first plan when project missing |
| Failure recovery / state consistency | 15% | 82 | B | Honest cache invalidation; SQLite close on switch; job revision CAS |
| Install / update / portable | 15% | 85 | B | Assert-SafePackagePath; allowlist pack; content scan CI |
| Real-world UX | 15% | 72 | C | structuredContent cap; project hint precedence; build doc fix |
| Test / release reproducibility | 10% | 88 | A | Portable scan + fixture discovery + namespace tests |
| Security / permission boundaries | 5% | 86 | B | run_command spawn allowlist; taskSessionId sanitization |

**Weighted composite: ~84/100** (stable gate target ≥80 met in-tree; ≥85 after manual install smoke)

## Stable gate status

| Criterion | Status |
|-----------|--------|
| P0 portable path guards + leak scan | Done |
| P0 fixture exclusion + rag namespace SSOT | Done |
| P0 write_file wx + atomic-io + replace/bundle CAS | Done |
| P0 red pytest (source-first without active project) | Done |
| P1 cache/SQLite/job revision/taskId | Done |
| P1 run_command hardening | Done |
| CI installer + portable content scan | Wired |
| 3× full pytest green | Verify in CI/local |
| Manual clean Win11 install | Pending manual smoke |

See [`Release_Baseline_Audit.md`](Release_Baseline_Audit.md) and the Stable Gate Stabilization plan for evidence.
