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

## Long-running continuity

`unreal_task_start` creates a renewable task lease. With control-plane tools enabled, use `unreal_task_checkpoint`:

- `heartbeat` renews ownership of the current plan/slice.
- `record` stores completed/pending slices, the next action, validation notes, and SHA-256 snapshots of project-contained modified files.
- `recover` compares current files with the last checkpoint.
- `rebase` requires `acceptCurrentFiles=true`, advances the lease epoch, refreshes snapshots, and invalidates prior pre-write gates.

An expired lease or checkpoint conflict blocks writes in both the Python task phase and LM Studio's Node mutation authorization. Legacy tasks without continuity state remain readable and compatible.

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
