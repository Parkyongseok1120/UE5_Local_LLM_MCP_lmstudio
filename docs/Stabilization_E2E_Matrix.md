# Stabilization E2E Matrix (pre-main merge)

Run after Phase 1 gates pass locally and on the stabilization branch CI.

## Automated gates

```powershell
ruff check scripts/ tests/
pytest -q
python scripts/verify_encoding.py
npm test --prefix lmstudio-unreal-agent-mcp
node --check lmstudio-unreal-agent-mcp/src/server.js
installer\Verify-UnrealMcp.ps1
installer\Install-ClineUnrealMcp.ps1 -WhatIf
pytest tests/test_tool_manifest_contract.py tests/test_mcp_stable_subprocess_e2e.py -q
```

## Install matrix (manual)

| Scenario | Steps | Pass |
|----------|-------|------|
| Clean Win11 | `Install-UnrealMcp.ps1` then `Install-ClineUnrealMcp.ps1 -All` | Verify script green; Cline shows both servers |
| Existing Cline (3+ MCP servers) | Install Cline script on machine with other MCP entries | Non-Unreal servers preserved |
| Cline reinstall idempotency | Run Cline installer twice | Second run merges; backup `*.bak-*` created once |
| Safe → agent toggle | Re-run with `-EnableAgentMode` | Write/build env flags flip; no settings loss |
| `-WhatIf` preview | `Install-ClineUnrealMcp.ps1 -WhatIf` | Lists keys that would change; no file write |

## Workflow E2E (manual)

1. RAG health → set active project → agent read → replace trivial comment → `static_validate_project`
2. Build in Rider (preferred) or agent build when enabled
3. Restart both MCP servers → confirm shared active project and validation dirty persistence (if validate-on-write timed out)

## Mutation / fault injection (manual)

| Case | Expected |
|------|----------|
| MCP kill mid-write | No partial corruption; rollback or lock release |
| Cancel background compile/RAG job | Job status `cancelled`; no orphan process |
| Invalid `.uproject` switch | `switchResult=failed`; shared config unchanged |

## Soak (optional before main)

- 30× project switch via `unreal_set_active_project` — no config loss, cache generation increments
- 30× write rollback — mutation history intact
