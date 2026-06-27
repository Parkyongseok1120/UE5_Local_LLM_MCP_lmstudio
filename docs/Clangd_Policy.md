# clangd trust policy

**UBT / MSVC = build truth.** clangd is navigation-only.

## Trust order

```
UBT/MSVC build result
  > validate_unreal_readiness
  > module graph / include_owner sidecar
  > regex symbol index
  > clangd go-to-definition / find-references (navigation)
  > clangd diagnostics (low trust unless UBT confirms)
```

## MCP tools

| Tool | Purpose |
|------|---------|
| `clangd_document_symbols` | Document symbols (clangd or heuristic fallback) |
| `clangd_goto_definition` | Go to definition |
| `clangd_find_references` | Find references (grep fallback if no clangd) |

Requires `compile_commands.json` for full LSP (generate via UBT when needed).

Do not block builds or agent retries on clangd diagnostics alone.

See `scripts/clangd_helper.py`.
