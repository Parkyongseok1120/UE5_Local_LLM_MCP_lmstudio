# Stabilization E2E Matrix (pre-main merge)

## Automated gates (CI — `.github/workflows/ci.yml`)

| Gate | CI job | Notes |
|------|--------|-------|
| UTF-8 BOM check | python-tests | Extra vs local matrix |
| `verify_encoding.py` | python-tests | |
| Full `pytest -q` | python-tests | |
| Hardening fault-injection subset | python-tests | Includes `test_atomic_io`, `test_mcp_envelope` via full pytest |
| Domain contract gate | python-tests | Extra |
| Repetition gate (×3) | python-tests | Extra |
| `ruff check scripts/ tests/` | python-lint | With `--select` / `--ignore` |
| `npm test` + `node --check` | node-install | Checks server, context-ux, build-proof |
| OSS readiness | oss-ready | Extra |
| `Verify-UnrealMcp.ps1 -RepoOnly` | installer-gates | Repo layout; BYOI index = WARN |
| `install.py --dry-run` | integrated-installer | Zero mutations |
| `build_integrated_package.py` | integrated-package | Reproducible cross-platform bundle |

Local full matrix (optional):

```powershell
ruff check scripts/ tests/
pytest -q
python scripts/verify_encoding.py
npm test --prefix lmstudio-unreal-agent-mcp
node --check lmstudio-unreal-agent-mcp/src/server.js
scripts\installer_support\Verify-UnrealMcp.ps1 -RepoOnly
python install.py --profile safe --yes --dry-run
python scripts/build_integrated_package.py --output OUTSIDE_REPO_PATH
pytest tests/test_tool_manifest_contract.py tests/test_tool_call_authorization.py tests/test_project_cache_generation.py tests/test_atomic_io.py tests/test_mcp_envelope.py tests/test_mcp_stable_subprocess_e2e.py tests/test_installer_gates.py -q
```

### Automated fault-injection (pytest / npm)

| Test file | Pass criteria |
|-----------|----------------|
| `tests/test_tool_call_authorization.py` | Hidden/extended tools rejected via `tools/call` (Python + Node) |
| `tests/test_project_cache_generation.py` | 1 switch = +1 generation; 100 observer calls = stable |
| `tests/test_atomic_io.py` | Atomic replace updates content |
| `tests/test_mcp_envelope.py` | Shared envelope fields on error payloads |
| `tests/test_installer_gates.py` | WhatIf zero mutation; Resolve-RagIndexPath namespaces |
| `lmstudio-unreal-agent-mcp/test/validation-dirty-corrupt.test.js` | Corrupt validation.json blocks build |
| `lmstudio-unreal-agent-mcp/test/atomic-io.test.js` | Node atomic write updates content |
| `tests/test_mcp_stable_subprocess_e2e.py` | Dual MCP subprocess health + negative tool calls |

## Manual gates (pre-stable label)

| Scenario | Pass |
|----------|------|
| Clean Win11 install | Verify green; Cline shows both servers |
| Existing Cline (3+ MCP servers) | Non-Unreal servers preserved after merge install |
| UE 5.7 / 5.8 / 5.9 | `workspace.json.indexPath` matches MCP `--index` arg |
| Cline `-WhatIf` | Zero filesystem mutations |
| Main installer `-WhatIf` | Zero filesystem mutations |
| Cline reinstall identical content | Second run: no backup, no write |
| MCP kill mid-write (manual) | Original source file intact |
| UBT timeout (manual) | No orphan compiler processes |
| Portable ZIP build | `build_integrated_package.py` succeeds from the repository clone |

## Release gate

1. All automated gates green on exact commit (GitHub Actions)
2. Manual install matrix on at least one clean Windows machine
3. Smoke on 2+ non-sample `.uproject` projects
4. No stack traces or tokens in user-visible MCP errors
