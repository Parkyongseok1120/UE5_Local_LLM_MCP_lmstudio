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

`rag_search.py` can add compact `rag_sidecar` rows for symbol graph hits, module resolver hints, and error-route hints. These sidecars are optional: missing `data/symbol_graph/symbol_graph.json` does not block search, and sidecars never replace normal FTS results. Symbol graph hints now carry an explicit source-location proof boundary: they support navigation and impact discovery, never standalone claims of runtime behavior, wiring, or data flow.

## P0–P3 reasoning rails

`build_symbol_graph.py` is the portable source foundation. Its v2 artifact retains legacy symbol lookup while adding direct source relation edges and separately labeled heuristic call candidates. A project-root build includes plugins/tests, prunes generated directories before recursion, and records unreadable-source gaps. `code_generation_contract.py` requires valid source targets, paired surfaces, invariants, and validation before a project-specific draft is presented as a patch. `change_impact_contract.py` extends the existing refactor/compile retry rail with direct impacts, candidate impacts, explicit truncation/unmatched-symbol blocking, and a targeted regression/coverage-gap plan. `architecture_reasoning.py` adds source-boundary, candidate data-flow/state-transition analysis and validates typed architecture proposal fields, graph freshness, focused symbols, and cycle gates before its implementation gate can open.

These are workflow rails, not evidence that a local model has learned new capabilities. See [Architecture Understanding Layer](architecture/Architecture_Understanding_Layer.md) for command examples and proof limits.

See [Safe_Agent_Mode.md](Safe_Agent_Mode.md), [Project_Routing.md](Project_Routing.md), [Build_Cs_Parser.md](Build_Cs_Parser.md).
