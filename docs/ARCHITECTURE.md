# Architecture

```
collect_* → raw_*.jsonl → build_rag_index.py → rag.sqlite (FTS)
  → rag_search.py (mode + retrieval layers)
  → rag_context.py (ordered evidence)
  → unreal_rag_mcp.py / unreal-agent MCP
  → static validation → UBT (agent mode) → failure rerag → retry
```

## MCP roles

| Server | Role |
|--------|------|
| **unreal-rag** | Search, symbols, genre gates, architecture brief, compile loop jobs |
| **unreal-agent** | read/write/replace, detect project, UBT build |

## Workflow (10 steps)

1. Retrieve evidence 2. Classify mode 3. **Agent plan** (orchestrator) 4. Assemble context 5. Inspect project state
6. Smallest edit (files or patches) 7. Static validation 8. UBT 9. Parse logs 10. Retry

Phases 14-23: see [Advanced_Architecture.md](Advanced_Architecture.md).

## Optional Retrieval Sidecars

`rag_search.py` can add compact `rag_sidecar` rows for symbol graph hits, module resolver hints, and error-route hints. These sidecars are optional: missing `data/symbol_graph/symbol_graph.json` does not block search, and sidecars never replace normal FTS results.

See [Safe_Agent_Mode.md](Safe_Agent_Mode.md), [Project_Routing.md](Project_Routing.md), [Build_Cs_Parser.md](Build_Cs_Parser.md).
