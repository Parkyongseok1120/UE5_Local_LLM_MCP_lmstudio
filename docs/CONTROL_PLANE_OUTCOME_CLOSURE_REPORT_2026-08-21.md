# Control-plane outcome closure report — 2026-08-21

## Verdict

The reported `SERVER_CONTROL_STALLED_AFTER_SUCCESSFUL_REQUIRED_TOOL` event was one symptom of a broader ownership breach: routed RAG successes entered canonical Python task state, while early failures returned directly to MCP transport. LM Studio then persisted the plain text without MCP `isError`, and the Compactor converted the absent bit to `false`. The exact-tool replay guard therefore received a false success while the authoritative epoch and obligation correctly remained unchanged.

The correction restores one outcome transaction for both success and failure, retains a tri-state transport observation at the replay-suppression boundary, and moves installed RAG data out of versioned packages. The proven mutation, transaction-journal, validation, build, Automation, and rollback data plane is unchanged.

## Authoritative owners

### Existing

- `scripts/phase_tool_router.py` is the sole semantic reducer for phase, disposition, exact obligation, retry, blocker, and completion control.
- `scripts/task_api.py` owns atomic task state, authorization, control epochs, result ledgers, checkpoint/replan, synthesis acknowledgement, and terminal state.
- `lmstudio-unreal-agent-mcp` executes and records direct-source, mutation, validation, build, and Automation facts; it does not choose semantic control.
- `lmstudio-context-compactor-plugin` preserves/project server control, serializes exact commands, and owns UI delivery recovery; it is not a second reducer.
- `install.py` owns installed runtime configuration, stable data selection, migration, and query-readiness reporting.

### Added delta

- `task_commit_routed_analysis_outcome` commits both `succeeded` and `failed` RAG outcomes. The old success-only API remains a compatibility wrapper.
- Missing/unreadable/empty RAG marks `analysisCapabilities.ragIndex.available=false`, skips remaining index-dependent initial actions, and routes to bounded direct-source discovery. A compatible replan clears the capability observation.
- `routedAnalysisOutcomeLedger` retains the newest 32 authorization-free receipts with monotonic total/eviction counts and a ledger hash.
- Compactor snapshots preserve an absent error bit as unknown. `toolResultOutcome` distinguishes `success`, `failure`, and `unknown`; only affirmative success can trigger exact-tool replay suppression.
- Installed indexes default to `<state-home>/indexes/<namespace>/rag.sqlite`. Upgrade selects the newest query-ready prior index and hard-links its data directory when possible, copying only when linking is unavailable.

### Do not duplicate

- Do not create another semantic transition table in Node, Compactor, or RAG handlers.
- Do not replace mutation generation, transaction journal, CAS, static validation, build proof, Automation queue, synthesis ACK, or delivery recovery.
- Do not let a package-local relative index become installed data authority again.
- Do not infer transport success from human-readable prose.

## Production state inventory

### Task lifecycle states (19)

`BOOTSTRAP`, `INITIAL_DISCOVERY_REQUIRED`, `DISCOVERY_IN_PROGRESS`, `EVIDENCE_REQUIRED`, `EVIDENCE_COLLECTING`, `EVIDENCE_READY`, `MUTATION_REQUIRED`, `STATIC_VALIDATION_REQUIRED`, `BUILD_REQUIRED`, `AUTOMATION_REQUIRED`, `SYNTHESIS_REQUIRED`, `SYNTHESIS_PREPARED`, `SYNTHESIS_COMMIT_REQUIRED`, `DELIVERY_REQUIRED`, `AWAITING_USER_INPUT`, `DELIVERY_RECOVERY_AWAITING_USER`, `COMPLETE`, `TERMINAL_BLOCKED`, `CANCELLED`.

`COMPLETE`, `TERMINAL_BLOCKED`, and `CANCELLED` are terminal. The last two are intentionally not classified as recoverable.

### Lifecycle events (24)

`TASK_STARTED`, `INITIAL_DISCOVERY_REQUESTED`, `DISCOVERY_RESULT_COMMITTED`, `EVIDENCE_READ_COMMITTED`, `EVIDENCE_BUDGET_EXHAUSTED`, `EVIDENCE_READY_COMMITTED`, `SCOPE_APPROVAL_REQUESTED`, `USER_INPUT_COMMITTED`, `MUTATION_COMMITTED`, `STATIC_VALIDATION_PASSED`, `STATIC_VALIDATION_FAILED`, `BUILD_PASSED`, `BUILD_FAILED`, `AUTOMATION_PASSED`, `AUTOMATION_FAILED`, `SYNTHESIS_PREPARED`, `SYNTHESIS_COMMIT_ACKED`, `SYNTHESIS_COMMIT_REJECTED`, `DELIVERY_ACKED`, `DELIVERY_UNCERTAIN`, `OPERATOR_CONFIRMED_VISIBLE`, `OPERATOR_AUTHORIZED_REEMIT`, `TASK_CANCELLED`, `TERMINAL_BLOCKER_COMMITTED`.

### Canonical reducer events (5)

`PHASE_BUDGET_EXHAUSTED`, `EVIDENCE_STAGNATION`, `GATE_VALIDATION_FAILED`, `HANDLER_RECOVERY_FACT`, `TOOL_RESULT_COMMITTED`.

`TOOL_RESULT_COMMITTED` now means a committed outcome fact, not an implicit success. Its `outcome` is reduced symmetrically; failure either schedules the next non-index action or creates `analysis_dependency_recovery`.

### Recovery states (10)

`analysis_dependency_recovery`, `evidence_required`, `repair_planning_required`, `repair_required`, `revalidate_required`, `checkpoint_rebase_required`, `phase_budget_checkpoint_required`, `phase_budget_replan_required`, `environment_recovery`, `evidence_complete`.

Every recovery state declares a satisfaction event, next status, and next lifecycle. The new analysis dependency state exits on direct-source discovery into `evidence_required` / `EVIDENCE_COLLECTING`.

### Synthesis and proxy lifecycle

Canonical synthesis states (10): `pending`, `prepared`, `commit_sent`, `commit_acked`, `delivery_pending`, `delivery_uncertain`, `delivery_reemit_authorized`, `delivered`, `rejected_stale`, `evidence_recovery`.

Proxy-accepted states (12): the canonical ten plus `completed` and `committed`. Registry loading proves the canonical set is a subset of the proxy set.

## COMPLETE reachability proof

`control_state_registry.py` loads every declared state/event/edge, rejects an undeclared edge, requires an explicit transition list for all 19 states, and performs breadth-first search from every recoverable state. Import fails if any such state lacks a path to `COMPLETE`.

Shortest declared state paths are:

| Start | One declared path to `COMPLETE` |
|---|---|
| `BOOTSTRAP` | `BOOTSTRAP → AWAITING_USER_INPUT → MUTATION_REQUIRED → STATIC_VALIDATION_REQUIRED → BUILD_REQUIRED → COMPLETE` |
| `INITIAL_DISCOVERY_REQUIRED` | `INITIAL_DISCOVERY_REQUIRED → DISCOVERY_IN_PROGRESS → EVIDENCE_REQUIRED → AWAITING_USER_INPUT → MUTATION_REQUIRED → STATIC_VALIDATION_REQUIRED → BUILD_REQUIRED → COMPLETE` |
| `DISCOVERY_IN_PROGRESS` | `DISCOVERY_IN_PROGRESS → EVIDENCE_REQUIRED → AWAITING_USER_INPUT → MUTATION_REQUIRED → STATIC_VALIDATION_REQUIRED → BUILD_REQUIRED → COMPLETE` |
| `EVIDENCE_REQUIRED` | `EVIDENCE_REQUIRED → AWAITING_USER_INPUT → MUTATION_REQUIRED → STATIC_VALIDATION_REQUIRED → BUILD_REQUIRED → COMPLETE` |
| `EVIDENCE_COLLECTING` | `EVIDENCE_COLLECTING → EVIDENCE_READY → MUTATION_REQUIRED → STATIC_VALIDATION_REQUIRED → BUILD_REQUIRED → COMPLETE` |
| `EVIDENCE_READY` | `EVIDENCE_READY → MUTATION_REQUIRED → STATIC_VALIDATION_REQUIRED → BUILD_REQUIRED → COMPLETE` |
| `MUTATION_REQUIRED` | `MUTATION_REQUIRED → STATIC_VALIDATION_REQUIRED → BUILD_REQUIRED → COMPLETE` |
| `STATIC_VALIDATION_REQUIRED` | `STATIC_VALIDATION_REQUIRED → BUILD_REQUIRED → COMPLETE` |
| `BUILD_REQUIRED` | `BUILD_REQUIRED → COMPLETE` (or `AUTOMATION_REQUIRED → COMPLETE`) |
| `AUTOMATION_REQUIRED` | `AUTOMATION_REQUIRED → COMPLETE` |
| `SYNTHESIS_REQUIRED` | `SYNTHESIS_REQUIRED → SYNTHESIS_PREPARED → SYNTHESIS_COMMIT_REQUIRED → DELIVERY_REQUIRED → COMPLETE` |
| `SYNTHESIS_PREPARED` | `SYNTHESIS_PREPARED → SYNTHESIS_COMMIT_REQUIRED → DELIVERY_REQUIRED → COMPLETE` |
| `SYNTHESIS_COMMIT_REQUIRED` | `SYNTHESIS_COMMIT_REQUIRED → DELIVERY_REQUIRED → COMPLETE` |
| `DELIVERY_REQUIRED` | `DELIVERY_REQUIRED → COMPLETE` |
| `AWAITING_USER_INPUT` | `AWAITING_USER_INPUT → MUTATION_REQUIRED → STATIC_VALIDATION_REQUIRED → BUILD_REQUIRED → COMPLETE` (evidence resume is the symmetric alternative) |
| `DELIVERY_RECOVERY_AWAITING_USER` | `DELIVERY_RECOVERY_AWAITING_USER → COMPLETE` or `→ DELIVERY_REQUIRED → COMPLETE` |
| `COMPLETE` | already complete |

This graph proof is paired with reducer tests, because graph existence alone cannot prove that a runtime fact reaches the reducer. The new missing-index test observes: exact `unreal_symbol_lookup` obligation → typed failed outcome commit → epoch increment → `search_files` obligation. A stale replay using the old authorization is rejected.

## Transition non-blocking analysis

| Symmetric path | Preserved behavior | New guard |
|---|---|---|
| RAG success | Advances discovery cursor, records evidence hash, emits new control | Same transaction also records a bounded success receipt and marks the index observed available |
| RAG failure | Previously bypassed task state | Records failure, advances/changes obligation, and emits structured `ok:false` plus canonical control |
| Direct-source success | Existing Agent reserve/execute/commit path | Unchanged; it can satisfy the RAG dependency fallback |
| Mutation/build/Automation | Existing journals, generations, proof tuples, queue-empty rule | No source changes in those execution modules |
| Explicit MCP failure | `isError=true` or structured negative remains failure | Unknown is not promoted to success at replay suppression |
| Legacy human-readable success | Remains non-failing for evidence compatibility | It cannot suppress a once/forbidden exact-tool replay unless affirmative success is observable |
| Replan | Existing compatible replan rotates authorization/control | Clears the stale RAG availability observation; retains bounded historical receipts |

## Capacity invariant comparison

| Subsystem | Owner / persistence | Unit and bound | Overflow policy | Recovery/continuation |
|---|---|---:|---|---|
| Global directory abuse guard | Agent process / process-local | 24 calls per 5-minute window; 2 per scoped path | Reject call | Sliding window/process restart; use search or known-file read |
| Durable directory workflow budget | Python task state | `maxDirectoryLists` per task + plan revision + phase | Canonical checkpoint obligation | `unreal_task_checkpoint` then compatible replan |
| Phase tool-call budget | Python task route usage | 12 work calls per phase; controls are non-budgeted | Canonical phase-budget state | checkpoint then one compatible replan window |
| Inspection result producer | Agent direct discovery | 1,000 candidates/result | Producer truncation must disclose totals/hash | Durable frontier receives bounded continuation facts |
| Inspection frontier | Python task state | 4,096 paths | Workflow stop with `EVIDENCE_FRONTIER_CAPACITY_EXCEEDED` | Replan/new scope; never silent loss |
| Repository audit page/queue | Python task state | 128/page, 4,096 retained/inventory | Workflow stop with `REPO_AUDIT_INVENTORY_OVERFLOW` | Cursor + inventory hash page continuation |
| Slice plan/checkpoint ledger | Python task state | 1,024 slices and 1,024 completed receipts | Reject oversized plan | Smaller/revised plan |
| Routed analysis outcome ledger | Python task state | 32 receipts | Evict oldest, preserve total/eviction/hash | Observability only; never queues work, so eviction cannot remove an obligation |
| Model-visible control projection | Agent/Python projection | 32,000 characters | Priority compaction | Required control header remains present; bulky evidence stays digest-bound |
| Automation queue | Agent executor + Python proof | No numeric work admission claim; terminal empty is mandatory | No completion while non-empty | Continue/poll bounded executor or enter typed failure recovery |
| Synthesis evidence selection | Python ledger/readiness | Exact representative subset under prompt budget | Readiness false; no fabricated completeness | Continue evidence/replan or explicit coverage limitation |

The critical cross-capacity relations are executable: audit retention and maximum inventory may not exceed the durable frontier (4,096); slice production and checkpoint capacities are equal (1,024); outcome receipts are explicitly non-authoritative and may evict only historical observability; the 32K projection cannot discard required header fields.

## Evidence classification

- **P0 Bug / RuntimeVerified:** the installed `3ecd286` RAG process pointed at a missing package-local index, so the exact required query could not work.
- **P1 Bug / Runtime+SourceVerified:** LM Studio persisted only text, and Compactor manufactured `isError=false`, creating a false successful-stall diagnosis.
- **P1 Bug / SourceVerified:** early RAG failures never called the canonical result commit, so epoch and obligation could not advance.
- **P1 Architecture gap / SourceVerified:** installer success did not distinguish configured RAG code from query-ready RAG data.
- **Correction / TestVerified:** targeted Python transition/MCP/capacity tests and Compactor tri-state/replay tests pass. Full non-E2E suite and physical reinstall evidence are recorded in the final change handoff.

## Counterevidence and alternatives checked

- Stale runtime code was disproved: Agent/RAG/Compactor runtime identities matched the installed commit.
- Model failure was disproved: the direct server-owned exact call bypassed model tool selection.
- Argument mismatch was disproved for the original trace: the successful preceding `search_files` advanced state, while the exact RAG call reached the provider and failed on index existence.
- Removing the replay guard was rejected: it correctly prevents an affirmative once-only tool from looping; the defect was its binary classifier and missing failure commit.
- Treating every unknown result as failure was rejected after symmetric transition tests: legacy plain-text evidence tools must remain non-failing, while safety decisions require affirmative success.
- Raising queue/budget limits was rejected: owner, unit, persistence, overflow, and recovery semantics differ across abuse guard, workflow budget, frontier, audit queue, receipt ledger, and context projection.

## Validation boundary

E2E/model/GUI tests are intentionally excluded. Required validation is deterministic Python/Node tests, TypeScript build, protocol validation, package build, Agent-mode install, query-level RAG readiness, MCP initialize/tools-list smoke, runtime identity, and context-compactor activation. Actual Unreal mutation/build behavior is preserved by source isolation plus its existing non-E2E suites; no new runtime mutation is used as proof for this control-plane correction.
