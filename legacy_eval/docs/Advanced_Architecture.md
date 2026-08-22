# Advanced Architecture (Phases 14-23)

> **ARCHIVED, UNSUPPORTED PYTHON CONTROLLER PIPELINE.** The ordered planner/tool-policy/write-gate pipeline below is historical documentation for repository-local wrapper/evaluation research. No supported MCP entry exposes it, and portable packages omit the monolithic controller. Direct callers do not inherit this sequence. The sole supported Strict implementation is Node `strict_begin`, which does not consume this task protocol.

Post-Phase-13 pipeline:

```
User request
  -> agent_orchestrator (classify, evidence plan, tool policy)
  -> RAG search (mode-aware FTS + optional hybrid)
  -> inspect (PAB, project graph, clangd navigation)
  -> smallest edit (patch preferred)
  -> static validate_unreal_readiness
  -> UBT (agent mode)
  -> failure rerag + failure memory hints
  -> retry with narrower scope
```

## Components

| Layer | Module | Role |
|-------|--------|------|
| Planner | `scripts/agent_orchestrator.py` | TaskKind, EvidencePlan, EditStrategy |
| Tool policy | `scripts/tool_policy.py` + `config/tool_orchestration.json` | Ordered MCP tools |
| Executor | `lmstudio_unreal_wrapper.py`, unreal-agent MCP | Apply patches/files, UBT |
| Verifier | validate_unreal_readiness, genre/runtime/claim gates | Block bad edits |
| Symbols | `clangd_helper.py` | Navigation only; UBT is truth |
| Metadata | `tools/ue_export/*`, `collect_editor_metadata.py` | Blueprint/config visibility |
| Graph | `build_project_graph.py` | C++/BP/config relationships |
| Regression | `run_eval_regression.py` | Before/after KPI deltas |

See also: [Agent_Orchestrator.md](Agent_Orchestrator.md), [Eval_Regression_Workflow.md](Eval_Regression_Workflow.md).
