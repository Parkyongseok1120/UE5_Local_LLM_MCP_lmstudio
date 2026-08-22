# Architecture

> **CURRENT DIRECT ARCHITECTURE.** The supported Python server is a small factual RAG service with no task/controller state. The sole supported Strict implementation is the independent Node `strict_begin` lifecycle. Planner/gate/checkpoint controllers are not installed or exposed.

```
collect_* → raw_*.jsonl → build_rag_index.py → rag.sqlite (FTS)
  → bounded Direct factual retrieval + evidence formatting
  → unreal_rag_direct.py (8 read/index/project tools)

unreal-agent MCP (20 Direct file/log/edit/build/test/command tools)
  → containment + read hashes + mutation authority + immediate diagnostics

optional Node strict-server.js
  → conversation-scoped strict_begin/complete/fail/cancel lifecycle
```

## MCP roles

| Server | Role |
|--------|------|
| **unreal-rag** | Eight factual capabilities: active project get/set, search, symbol lookup, health, rebuild status, synchronous refresh, and inventory |
| **unreal-agent** | Twenty Direct project/file/log/edit/build/test/allowlisted-command capabilities |
| **unreal-agent-strict** | Optional Node-only conversation lifecycle around the Node capability surface |

## Direct workflow

A common implementation flow is exact project selection, focused search/inspection, exact target reads, the smallest safe edit, and a relevant build/test diagnostic. This is model-selected work, not a server-owned sequence. Direct has no planner, route owner, required-next-tool instruction, handoff state, or synthesis acknowledgement.

The Python Direct entry ignores `MCP_EXECUTION_MODE` and never imports the unsupported monolithic controller. Node Strict is a separate executable; it does not share authorization or state with Python RAG.

## Optional Retrieval Sidecars

The Direct RAG path does not emit route sidecars, required reads, allowed patch targets, forbidden actions, assembly instructions, or other synthesized sequencing. It returns bounded source/document evidence, locators, project identity, and freshness only. Historical retrieval modules may still contain sidecar implementations for offline evaluation, but they are outside the Direct import boundary.

See [Safe_Agent_Mode.md](Safe_Agent_Mode.md), [Project_Routing.md](Project_Routing.md), [Build_Cs_Parser.md](Build_Cs_Parser.md).
