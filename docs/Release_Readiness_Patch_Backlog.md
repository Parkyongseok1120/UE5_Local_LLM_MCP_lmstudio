# Release Readiness Patch Backlog (Stable Gate)

> **Historical / superseded snapshot.** This backlog describes an earlier Stable
> Gate milestone. Status rows and deferred items below are retained as historical
> facts and must not be read as the current 1.3.3 backlog. Use
> [Release Notes 1.3.3](Release_Notes_1_3_3.md),
> [Integrated Installer](Integrated_Installer.md), and the current CI workflow for
> the supported Direct-mode release contract.

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
