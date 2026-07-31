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

`build_symbol_graph.py` is the portable source foundation. Its v2 artifact retains legacy symbol lookup while adding direct source relation edges and separately labeled heuristic call candidates. A project-root build includes plugins/tests, prunes generated directories before recursion, and records unreadable-source gaps. `code_generation_contract.py` requires valid source targets, paired surfaces, invariants, and validation before a project-specific draft is presented as a patch. `change_impact_contract.py` extends the existing refactor/compile retry rail with direct impacts, candidate impacts, explicit truncation/unmatched-symbol blocking, and a targeted regression/coverage-gap plan. `architecture_reasoning.py` adds source-boundary and candidate data-flow/state-transition analysis, infers explicit lifecycle/cardinality/authority/network/persistence/scale/designer/boundary requirements, then searches a bounded Unreal pattern catalog and compositions for three to five viable candidates when the declared requirements are consistent. Hard contradictions are eliminated before candidates are scored for fit, testability, migration, complexity, risk, and performance; if none remain, the result requires the conflicting requirements to be corrected or partitioned instead of restoring an invalid candidate. The result preserves ambiguity and requires matching project-source owner evidence before it can recommend an owner; build/runtime proof and explicit selection are still required before implementation.

`feature_intent_contract.py` closes the earlier gap between an ambiguous feature request and those implementation gates. Ambiguous edit/refactor requests receive three compact, deterministically scored candidates covering ownership/lifetime, authority/replication, persistence, failure semantics, user-visible behavior, and non-goals. `unreal_feature_intent_resolve` requires an eligible selection, rationale, explicit observer/oracle acceptance criteria, and exact target snapshots. The selected intent ID and contract hash are persisted in task state and bound to the active plan revision and checkpoint; write authorization fails closed if any binding becomes stale. Low-ambiguity reversible, bounded edits keep the legacy fast path.

Long-running task state now has a renewable lease and file-hash checkpoint. Recovery detects edits made by another worker and closes both Python and Node write gates until an explicit rebase accepts current files. Runtime debugging follows a separate fail-closed state machine: rank hypotheses, record a same-reproduction experiment, compare two to four isolated patch candidates, apply the selected candidate, and verify the same observer against metric/trace/soak policy.

The phase route separates budgeted work tools from an always-discoverable control surface (`status`, `checkpoint`, and `cancel`). Recording a checkpoint resets the current phase call budget even when the phase name does not change. Time-limited gate evidence carries a deterministic fallback route; Python authorization and LM Studio's Node watcher both re-evaluate it at expiry, so a stale gate is removed without requiring a restart or an unrelated state mutation.

An active phase contains 5-10 budgeted work tools; the three control tools are outside that count. Bounded replan is another separate surface. It atomically retains the active `taskSessionId`, increments `planRevision`, rotates the authorization token, and invalidates plan-bound proof rather than creating a second running owner. A monotonic `checkpointGeneration` permits one replan per explicit checkpoint record. Autonomy-only blocked routes may expose this replan action; lease-expired, checkpoint-conflicted, or ambiguous routes do not.

High-ambiguity feature approval is not exposed as a model-callable boolean or MCP approval transition. The resolver persists a challenge bound to the task session, plan revision, intent contract, and expiry. A human approves it through `scripts/approve_feature_intent.py`; the resolver then consumes that approved record once. Expired, rebound, pending, or replayed records fail closed.

Refactor writes have an additional `unreal_semantic_refactor_guard`. It compares the live project with a distinct isolated candidate, hashes the complete `Source`/`Plugins`/`Config` transition, inventories reflected/public/module/config surfaces, and binds static/build/runtime proof plus paired invariant observers to that exact diff. Removed or changed contract surfaces require explicit migration/compatibility coverage. Its proof boundary is deliberately narrower than full behavioral equivalence.

These are workflow rails, not evidence that a local model has learned new capabilities. See [Architecture Understanding Layer](architecture/Architecture_Understanding_Layer.md) for command examples and proof limits.

See [Safe_Agent_Mode.md](Safe_Agent_Mode.md), [Project_Routing.md](Project_Routing.md), [Build_Cs_Parser.md](Build_Cs_Parser.md).
