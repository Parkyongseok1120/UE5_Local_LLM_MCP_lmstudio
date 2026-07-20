# Phase 0 — Pre-Improvement Audit Baseline

Generated as the frozen reference before the UE Agent phased improvement plan.

## Pipeline

```
collect_* → raw_*.jsonl → build_rag_index.py → rag.sqlite (FTS)
  → rag_search.py (mode-aware) → rag_context.py → unreal_rag_mcp.py
  → unreal-agent MCP → UBT / validate-write
  → lmstudio_unreal_wrapper.py (compile loop)
```

## Frozen surfaces (do not break without explicit phase)

| Surface | Path | Rule |
|---------|------|------|
| CLI commands | `rag.ps1` | Keep command names and default paths |
| FTS core | `scripts/rag_search.py`, `scripts/build_rag_index.py` | Extend only; do not remove BM25 path |
| Broad RAG modes | `compile_fix`, `module_fix`, `reflection_fix`, etc. | Extend with subkinds; do not replace |
| Index schema | `chunks` table + metadata JSON | Add fields compatibly only |
| Dual MCP | `unreal-rag` + `unreal-agent` | Incremental dedup only |

## Rollback tags

- Feature flags: `UNREAL_RAG_LEGACY_PROJECT_FILTER=1`, `UNREAL_RAG_PROJECT_ROUTING=v1`
- Reindex rollback: keep prior `data/unreal58/rag.sqlite` + `build_manifest.json` before Phase 1 reindex
- Installer: `python install.py --profile standard --yes` restores the SAFE profile

## Known gaps at baseline

- Build.cs parsing: `AddRange(new string[])` only in collectors
- activeProject auto-filter on all MCP searches
- Wrapper full-file rewrite only (no patches[])
- Installer defaults ALLOW_WRITE/COMMANDS/UNREAL_BUILD=1
- eval-e2e-compile cases empty; eval-reasoning tool_order mock

## Improvement phases

See attached plan: Build.cs → routing → taxonomy → safe profile → patches → clangd → BP metadata → retrieval layers → eval → failure memory → PAB → sampling → docs.
