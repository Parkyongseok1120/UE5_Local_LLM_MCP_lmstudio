# Architecture

> **CURRENT DIRECT ARCHITECTURE.** The supported Python server is a small factual RAG service with no task/controller state. The sole supported Strict implementation is the independent Node `strict_begin` lifecycle. Planner/gate/checkpoint controllers are not installed or exposed.

```
collect_* → exact project/engine provenance → raw_*.jsonl
  → build_rag_index.py → engine-bound sibling rag.sqlite shards (FTS)
  → exact-project shard selection + bounded factual evidence formatting
  → unreal_rag_direct.py (8 read/index/project tools)

unreal-agent MCP (20 Direct file/log/edit/build/test/command tools)
  → containment + scoped fileVersionReceipt snapshots + CAS mutation authority
  → shared bounded Build/Automation process owner + immediate diagnostics

optional codex/unreal-context-compactor chat plugin (host toggle OFF by policy)
  → factual active objective/continuation/work/file/tool/build continuity only
  → canonical project/path + observed SHA; runtime-local mutation receipt omitted

optional Node strict-server.js
  → conversation-scoped strict_begin/complete/fail/cancel lifecycle
```

## MCP roles

| Server | Role |
|--------|------|
| **unreal-rag** | Eight factual capabilities: active project get/set, search, symbol lookup, health, rebuild status, synchronous refresh, and inventory |
| **unreal-agent** | Twenty Direct project/file/log/edit/build/test/allowlisted-command capabilities, including scoped receipt/CAS mutation and the shared bounded process runner |
| **context compactor** | Installed/pinned for availability; the installer never enables host-owned chat activation, and the inner compaction opt-in defaults OFF; canonical file observations become `fresh_read_required` and runtime-local mutation receipts are not durable, while user-authored payment-receipt and code-symbol language is preserved; no planner, router, tool authority, or completion authority |
| **unreal-agent-strict** | Optional Node-only conversation lifecycle around the Node capability surface |

## Direct workflow

A common implementation flow is exact project selection, focused search/inspection, exact target reads that issue `fileVersionReceipt`, the smallest receipt/CAS-safe edit, and a relevant build/test diagnostic. Every existing-file mutation explicitly passes that receipt or a valid raw `expectedHash`; same-session evidence is never selected automatically, and successful mutations issue a new receipt for consecutive edits. This is model-selected work, not a server-owned sequence. Direct has no planner, route owner, required-next-tool instruction, handoff state, or synthesis acknowledgement.

Standalone creates, replacements, and recoverable deletes re-resolve their lexical target, canonical real target, and containment root under the cooperative path lock; deletion also verifies the real trash ancestor before directory creation and the real trash parent afterward. Atomic bundle patches recheck their frozen canonical target immediately after write-ahead and before CAS. These checks close deterministic in-process path swaps, but path-based OS I/O still has a post-revalidation window. They are not an OS handle-relative `no-follow` guarantee against a hostile same-user process changing filesystem topology after the last revalidation.

Build and Automation dispatch through one bounded process owner that retains head
and tail output, reports omitted bytes, persists the same bounded projection, and
terminates the process tree on timeout. The portable `Editor` target alias maps
to the selected project's canonical, configured preferred, or sole discovered
custom Editor target; an explicit non-Editor target is never rewritten.

The Python Direct entry ignores `MCP_EXECUTION_MODE` and never imports the unsupported monolithic controller. Node Strict is a separate executable; it does not share authorization or state with Python RAG.

## Optional Retrieval Sidecars

The Direct RAG path does not emit route sidecars, required reads, allowed patch targets, forbidden actions, assembly instructions, or other synthesized sequencing. It returns bounded source/document evidence, locators, exact project identity, engine-bound shard provenance, and freshness only. An exact project selector routes to its compatible sibling shard; one call cannot merge projects from different engine shards. Historical retrieval modules may still contain sidecar implementations for offline evaluation, but they are outside the Direct import boundary.

See [Safe_Agent_Mode.md](Safe_Agent_Mode.md), [Project_Routing.md](Project_Routing.md), [Build_Cs_Parser.md](Build_Cs_Parser.md).
