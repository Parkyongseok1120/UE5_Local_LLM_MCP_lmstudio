# Unreal57 -> Unreal58 rename checklist (UE 5.8)

## Physical paths
| Before | After |
|--------|-------|
| `%USERPROFILE%\.lmstudio\Unreal58-RAG` | `Unreal58-RAG` |
| `data\unreal58\` | `data\unreal58\` |
| `config\unreal_57_seed_urls.txt` | `unreal_58_seed_urls.txt` |

## Environment variables
| Before | After |
|--------|-------|
| `UNREAL58_ROOT` | `UNREAL58_ROOT` (legacy `UNREAL58_ROOT` still read in workspace_paths.py) |
| `UNREAL58_PORTABLE_ROOT` | `UNREAL58_PORTABLE_ROOT` |

## LM Studio / MCP
- `%USERPROFILE%\.lmstudio\mcp.json` — unreal-rag index + UNREAL58_ROOT
- `%USERPROFILE%\.lmstudio\.internal\last-synced-mcp-state.json`
- `%USERPROFILE%\.lmstudio\config\unreal-workspace.json` — projectSearchRoots
- `%USERPROFILE%\.lmstudio\lmstudio-unreal-agent-mcp\config\agent-mcp.json`
- LM Studio extension bridge configs under `extensions\plugins\mcp\`

## Cline
- `%USERPROFILE%\.cline\data\settings\cline_mcp_settings.json`
- VS Code: `%APPDATA%\Code\User\globalStorage\saoudrizwan.claude-dev\settings\cline_mcp_settings.json`
- Template: `config\cline_mcp_settings.template.json`

## Workspace internals
- `config\workspace.json` — workspaceName, rootPath, indexPath
- `rag.ps1` — all `data\unreal58\` paths
- `scripts\*.py`, `scripts\*.ps1` — index/out-dir defaults
- `installer\*.ps1` — portable + install paths
- Eval configs, baseline JSON, build_manifest.jsonl paths

## Portable backup (optional local staging)

Use a maintainer-chosen output directory (for example `$env:TEMP\Unreal58-RAG-Portable` or an external drive path you control). Do not commit machine-specific drive letters to the public repo.
- `$HOME\.lmstudio\Unreal58-RAG-Portable` — old C: staging (deprecated)
- Scaffold `Intermediate/` build artifacts — stale paths; delete or rebuild scaffold if needed

## After rename
1. Restart LM Studio and Cline
2. `cd Unreal58-RAG; .\rag.ps1 doctor`
3. `.\installer\Verify-UnrealMcp.ps1`
