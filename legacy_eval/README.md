# Historical workflow/evaluation archive

This tree contains pre-Direct task, route, planner, synthesis, wrapper, and
evaluation artifacts retained only for forensic comparison. It is not a
supported runtime, is not imported by the default test suite, and is excluded
from integrated packages and CI production gates.

Files here may no longer form an executable application. Do not add this
directory to `PYTHONPATH`, point an MCP entry at it, or treat its tests and
configuration as current product behavior. The supported entry points are:

- Python RAG: `scripts/unreal_rag_direct.py`
- Node Direct: `lmstudio-unreal-agent-mcp/src/direct-server.js`
- Node Strict opt-in: `lmstudio-unreal-agent-mcp/src/strict-server.js`
- Context compactor: `lmstudio-context-compactor-plugin/src/index.ts`

The archived holdout/profile/index runners and asset-graph lookup helpers are
for forensic comparison only. Several intentionally retain references to
retired evaluators, graph builders, or cache warmers, so moving them back into
the repository root would recreate a dangling, unsupported product surface.
