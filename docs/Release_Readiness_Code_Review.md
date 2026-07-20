# Release Readiness — Code Review Summary

P0/P1 findings addressed in Release Readiness Plan implementation.

## P0 (resolved in tree)

| Finding | Fix |
|---------|-----|
| Legacy portable builders created multiple install paths | Replaced by [`build_integrated_package.py`](../scripts/build_integrated_package.py) |
| Full portable build expected sibling repos | Integrated package builder resolves one repository root and emits one inventory |
| CI missing installer gates | [`ci.yml`](../.github/workflows/ci.yml) `installer-gates` job |
| Non-atomic installer JSON for workspace/agent | `Write-JsonUtf8Atomic` in `Sync-WorkspaceJson` / `Sync-AgentMcpJson` |
| Partial mcp.json install on failure | `Write-McpConfigBatch` with backup restore |

## P1 (resolved / partial)

| Finding | Fix |
|---------|-----|
| Node atomic-io fixed `.tmp` name | Unique temp + fsync in [`atomic-io.js`](../lmstudio-unreal-agent-mcp/src/atomic-io.js) |
| edit-bundle direct writes | Uses `atomicWriteText` |
| Blocked tool envelope incomplete | Forwards `userMessage` / `agentInstruction` |
| README vs README-PORTABLE index claim | Cross-linked OSS vs ZIP sections |
| Version confusion | [`VERSIONING.md`](VERSIONING.md) |

## P2 (remaining)

| Finding | Notes |
|---------|-------|
| `agent-mcp.json` machine-specific paths in repo | Run Sync-InstallMachinePaths after clone; template hygiene |
| Manual UBT timeout / kill mid-write | Documented manual gates only |
| Full pytest unrelated failures | `test_agent_orchestrator`, `test_domain_refactor_source_hardening` |

## Out of scope (v-next)

- Full `apply_edit_bundle` rewrite
- Cross-process file locks
- Unified AGENT_STATE_ROOT
