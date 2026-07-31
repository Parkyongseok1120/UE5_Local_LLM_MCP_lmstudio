# Agent Orchestrator

Small planner module, not a heavy framework. Its job is to make compact 9-20B models follow a stable order before they touch files.

## Flow

1. **classify** request -> `TaskKind`
2. **build evidence plan** -> RAG modes, gates, writes_allowed
3. **choose edit strategy** -> patch / no_edit / new_file
4. **orchestration route** -> risk tier, active model profile, direct/guarded/architecture-first strategy
5. **tool policy** -> ordered tools from `config/tool_orchestration.json`
6. **write gate** -> whether writes are allowed, max edit count, read-before-write/build requirements
7. **checkpoints** -> conditions the model must satisfy before moving to the next tool
8. **stop/retry policy** -> when to stop, and how to retry compile failures

## CLI

```powershell
.\rag.ps1 agent-plan -Question "Fix missing generated.h in MyComponent" -Mode compile_fix
```

## MCP

`unreal_agent_plan` returns read-only JSON with:

- `taskKind`
- `evidencePlan`
- `editStrategy`
- `toolPolicy`
- `orchestration`
- `writeGate`
- `checkpoints`
- `stopConditions`
- `retryPolicy`

LM Studio chat should call it first after `unreal_get_active_project`.

The orchestration route controls reasoning/tool phases for the model currently loaded in LM Studio. It does not claim that multiple models are loaded or switch models by itself. Multi-file, subsystem, replication, and architecture work escalates to `architecture_first`; bounded edits use `guarded`.

Refactor tasks always add `unreal_semantic_refactor_guard` to `requiredBeforeWrite`, including when a caller supplies a custom plan that omits it. The guard compares the current project with an isolated candidate and requires exact changed-file/diff identity, paired invariant observers, static/build proof, and runtime proof for runtime-sensitive invariants. Non-refactor task routing is unchanged.

Ambiguous write requests also add `unreal_feature_intent_resolve` to `requiredBeforeWrite`. The planner exposes only compact candidate summaries. The resolver recomputes candidate scores, requires at least two complete candidates, rejects unresolved ties, and records `selectedIntentId`, `intentContractHash`, acceptance-oracle hash, plan revision, checkpoint binding, and exact target snapshots in the task-state SSOT. High ambiguity remains plan-only until explicit approval; low-ambiguity reversible changes with a bounded target do not pay this gate.

For high ambiguity, MCP only creates a pending approval challenge. It cannot approve that challenge, and a `userApproved` argument has no authority. The user must run:

```powershell
python scripts/approve_feature_intent.py --workspace <root> --task-session-id <id> --intent-contract-hash <hash>
```

The approval is bound to that task, plan revision, contract hash, and expiry, and is consumed once by the next matching resolver call.

## Long-running continuity

`unreal_task_start` creates a renewable task lease. With control-plane tools enabled, use `unreal_task_checkpoint`:

- `heartbeat` renews ownership of the current plan/slice.
- `record` stores completed/pending slices, the next action, validation notes, and SHA-256 snapshots of project-contained modified files.
- `recover` compares current files with the last checkpoint.
- `rebase` requires `acceptCurrentFiles=true`, advances the lease epoch, refreshes snapshots, and invalidates prior pre-write gates.

An expired lease or checkpoint conflict blocks writes in both the Python task phase and LM Studio's Node mutation authorization. Legacy tasks without continuity state remain readable and compatible.

`record` also resets the phase tool-call budget, including when the phase is unchanged. Budgeted phase tools remain separate from the always-discoverable `status`, `checkpoint`, and `cancel` control surface. Gate evidence with a TTL transitions to its precomputed fallback route at expiry; both Python tool listing/authorization and the LM Studio route watcher observe the change without a server restart.

Each phase exposes 5-10 budgeted work tools. The three recovery controls (`unreal_task_status`, `unreal_task_checkpoint`, and `unreal_task_cancel`) are a separate, non-budgeted surface. `unreal_agent_plan` is also a bounded replan surface: while one healthy task owns the active project, it updates that same `taskSessionId` atomically instead of creating another running task. Replan increments `planRevision`, rotates `authToken`, and invalidates prior gates, selections, checkpoint proof, and phase usage, so prior authorization becomes stale immediately.

Only one replan is allowed per monotonically increasing `checkpointGeneration`. A second attempt returns `REPLAN_BUDGET_EXHAUSTED` with `checkpointRecordRequired=true`; an explicit `unreal_task_checkpoint` `record` action opens the next replan window. Autonomy-supervisor blockers may use this bounded path to advance one strategy epoch while preserving retry counters, budgets, and history. Lease expiry, checkpoint conflicts, and ambiguous ownership do not expose or authorize replan.

## Runtime causal workflow

`unreal_runtime_debug_session` is fail-closed:

1. `prepare` ranks falsifiable hypotheses and selects the highest evidence/benefit candidate.
2. `record_experiment` must use the same reproduction fingerprint and observer. A falsified hypothesis selects the next ranked hypothesis.
3. `compare_patch_candidates` requires two to four distinct candidates and isolated static/build/invariant evidence.
4. `record_patch` must match the selected candidate and changed-file set.
5. `verify` evaluates the same observer plus configured sample, duration, error, crash, timeout, and trace requirements.

`scripts/runtime_experiment_runner.py` builds argv-safe Unreal Automation/trace/soak plans. `scripts/patch_candidate_sandbox.py` applies unified diffs only to project copies outside the project root. Neither script upgrades a result to `RuntimeVerified` unless its required executions and artifacts are present.

## Wrapper

Enabled by default via `UNREAL_AGENT_ORCHESTRATE=1`. Disable with `--no-orchestrate` or env `UNREAL_AGENT_ORCHESTRATE=0`.

Plan is written to `agent_plan.json` in the wrapper run dir and prepended to prompts.

## Edit Verification

`verify_edit_allowed(plan, files_count, patches_count)` blocks:

- writes on inspect-only, answer-only, code-sketch, and runtime-debug tasks
- duplicate `.h`/`.cpp` creation without prior `search_files` (checkpoint guidance; extended mode: finish edits → `propose_file_deletions` → explicit user approval → `delete_file` with matching `approvalToken`)
- edits when `editStrategy=no_edit`
- bundles larger than `writeGate.maxFilesPerEdit`

The wrapper also rejects invalid JSON, no-op edits, unsupported paths, static validation failures, and failed UBT loops.
