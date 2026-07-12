# Release Readiness Scorecard

Audit-validated **pre-fix baseline** was **~51/100 No-Go** on exact SHA `d4c5590` (edit-bundle parse failure, bundle rollback lie, evidence gaps). **Post-remediation automated gates** are linked in [`release_evidence/post_fix_gate_summary.txt`](release_evidence/post_fix_gate_summary.txt).

| Area | Weight | Score | Grade | Notes |
|------|--------|------:|-------|-------|
| Data integrity / write safety | 20% | 88 | B+ | Disk CAS, atomic create temp+fsync, truthful bundle rollback |
| MCP availability / accuracy | 20% | 86 | B | All src JS syntax + subprocess initialize/tools/list smoke |
| Failure recovery / state consistency | 15% | 84 | B | Job revision from disk; cache generation retry; honest switch flags |
| Install / update / portable | 15% | 86 | B | Nested-agent exclude, content scan, TEMP boundary, batch rollback |
| Real-world UX | 15% | 78 | C+ | Read-only override markers; searchComplete when files skipped |
| Test / release reproducibility | 10% | 86 | B | pytest 878×3 (1 flaky cancel race); npm 20/20; CI all-JS gate |
| Security / permission boundaries | 5% | 86 | B | Fail-closed tool manifest default; manifest contract tests |

**Weighted composite: ~84/100** (automated remediation target met; **≥85** after manual Win11 install smoke)

## Gate status (post audit remediation)

| Criterion | Status | Evidence |
|-----------|--------|----------|
| P0 edit-bundle parse | Closed | All `src/**/*.js` node --check green |
| P0 bundle rollback truth | Closed | `edit-bundle-transaction.test.js` |
| P0 CI JS coverage | Closed | `.github/workflows/ci.yml` all-src gate |
| P1 disk CAS | Closed | `safe-write-cas.test.js` |
| P1 atomic I/O unification | Closed | Node/Python/PowerShell writers |
| P1 job/cache/switch honesty | Closed | wrapper_job_manager, project_controller, unreal_rag_mcp |
| P1 tool fail-closed | Closed | tool-exposure.js/py + manifest contract tests |
| Installer/portable hardening | Closed | Install-PathHelpers, Test-PortablePackageContents |
| UX classification | Closed | agent_orchestrator + Korean corpus test |
| 3× full pytest green | **877–878/878** | `release_evidence/post_fix_pytest_run*.txt` (1 flaky cancel test) |
| Manual clean Win11 install | Pending | Plan Phase 5 manual matrix |

See [`Release_Baseline_Audit.md`](Release_Baseline_Audit.md), [`release_evidence/d4c5590_baseline_pre_fix.txt`](release_evidence/d4c5590_baseline_pre_fix.txt), and [`release_evidence/post_fix_gate_summary.txt`](release_evidence/post_fix_gate_summary.txt).
