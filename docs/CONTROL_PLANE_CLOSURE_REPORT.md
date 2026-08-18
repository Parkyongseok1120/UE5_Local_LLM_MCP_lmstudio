# Control-plane closure report

## 1. Audited SHA and working-tree state

- Baseline audited SHA: `d78e64a2be297c7b3a0fe1ecfa3cfe5939f9ddcd` on `Develop`.
- Historical comparison SHAs: `d74bf63e0b4fbb0b317b7963df536e60e5825883` and `d16fc0519be0f217306780b466265be6ddd28cb0`.
- The d16/d74 LM Studio transcripts and server logs supplied with the correction request were treated as primary reproduction evidence.
- Runtime correction commit: `791ae0cac88fce47122646e2f7a8cbd456e1b6a5` (control-plane base correction: `bc9836be5d89278f465be8f127d8bc8dc60def34`).
- Evidence-only documentation commit: the final pushed HEAD records package/install and final-suite evidence; its exact SHA is reported in the final handoff because a commit cannot embed its own hash.
- Release status: prerelease, `installer/manifest.json → portablePackage.releaseReady=false`.

## 2. Complete subsystem/file audit ledger

[`CONTROL_PLANE_FILE_MANIFEST.json`](CONTROL_PLANE_FILE_MANIFEST.json) is the machine-readable ledger. It contains every Git-index file plus every new file staged for this correction, assigns one or more of the 17 required categories, and gives an explicit exclusion reason when detailed runtime reading is unnecessary. The final regeneration contains `1,095` paths.

The runtime control path was read in detail across these owners:

| Subsystem | Canonical implementation | Runtime role |
|---|---|---|
| Request classification and planning | `scripts/task_api.py`, `scripts/agent_orchestrator.py`, request-intent helpers | Creates the durable objective, plan, slice, and initial discovery facts. |
| Durable task state | `scripts/task_api.py`, `scripts/task_continuity.py`, `lmstudio-unreal-agent-mcp/src/task-auth.js` | CAS/lock-protected state, leases, route usage, evidence, recovery, synthesis lifecycle. |
| Canonical transition reducer | `scripts/phase_tool_router.py`, `scripts/control_state_registry.py`, `config/control_state_machine.json` | The only production owner of phase, disposition, exact required tool, allowed tools, and terminal/user-input transitions. |
| Node execution adapter | `lmstudio-unreal-agent-mcp/src/server.js`, `task-auth.js`, `task-control-transition.js` | Executes tools, records committed facts, transports bounded state to Python, returns the committed canonical envelope verbatim. |
| RAG MCP | `scripts/unreal_rag_mcp.py` | Exposes task/control/evidence/synthesis tools and dispatches to the task server. |
| LM Studio Compactor | `lmstudio-context-compactor-plugin/src/generator.ts`, `compaction-core.js`, `checkpoint-store.js` | Preserves authoritative control, serializes exact calls, binds synthesis, persists proxy checkpoints, emits UI output. |
| Evidence and repository audit | `scripts/synthesis_readiness.py`, `scripts/task_api.py`, `task-auth.js` | Durable evidence ledger, 128-file audit page, claim selection, exact final-model bundle. |
| Mutation/build data plane | mutation journal, validation, build, Automation modules | Existing atomic/CAS/rollback/proof paths were retained and rerun without weakening. |
| Packaging/identity | `scripts/control_runtime_identity.py`, `scripts/build_integrated_package.py`, component identity modules | Seals the runtime dependency closure and rejects mixed builds. |

### Control-field writer/reader ledger

| Field | Authoritative writer | Principal readers / projections |
|---|---|---|
| `controlState`, `control` | `commit_control_transition` in `phase_tool_router.py` through task-state mutation | Task API responses, Agent authorization, MCP envelopes, Compactor trusted result extraction. |
| `serverControl` | Compactor copies only trusted authoritative v2 `control` | Generator scheduler, synthesis finality, model-facing bounded projection. |
| `protocolControl` | Compactor compatibility projection | Diagnostic continuity only; task-owned payload without authoritative v2 fails closed. |
| `toolRoute`, `activeTools` | Canonical commit projects `phase` and `allowedTools` into route | Agent route authorization and budget reservations. |
| `allowedTools`, `requiredTool` | Python reducer | Agent exact authorization and Compactor exact tool serialization. |
| `requiredNextTool`, `nextAction` | Compactor mirrors canonical required tool; legacy fields are diagnostic when task v2 is absent | Tool-schema selection and host dispatch. |
| `recoveryObligation`, `buildRecovery`, `postBudgetAction` | Python committed-event reducer / task server | Canonical transition derivation, compatibility reporting, synthesis latch. |
| `synthesisReadiness`, `synthesisLatch` | `synthesis_readiness.py` plus canonical commit | Python commit validation and Compactor finality validation. |
| `synthesisEvidenceBundle` | `materialize_synthesis_evidence_bundle` | Compactor exact validator, exact prompt block, prepared transaction binding. |
| `preparedSynthesis`, `synthesisState`, `synthesisDelivery` | Compactor checkpoint transaction | Commit replay/NACK recovery, recovery artifact, delivery acknowledgement reconciliation. |
| `pendingToolCalls` | Compactor host transaction manager | Result reconciliation, same-ID replay, dispatch liveness. |
| `inspectionProgress`, `remainingFrontier` | Agent committed read/search facts normalized by Python | Evidence recovery, repo audit, readiness, bounded replan. |
| `repoAuditLedger` | Task API inventory builder; Agent marks current-page reads | Python page continuation, readiness and synthesis commit gate. |
| `sourceEvidence`, `directSourceEvidence`, `absentEvidence` | Agent result commit into durable task state | Python readiness/recovery and Compactor retained grounding. |
| `planRevision`, `controlEpoch` | Task server / canonical transition commit | Every authorization, evidence, synthesis, and proxy checkpoint identity. |
| `mutationGeneration`, `evidenceSnapshotGeneration` | Mutation checkpoint / evidence recorder | Stale-evidence rejection, static/build proof, synthesis transaction identity. |

Control flow after correction:

`user request → classification → task creation → planner → canonical Python control → tools/list → Agent authorization → tool execution → committed raw event → durable evidence/page ledger → canonical readiness/next obligation → checkpoint or compatible replan → mutation → static validation → build → Automation → synthesis claim selection → exact prompt materialization → prepared commit → ACK/NACK reconciliation → at-most-once UI emission with recovery artifact → proxy emission ACK → terminal task state`.

## 3. Previous completion-report claims

| Claim | Verdict | Evidence |
|---|---|---|
| Python is the durable semantic owner | Confirmed after correction | Node handler proposals are stripped to raw facts; reducer registry and forged-command tests prove Python chooses the executable command. |
| Authoritative v2 survives cached/repeat results | Confirmed | Agent binds the committed envelope verbatim; Compactor rejects non-authoritative task v2 and task-owned legacy routing. |
| Synthesis commit is idempotent after lost result | Confirmed | Same transaction ID is replayed; exact ACK identity and stale-control rebind tests pass. |
| Final-model grounding is bound to the committed bundle | Disproved at d78; confirmed after correction | d78 could lose excerpts during recursive projection. Version-2 exact serialization is now injected byte-for-byte and hash checked. |
| Evidence capacity is live through 32 accepted files | Disproved at d78; confirmed after correction | d78 became permanently not-ready at 17. Claim selection is now independent of durable accepted-file count. |
| Repository-wide audit is total above 4,096 files | Disproved at d78; confirmed after correction | The old overflow state stopped immediately with no next page. The new ledger has deterministic 128-file continuations. |
| UI final delivery is exactly once | Disproved and unresolved | SDK `fragmentGenerated()` returns `void` and exposes neither durable receipt nor idempotency key. The corrected guarantee is explicitly weaker. |
| Final full-suite evidence covered the final source tree | Partially confirmed at d78 | Prior reports had post-suite edits. This correction reruns all required suites after the final runtime source change. |

## 4. New P0/P1 findings

### P0

| ID | Defect and root cause | Affected files | Invariant / resolution | Test |
|---|---|---|---|---|
| P0-1 | 17–32 accepted files could never satisfy an all-accepted-paths-in-16-bundle subset check. | synthesis readiness and Compactor | Durable evidence, selected claims, exact materialized bundle, and audit coverage are separate identities. | Exact accepted-count boundaries 0/1/2/15/16/17/31/32/33. |
| P0-2 | Recursive hard-compaction projection replaced nested exact excerpts with hashes while commit retained the full bundle hash. | `compaction-core.js` | A reserved exact serialized block is injected before bounded sections; byte hash must match commit. | First/middle/last sentinel and exact serialized-string regression. |
| P0-3 | Repository inventory above 4,096 entered a terminal-looking overflow with no continuation. | `task_api.py`, `task-auth.js`, reducer | Complete inventory hash plus 128-file durable current page and hash-bound page summaries. | 0/1/16/17/32/33/4095/4096/4097 and page advance. |
| P0-4 | State-machine JSON declared events that reducer ignored and omitted events reducer handled. | state registry/reducer/Compactor | Loader rejects event/handler drift; generated proxy registry aligns lifecycle names. | Registry equality, unknown-event rejection, recovery exit tests. |
| P0-5 | Public derive API mutated facts that the Node bridge discarded. | `phase_tool_router.py`, bridge | Public derive is pure; commit/reduce APIs return and persist normalization. | Direct input-unchanged plus commit-mutates contract test. |
| P0-6 | 16MB stdout buffer made a 17MB canonical state fail. | Node/Python bridge | Payloads above 512KiB use unique, owner-private temp-file request/response transport; stdout is capped at 1MiB. | 1/8/15/16/17MB and four concurrent requests. |
| P0-7 | A UI emit crash could be loss or duplicate and the SDK gives no receipt. | generator/checkpoint store/task server | Not closed as exactly-once. Exact output is atomically preserved before intent; automatic replay stops in `uncertain`; guarantee is named `at_most_once_with_recovery_artifact`. | Injected post-fragment crash, restart no-replay, exact artifact digest; SDK contract assertion. |

### P1

| ID | Defect and root cause | Affected files | Invariant / resolution | Test |
|---|---|---|---|---|
| P1-1 | Process-local 24/5-minute guard and durable per-phase directory budget had indistinguishable recovery metadata. | list budget/server/task auth/reducer | Responses identify owner, persistence, reset rule, scope, and resume action. | root/Private/Public, 23/24/25, duplicate, cross-task isolation, durable checkpoint. |
| P1-2 | Partial output could claim full coverage without a structural gate. | readiness/Compactor/generator | Six partial-disclosure sections plus per-claim citations are required before prepare. | Valid/invalid partial report regression. |
| P1-3 | Runtime identity did not seal the newly executable registry. | Python/Node identity and package builder | Registry loader/generated proxy are in required closure and component hashes. | Cross-runtime identity/package tests. |
| P1-4 | Whole-repository review had no auditable file classification. | manifest generator and JSON ledger | Every final tracked path is categorized; non-runtime exclusions explain why they cannot write control. | Manifest generation and count validation. |

## 5. Executable canonical state model

`config/control_state_machine.json` is loaded by `scripts/control_state_registry.py`. It declares the semantic owner, adapter restrictions, lifecycle vocabulary, exact event-to-handler mapping, liveness alternatives, evidence identities, recovery exits, synthesis identities, and the two budget policies. `scripts/generate_control_state_registry.py` emits the proxy-safe CommonJS subset; `validate_control_protocol.py` fails when it is stale.

Before: JSON was descriptive, Node/Compactor names drifted, and undeclared events could silently no-op.

After: unknown canonical events raise an error; every declared event has one branch identifier; proxy lifecycle states are a checked superset; adversarial running states have exactly one liveness exit.

## 6. Event vocabulary and totality proof

Canonical committed events are exactly:

- `PHASE_BUDGET_EXHAUSTED`
- `EVIDENCE_STAGNATION`
- `GATE_VALIDATION_FAILED`
- `HANDLER_RECOVERY_FACT`
- `TOOL_RESULT_COMMITTED`

Handlers record facts. They may not persist a proposed `status`, `requiredTool.name`, disposition, or route as semantic truth. `HANDLER_RECOVERY_FACT` derives the command in Python. The adversarial corpus asserts exactly one of: exact required tool, bounded exploratory route, deterministic synthesis handoff, typed user input, or truthful terminal state.

## 7. Evidence capacity model

| Boundary | Durable accepted evidence | Selected claim evidence | Exact model materialization | Outcome |
|---|---:|---:|---:|---|
| 0 | 0 | 0 | 0 | Not ready. |
| 1 | 1 | 1 | 1 | Not ready; declaration/implementation minimum absent. |
| 2 | 2 | 2 | 2 | Ready when the representative pair and other gates match. |
| 15 | 15 | up to 15 | exact serialized records ≤12,000 chars | No all-files subset deadlock. |
| 16 | 16 | up to 16 | exact serialized records ≤12,000 chars | No deadlock. |
| 17 | 17 | claim-supporting subset ≤16 | exact selected subset | Ready under the same coverage contract; unrelated accepted evidence does not block. |
| 31/32 | 31/32 | claim-supporting subset ≤16 | exact selected subset | Same. Durable evidence hash still binds all retained evidence state. |
| 33 | synthetic boundary supported; production ledger remains bounded and audit pages carry wider coverage | ≤16 | exact selected subset | No silent full-repository claim; broader coverage belongs to the audit ledger/report contract. |

An excerpt over 4,000 characters is rejected instead of sliced. The source range must be recollected narrowly. Claim IDs are unique, every record binds path/hash/line range/exact text/digest/coverage/classification, and prepare validates every substantive bullet citation.

## 8. Repository-audit pagination model

- Full relevant inventory is deterministically sorted and represented by one `inventoryHash`.
- Current durable page is at most 128 files; completed pages retain inventory-page and coverage hashes, not full entries.
- `task_status` advances a completed page to the next continuation token.
- Synthesis commit requires final status `complete`, `remainingCount=0`, and no overflow.
- Measured serialized ledger bytes for 1/16/32/100/1,000/4,096 inventory files were 1,003 / 4,728 / 8,696 / 25,565 / 32,511 / 32,512 bytes. Growth becomes flat after the 128-file page is full.

## 9. Exact prompt materialization contract

The Python bundle contains `serializedEvidence`, its exact character count, and `bundleHash=sha256(UTF-8 serializedEvidence)`. The Compactor reconstructs the binding, rejects any field mutation, digest mismatch, duplicate claim ID, truncation, or length overflow, and places the exact serialized bytes in a reserved model-facing block. Recursive bounded control projection explicitly removes its second copy. Prepared synthesis and server commit both bind `synthesisEvidenceBundleHash`.

The proof chain is:

`direct source row → selected claim record → stable serializedEvidence → modelMaterializedSynthesisEvidenceSha256 → cited output validation → prepared synthesis identity → task_commit_synthesis exact hash comparison`.

## 10. Bridge scalability measurements

The semantic owner remains Python. State above 512KiB is transported through a unique temp directory; request and response are deleted after parsing. The adapter still uses synchronous process startup, so it blocks that Agent request's event loop; this is explicit rather than hidden.

| State | Node operation latency | Node RSS delta | External process wall time | Result |
|---:|---:|---:|---:|---|
| 1MB | 109.96ms | 4,898,816 bytes | 160.54ms | PASS |
| 8MB | 155.85ms | 28,033,024 bytes | 221.52ms | PASS |
| 15MB | 203.41ms | 49,885,184 bytes | 277.24ms | PASS |
| 16MB | 210.41ms | 52,899,840 bytes | 285.20ms | PASS |
| 17MB | 223.63ms | 56,119,296 bytes | 299.83ms | PASS |

Four concurrent 1MB requests all returned authoritative control; maximum external wall time was 175.40ms. Each semantic operation starts one Python process. A normal committed evidence result currently uses a reduce/commit operation and can use a second commit after later facts are added; the paginated audit keeps the transported task state bounded rather than copying a 4,096-entry inventory on every file transition.

## 11. Budget ownership

| Budget | Owner | Persistence/scope | Exhaustion code | Reset | Resume |
|---|---|---|---|---|---|
| Global abuse guard | Agent process | Sliding 5-minute bucket; task session when available, otherwise conversation+project | `LIST_DIRECTORY_BUDGET_EXCEEDED` / `LIST_DIRECTORY_DUPLICATE` | Window expiry or process restart | Focused `search_files` or known-file read. |
| Durable workflow budget | Python task state | Task+plan revision+phase, configured by `maxDirectoryLists` | `INSPECTION_DIRECTORY_LIST_BUDGET_EXHAUSTED` | Durable checkpoint followed by compatible replan | Exact `unreal_task_checkpoint`. |

The first budget protects the process from enumeration abuse. The second advances workflow state. Neither is fixed by merely raising a number, and task-scoped buckets prevent one task consuming another's allowance.

## 12. Recovery transition table

| Current recovery | Satisfaction event | Next status/lifecycle |
|---|---|---|
| evidence required | accepted recovery evidence | repair planning / evidence ready |
| repair planning required | gate success | repair required / mutation required |
| repair required | mutation committed | revalidate / static validation required |
| revalidate required | static validation passed | build required |
| checkpoint rebase required | checkpoint recorded | prior active state |
| phase-budget checkpoint required | checkpoint recorded | bounded replan required |
| phase-budget replan required | compatible plan revision | evidence required |
| environment recovery | discovery/tool success | prior active state; two distinct committed failures become typed user input |
| evidence complete | synthesis prepared | synthesis prepared |

Late-stage mutation/static/build/Automation transitions retain existing journal, project/slice/generation, validation, build, and model-instance fencing.

## 13. Synthesis and UI delivery guarantees

Verified synthesis guarantees:

- Direct evidence, readiness, latch, prompt bundle, output digest, control epoch/fingerprint, mutation generation, and transaction ID are exact-bound.
- Lost commit results replay the same transaction ID; semantic NACK keeps prepared bytes and can rebind unchanged output to refreshed control.
- Partial reports require structural coverage disclosure and known claim citations.
- Task server completion occurs only after the delivery-ack tool result.

UI guarantee:

- Name: `at_most_once_with_recovery_artifact`.
- Before emit intent, the exact prepared answer is atomically saved under the Compactor session directory.
- A restart that observes `emitting`/`uncertain` never automatically emits the text again.
- The server's `delivered` lifecycle means `compactor_self_attestation`; `hostReceiptObservable=false` is persisted.
- It does **not** prove that LM Studio durably displayed the message. Exactly-once is impossible with the installed SDK contract and remains the release-blocking risk.

## 14. Changed files

The correction changes the synthesis policy/reducer/task API, Compactor core/generator/store, Agent bridge/auth/server/budgets, canonical/proxy state registries, protocol/identity/package closure, versions, documentation, and their tests. Exact paths and categories are in the machine-readable file manifest; the implementation commit's `git show --stat` is the authoritative changed-file list.

Component versions are now Agent `0.3.18`, Compactor `0.4.43` revision `89`, and portable manifest `2.1.7`. Product remains `1.3.0 RC3` prerelease with `releaseReady=false`.

## 15. Exact validation results

Final CI-equivalent results are filled only after the last runtime source change and are not allowed to count skips/TODO as passes.

| Command | Exit | Passed | Failed | Skipped | TODO | Duration |
|---|---:|---:|---:|---:|---:|---:|
| Changed-boundary Python suite | 0 | 175 | 0 | 0 | 0 | 28.87s |
| Agent `npm test` pre-final check | 0 | recorded in final run | 0 | platform skips reported separately | 0 | 15.53s |
| Compactor `npm test` pre-final check | 0 | 308 | 0 | 0 | 0 | 19.15s |
| `python -m pytest --tb=short -q` | 0 | 2,214 | 0 | 13 | 0 | 228.79s runner / 229.35s wall |
| Ruff E/F/W gate | 0 | All checks passed | 0 | 0 | 0 | 0.26s |
| Protocol validator | 0 | 1 validation run | 0 | 0 | 0 | 3.04s including registry generation, TypeScript build, and diff check |
| Agent final `npm test` | 0 | 473 | 0 | 5 | 0 | 15.25s |
| Compactor final `npm test` | 0 | 308 | 0 | 0 | 0 | 22.51s wall / 18.90s test runner |
| Package/installer CI-equivalent gates | 0 | Included in the 2,214-test clean-tree suite; focused installer/package run 76 passed | 0 | 0 attributable | 0 | 74.13s focused run |
| `git diff --check` | 0 | n/a | 0 | 0 | 0 | <1s |

## 16. Package/install identities

Protocol identity after correction:

- Protocol version: `2`
- Transition-policy hash: `d448905cdd83de8a23578f817ef441940c0b6bd3a97f0b0de6f5ddf49f789d34`
- Control-schema hash: `88c2a08f80a9328d404d8997f91662b49d29c483632e72902713a7cc1a9acf9c`
- Authorization-schema hash: `02b26055411a289e57c0a2fea47acaf82f393e2b4b03ae1ccba262c1528f4430`
- Error-catalog hash: `68f06e3d9fb738e0ef6f89b2b798739d4317e9779e16c71ae564934076cbc6b9`

The clean runtime commit was packaged and installed manually before the final full-suite run:

- Package source SHA: `791ae0cac88fce47122646e2f7a8cbd456e1b6a5`.
- Package inventory: 668 files, generated index excluded, forbidden inventory count 0.
- ZIP: `%USERPROFILE%\.evidence-first\packages\Evidence-First-Integrated-791ae0c.zip`, 2,405,099 bytes.
- ZIP SHA-256: `02389ce43ba9c4fc073e2750966ac2cd8b1d372e6f247b5427d0d6125ef50953`.
- Manual install command source: the generated package's `install.py`, FULL profile, runtime bootstrap skipped, pinned npm dependencies installed, Agent authority explicitly acknowledged, RAG rebuild omitted.
- Install result: exit 0 in 30.66s; Agent SDK runtime probe `ok:true` with source `npm_ci`; active project `%USERPROFILE%\Documents\Git\Project_MJS\Project_MJS.uproject`; selected Unreal Engine version 5.8 root; MCP smoke `ok:true`; Compactor installed and pinned; restart required.
- Installed runtime manifest: `%USERPROFILE%\.lmstudio\config\control-runtime.json`, expected source commit `791ae0cac88fce47122646e2f7a8cbd456e1b6a5`.
- Installed Agent build hash: `64b5accccd566d2d9c8daf0aeea06fcadc976e7d4d00551b7feb7352238b8f8f` (`0.3.18`).
- Installed RAG build hash: `3557d92c3298a671b7832bfc864a49c63e335a271cf45cf00dbaf09e9f7b583e` (`0.3.1`).
- Installed Compactor build hash: `276d98c6dbe17452cf3f0e768b5e32891ee81ec24118e50b0e1bbc27343b5465` (`0.4.43`, installed plugin revision 89).

## 17. LM Studio E2E transcript

The user explicitly requested that physical E2E execution be omitted. The required fresh-chat flow, forced recovery, and GUI delivery observation are therefore `Not Observable` in this run. No GUI, installed-runtime, or fresh-chat success claim is made.

## 18. Remaining limitations

- Exactly-once UI delivery is unresolved because the host API exposes no durable observation receipt or idempotent insertion key. This alone prevents a GO verdict under the requested acceptance criterion.
- The Node adapter still starts a Python process synchronously for each semantic operation. Large-state overflow is fixed and audit state is bounded, but persistent RPC would further reduce latency and event-loop blocking.
- The durable source ledger is intentionally bounded; repository-wide completeness is represented by audit pages and aggregate findings, not by pretending every file body fits the final prompt.
- Windows-only and POSIX-only suite skips are reported as skips, never passes.
- Physical LM Studio behavior is not inferred from unit tests.

## 19. Final GO/NO-GO verdict

`NO-GO`

Reason: evidence/pagination/materialization/control totality defects are corrected and testable, but the mandated exactly-once UI acceptance criterion cannot be proven or implemented with the installed LM Studio SDK. The release remains a truthful prerelease with a recoverable at-most-once delivery model.
