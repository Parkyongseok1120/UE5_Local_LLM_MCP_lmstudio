# Production control-plane refactor report — 2026-08-21

## 1. Scope and evidence boundary

This report supersedes the current-state conclusions in `CONTROL_PLANE_CLOSURE_REPORT.md` for the refactor that starts from baseline `6cd16c5555dc9a4efdc8e6cd82fb5f93db446c94`.

The investigation treated the observed LM Studio loop as one symptom of a production state-machine failure. It covered the Python task server, Unreal RAG MCP, Unreal Agent MCP, Compactor checkpoint/generator, control projection, evidence/readiness, package identity, and installer. The mutation journal, write authorization, CAS, static validation, Unreal build, Automation, and rollback data plane was preserved.

The user explicitly excluded model/GUI E2E. Unit, integration, build, package, installer, runtime-identity, MCP startup, and tools/list checks are in scope. A fresh LM Studio chat, model prediction, UI delivery observation, or actual Unreal mutation/build is not evidence produced by this run.

## 2. Outcome

The primary incident chain is closed at source level:

1. An oversized LM Studio result entered the final 32,000-character control projection.
2. The old minimal projection discarded `authoritative=true` and other canonical identity fields.
3. Compactor correctly rejected that malformed v2, but the exact Python-required tool was consequently unavailable to the model-facing scheduler.
4. Agent/RAG legacy recovery surfaces then alternated without an executable canonical checkpoint command.

Node and Python now preserve a bounded authoritative header through the final projection. Compactor accepts the resulting v2 in an explicit cross-runtime regression test. Task-owned payloads without authoritative v2 remain fail closed and cannot fall back to legacy semantic routing.

The broader refactor also closes the discovered inspection capacity, phase-budget, source-extension, representative-evidence, objective-projection, and route-fallback defects. Synthesis UI delivery is intentionally not called exactly-once: the installed LM Studio SDK supplies neither a display receipt nor an idempotent insertion key. An uncertain emit now becomes an explicit, resumable operator decision instead of a permanent dead end or an automatic duplicate.

## 3. Production ownership after the refactor

| Plane | Owner | Contract |
|---|---|---|
| Task semantics | `scripts/phase_tool_router.py` through `scripts/task_api.py` | Sole owner of phase, disposition, exact required command, allowed commands, blocker, retry, user-input, and completion decisions. |
| Durable task facts | Python task state plus Agent committed-result adapter | Tool handlers record facts/results; canonical control is re-derived and committed by Python. |
| Tool execution | Unreal Agent MCP / Unreal RAG MCP | Executes an authorized exact command and returns the transaction-committed authoritative control verbatim. |
| Model-facing preservation | Compactor | Validates/preserves authoritative v2, exposes only canonical allowed schemas, directly serializes fully server-owned exact commands, and never invents task semantics. |
| Mutation/build data plane | Existing journal/CAS/validation/build/Automation/rollback modules | Preserved. No ownership migration or weakened gate was introduced. |
| Synthesis delivery | Python commit/ack lifecycle plus Compactor durable delivery state | Commit and delivery are separate. Uncertain host display requires operator confirmation or explicit duplicate-risk re-emission authorization. |

`config/control_state_machine.json` declares the semantic owner and explicitly marks both Node adapters as unable to choose semantic control.

## 4. Extracted lifecycle state machine

The executable registry declares 19 states. `COMPLETE`, `TERMINAL_BLOCKED`, and `CANCELLED` are terminal. `DELIVERY_RECOVERY_AWAITING_USER` is separate from ordinary `AWAITING_USER_INPUT`, preventing a scope-approval prompt from being mistaken for a synthesis-display attestation.

| State | Successful continuation toward `COMPLETE` |
|---|---|
| `BOOTSTRAP` | `TASK_STARTED → INITIAL_DISCOVERY_REQUIRED` or `EVIDENCE_REQUIRED` |
| `INITIAL_DISCOVERY_REQUIRED` | `INITIAL_DISCOVERY_REQUESTED → DISCOVERY_IN_PROGRESS` |
| `DISCOVERY_IN_PROGRESS` | `DISCOVERY_RESULT_COMMITTED → EVIDENCE_REQUIRED/EVIDENCE_COLLECTING` |
| `EVIDENCE_REQUIRED` | `EVIDENCE_READ_COMMITTED → EVIDENCE_COLLECTING` |
| `EVIDENCE_COLLECTING` | `EVIDENCE_READY_COMMITTED → EVIDENCE_READY`; exhausted phase returns to bounded evidence recovery |
| `EVIDENCE_READY` | analysis: prepare synthesis; implementation: accept input/plan and enter mutation |
| `MUTATION_REQUIRED` | `MUTATION_COMMITTED → STATIC_VALIDATION_REQUIRED` |
| `STATIC_VALIDATION_REQUIRED` | pass → build; fail → bounded repair mutation |
| `BUILD_REQUIRED` | pass → `COMPLETE` or required Automation; fail → evidence recovery |
| `AUTOMATION_REQUIRED` | pass → `COMPLETE`; fail → evidence recovery |
| `SYNTHESIS_REQUIRED` | `SYNTHESIS_PREPARED → SYNTHESIS_PREPARED` |
| `SYNTHESIS_PREPARED` | durable prepare → `SYNTHESIS_COMMIT_REQUIRED` |
| `SYNTHESIS_COMMIT_REQUIRED` | exact ACK → `DELIVERY_REQUIRED`; semantic NACK → evidence recovery |
| `DELIVERY_REQUIRED` | delivery ACK → `COMPLETE`; uncertainty → delivery-specific operator input |
| `AWAITING_USER_INPUT` | ordinary input → evidence or mutation path |
| `DELIVERY_RECOVERY_AWAITING_USER` | confirm visible → `COMPLETE`; authorize re-emit → `DELIVERY_REQUIRED` |
| `COMPLETE` | terminal |
| `TERMINAL_BLOCKED` | truthful terminal |
| `CANCELLED` | terminal |

The registry loader performs a graph search at import time. Every state except the two non-success terminals (`TERMINAL_BLOCKED`, `CANCELLED`) must have a declared path to `COMPLETE`; otherwise startup and protocol validation fail. Tests additionally reject undeclared states/events, event/handler drift, and delivery/general-user-input state mixing.

## 5. Event inventory

Lifecycle events are:

`TASK_STARTED`, `INITIAL_DISCOVERY_REQUESTED`, `DISCOVERY_RESULT_COMMITTED`, `EVIDENCE_READ_COMMITTED`, `EVIDENCE_BUDGET_EXHAUSTED`, `EVIDENCE_READY_COMMITTED`, `SCOPE_APPROVAL_REQUESTED`, `USER_INPUT_COMMITTED`, `MUTATION_COMMITTED`, `STATIC_VALIDATION_PASSED`, `STATIC_VALIDATION_FAILED`, `BUILD_PASSED`, `BUILD_FAILED`, `AUTOMATION_PASSED`, `AUTOMATION_FAILED`, `SYNTHESIS_PREPARED`, `SYNTHESIS_COMMIT_ACKED`, `SYNTHESIS_COMMIT_REJECTED`, `DELIVERY_ACKED`, `DELIVERY_UNCERTAIN`, `OPERATOR_CONFIRMED_VISIBLE`, `OPERATOR_AUTHORIZED_REEMIT`, `TASK_CANCELLED`, and `TERMINAL_BLOCKER_COMMITTED`.

The lower-level canonical committed-event reducer accepts exactly:

`PHASE_BUDGET_EXHAUSTED`, `EVIDENCE_STAGNATION`, `GATE_VALIDATION_FAILED`, `HANDLER_RECOVERY_FACT`, and `TOOL_RESULT_COMMITTED`.

These are intentionally different vocabularies. Lifecycle events define the proof graph; committed events are production inputs that normalize durable facts and cause the Python reducer to select the next lifecycle obligation. Unknown committed events raise instead of silently becoming no-ops.

## 6. Recoverability proof

The following shortest state-only paths are generated by the executable registry. Event labels are validated independently against the transition table.

| Start | Path to successful terminal |
|---|---|
| `BOOTSTRAP` | `BOOTSTRAP → AWAITING_USER_INPUT → MUTATION_REQUIRED → STATIC_VALIDATION_REQUIRED → BUILD_REQUIRED → COMPLETE` |
| `INITIAL_DISCOVERY_REQUIRED` | `INITIAL_DISCOVERY_REQUIRED → DISCOVERY_IN_PROGRESS → EVIDENCE_REQUIRED → AWAITING_USER_INPUT → MUTATION_REQUIRED → STATIC_VALIDATION_REQUIRED → BUILD_REQUIRED → COMPLETE` |
| `DISCOVERY_IN_PROGRESS` | `DISCOVERY_IN_PROGRESS → EVIDENCE_REQUIRED → AWAITING_USER_INPUT → MUTATION_REQUIRED → STATIC_VALIDATION_REQUIRED → BUILD_REQUIRED → COMPLETE` |
| `EVIDENCE_REQUIRED` | `EVIDENCE_REQUIRED → AWAITING_USER_INPUT → MUTATION_REQUIRED → STATIC_VALIDATION_REQUIRED → BUILD_REQUIRED → COMPLETE` |
| `EVIDENCE_COLLECTING` | `EVIDENCE_COLLECTING → EVIDENCE_READY → MUTATION_REQUIRED → STATIC_VALIDATION_REQUIRED → BUILD_REQUIRED → COMPLETE` |
| `EVIDENCE_READY` | `EVIDENCE_READY → MUTATION_REQUIRED → STATIC_VALIDATION_REQUIRED → BUILD_REQUIRED → COMPLETE` |
| `MUTATION_REQUIRED` | `MUTATION_REQUIRED → STATIC_VALIDATION_REQUIRED → BUILD_REQUIRED → COMPLETE` |
| `STATIC_VALIDATION_REQUIRED` | `STATIC_VALIDATION_REQUIRED → BUILD_REQUIRED → COMPLETE` |
| `BUILD_REQUIRED` | `BUILD_REQUIRED → COMPLETE` |
| `AUTOMATION_REQUIRED` | `AUTOMATION_REQUIRED → COMPLETE` |
| `SYNTHESIS_REQUIRED` | `SYNTHESIS_REQUIRED → SYNTHESIS_PREPARED → SYNTHESIS_COMMIT_REQUIRED → DELIVERY_REQUIRED → COMPLETE` |
| `SYNTHESIS_PREPARED` | `SYNTHESIS_PREPARED → SYNTHESIS_COMMIT_REQUIRED → DELIVERY_REQUIRED → COMPLETE` |
| `SYNTHESIS_COMMIT_REQUIRED` | `SYNTHESIS_COMMIT_REQUIRED → DELIVERY_REQUIRED → COMPLETE` |
| `DELIVERY_REQUIRED` | `DELIVERY_REQUIRED → COMPLETE` |
| `AWAITING_USER_INPUT` | `AWAITING_USER_INPUT → MUTATION_REQUIRED → STATIC_VALIDATION_REQUIRED → BUILD_REQUIRED → COMPLETE` |
| `DELIVERY_RECOVERY_AWAITING_USER` | `DELIVERY_RECOVERY_AWAITING_USER → COMPLETE` |
| `COMPLETE` | `COMPLETE` |

This is an existence proof under satisfiable external results, not a claim that every tool call succeeds. Failures retain one of five permitted liveness exits: exact required tool, deterministic internal command, bounded exploration, typed resumable user input, or truthful terminal control.

## 7. Capacity invariants

| Subsystem | Producer / demand | Durable or projected capacity | Refactor invariant |
|---|---:|---:|---|
| LM Studio control text | Arbitrarily large tool result | 32,000 characters | Final fallback always preserves the authoritative identity/control header. Bulky fields may be removed and `controlPayloadTruncated=true` records that fact. |
| Compactor checkpoint summary | Many durable sections | 24,000 characters total; 6,000 per ordinary section | Exact objective and exact synthesis evidence are admitted first. If exact blocks cannot fit, generation fails closed instead of silently deleting requirements/evidence. |
| Durable objective | Long user goal | 65,536 characters plus SHA-256 | Continuation/hard compaction materializes the exact objective with begin/end/hash sentinels; a 1,200-character display prefix is no longer the only model-visible copy. |
| Inspection frontier | Search/list can produce up to 1,000 rows/result | 4,096 unique paths | Retains count/hash and emits `EVIDENCE_FRONTIER_CAPACITY_EXCEEDED`/workflow stop above the bound; no silent 32-row truncation. |
| Repository inventory | Relevant project files | 4,096 files, 128-file active audit page | All accepted in-bound files stay citation eligible. Above 4,096 is explicit `REPO_AUDIT_INVENTORY_OVERFLOW`, never a false exhaustive audit. |
| Source evidence | Direct reads | 4,096 source rows | No 32-row eviction during a bounded repository audit. |
| Absent evidence | Complete negative reads/searches | 4,096 rows | Matches the frontier/inventory ownership boundary. |
| Slice plan | Planner slices | 1,024 slices | Producer and `completedSlices` checkpoint capacity match; 1,025 is rejected before partial state. |
| Per-phase evidence | `maxEvidenceCharsPerPhase` (normally 64,000) | Phase counter plus separate lifetime diagnostic | Compatible replan resets only phase counters. Lifetime evidence no longer shrinks every later read to one character. |
| Synthesis claim selection | Direct accepted evidence | At most 16 claims, 12,000 exact characters, at most 4,000/record | Required representative pairs are selected atomically and fairly. Four configured declaration/implementation pairs remain present even with long excerpts. |
| Repository source kinds | Inventory includes `.h/.hpp/.hh/.c/.cc/.cpp/.cxx/.m/.mm/.ini` | Direct evidence accepts the same relevant set | `.hh` and `.ini` advance the audit cursor rather than creating a permanent queued entry. |

## 8. Synthesis and delivery contract

Prepared synthesis binds task/session, objective hash, evidence-state hash, frontier hash, plan revision, control epoch/fingerprint, mutation generation, exact evidence-bundle hash, transaction ID, and output digest. A lost commit result replays the same transaction identity. A stale-only rejection may reuse the identical output bytes only after re-binding unchanged evidence to refreshed control.

Delivery is a separate state:

1. Exact output and digest are durable before emit intent.
2. A normal callback is followed by a server delivery ACK; only then can the task complete.
3. A restart that sees an unresolved emit intent does not automatically duplicate the answer.
4. The proxy records `delivery_uncertain` through the internal recovery tool.
5. The server returns typed `synthesis_delivery_recovery` input with two exact choices.
6. `{"action":"confirm_visible"}` completes using explicit operator attestation.
7. `{"action":"authorize_reemit","acknowledgeDuplicateRisk":true}` authorizes exactly one controlled re-emission attempt, followed by the ordinary ACK.

This is a recoverable delivery protocol, not exactly-once UI delivery. The host callback returns `void`, so the code cannot prove whether LM Studio durably rendered a fragment at the crash boundary.

## 9. Regression containment

The incident fix is tested across both oversized projections and ordinary small v2 controls. It does not relax Compactor acceptance: `authoritative=true` remains mandatory, malformed task v2 remains terminal/fail closed, and legacy v1 cannot drive task routing.

The capacity changes are paired with exact boundary tests rather than only raising constants. Repository overflow, frontier overflow, slice overflow, phase reset, extension parity, objective preservation, and four-pair evidence all have adversarial tests.

The mutation/build data plane was not redesigned. The Agent full suite still exercises journal recovery, authorization, lock/CAS behavior, rollback, static validation, build proof, Automation, and platform-specific path identity. No failed regression required weakening those gates.

## 10. Validation before packaging

| Suite | Result |
|---|---|
| Python excluding clean-tree-only integrated package/installer tests | 2,146 passed, 13 platform skips, 0 failed |
| Python state/router/task targeted after final graph split | 134 passed, 0 failed |
| Unreal Agent MCP `npm test` | 480 passed, 5 platform skips, 0 failed, 0 TODO |
| Compactor `npm test` | 313 passed, 0 failed, 0 skipped, 0 TODO |
| Python compile and `git diff --check` | passed (line-ending notices only) |

The clean-tree full Python/package/installer result, integrated artifact identity, manual installation result, Agent Mode flags, installed component hashes, and non-E2E MCP startup verification are reported in the final handoff after the source commit is sealed. They are intentionally not guessed in this pre-package report.

## 11. Release interpretation

For the stated contract—control-plane liveness, capacity honesty, installed FULL Agent Mode, and no GUI/model E2E—the code is eligible to proceed to package/install verification.

For any contract that still requires provable exactly-once LM Studio UI delivery, the verdict remains `NO-GO` until LM Studio exposes an atomic durable display receipt or idempotent fragment insertion key. The operator recovery protocol makes the limitation explicit and resumable; it does not rename uncertainty as proof.
