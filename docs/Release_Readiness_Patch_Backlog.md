# Release Readiness Patch Backlog (Stable Gate)

No new features. Remaining work after Stable Gate Stabilization.

## P0 — closed

| Item | Status |
|------|--------|
| Portable path guards + allowlist pack + leak scan | Done |
| Fixture exclusion from project discovery | Done |
| rag.ps1 namespace SSOT (`Get-RagDataPaths`) | Done |
| write_file create-only + atomic-io PID temps + safe-write CAS | Done |
| Red pytest (source-first without active project) | Done |

## P1 — before stable tag

| Item | Owner action |
|------|--------------|
| Manual clean Win11 install smoke | Run root `INSTALL.bat` → select SAFE → Verify → first MCP health |
| GitHub Actions green on release commit | Push and confirm all jobs including portable scan |

## P2 — polish

| Item | File area |
|------|-----------|
| Cline idempotent reinstall automated test | Requires mock Cline settings path in CI |
| `agent-mcp.json` template without user paths | `lmstudio-unreal-agent-mcp/config/` |
| Portable manifest version bump on ZIP layout change | `installer/manifest.json` |

## P3 — deferred

| Item | Reason |
|------|--------|
| Cross-process write locks | v-next |
| AGENT_STATE_ROOT unification | v-next |
