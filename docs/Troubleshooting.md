# Troubleshooting

## Unreal Editor (optional)

The default `.\rag.ps1 refresh` uses `project_source` and never launches Unreal
Editor. `.\rag.ps1 refresh -RefreshScope editor_metadata` only ingests existing
exports. Add `-AllowEditorLaunch` only when starting Editor is an intended side
effect.

## Python not found

Install Python 3.10+, set `PYTHON_EXE`, or run the integrated installer so the
portable launcher can resolve its managed Python runtime.

## Generic API queries return only project chunks

Use the MCP call's `scope=engine` for engine-only evidence or `scope=mixed` for
engine and exact-project evidence. `scope=auto` uses the built-in classifier;
routing is not controlled by process environment variables.

## Build.cs index drift after parser fix

```powershell
.\rag.ps1 collect-symbols -Root C:\Projects\MyGame\Source -Tier public -SymbolScope project -ProjectName MyGame
.\rag.ps1 build-incremental
```

## Release / install verification

```powershell
python install.py --profile standard --yes
.\rag.ps1 doctor
```

## Agent wrote files unexpectedly

Rerun `python install.py --profile standard --yes` to restore SAFE read-only defaults.

## AGENT write is blocked by compactor/task authority

The default Direct MCP never uses context-compactor telemetry, a task, a route, a planner, or synthesis state as write authority. Select Qwen/GPT (the real LLM) in the model dropdown and enable `codex/unreal-context-compactor` separately in the chat plugin panel. The plugin is optional continuity support and does not grant or revoke `ALLOW_WRITE`, `ALLOW_COMMANDS`, or `ALLOW_UNREAL_BUILD`.

If a default chat returns `CONTEXT_COMPACTOR_NOT_ACTIVE`, `TASK_AUTH_*`, `TASK_ROUTE_*`, a required-next-tool recovery, or synthesis acknowledgement, an unsupported legacy Python entry or stale process is active. Re-run the installer, verify that `unreal-rag` launches `scripts/unreal_rag_direct.py` and `unreal-agent` launches `src/direct-server.js`, and fully restart LM Studio/MCP processes. The installer removes stale entries pointing at the removed monolithic Python server. Do not repair or invent task authorization for a Direct call. A separately named Node Strict entry returns `STRICT_SESSION_INVALID` for its own `strict_begin` lifecycle; it does not use Python task authorization.

### Multi-turn chat goes off the rails after turn 1

Check chat-level plugin activation and context pressure:

1. **Create a new chat.**
2. Load and select the actual LLM, such as Qwen, in the model dropdown.
3. Enable **`codex/unreal-context-compactor`** in that chat's plugin panel.
4. Keep the actual LLM selected. The compactor runs in the prediction loop and passes tools through unchanged.

`npm --prefix lmstudio-context-compactor-plugin run status` verifies installed source/build wiring only; it cannot prove that the plugin is enabled for a particular chat. Confirm the toggle in LM Studio. If the chat still exceeds context, start a fresh chat with a short factual handoff containing the exact project, current request, observed/changed files, and remaining build/test errors.

Successful Direct RAG searches and Node reads are condensed only when the current caller echoes the opaque, state-bound `repeatReceipt` from its preceding full result; without that receipt, even an identical call in the same process returns full content. Direct RAG does not issue pagination tokens; when evidence is truncated it returns only an actionable `nextDetailLevel`. Repeated Node failures may be automatically bounded but remain `ok=false`. In every case the duplicate marker is evidence, not a required-next-tool instruction.

## Oversized Unreal log may hide the original failure

- `read_unreal_logs mode=tail` reads recent failures.
- `mode=first_error` scans from byte zero for the first actionable error within the bounded scan budget.
- `mode=range cursorByte=N` returns `nextCursorByte` and `hasMore` for deterministic traversal.

When `sourceTruncated=true`, do not claim that the returned tail contains the root cause.

## Direct edit/build after an interruption

Direct Mode has no task lease or checkpoint to recover. Re-establish observable state instead:

1. Confirm or pass the exact project for this call.
2. Read every target that may have changed and use its current SHA-256.
3. Check `ALLOW_WRITE` / `ALLOW_UNREAL_BUILD` in `get_workspace_info`.
4. Recompute the smallest applicable patch; do not retry stale arguments.
5. Run `build_unreal_project` immediately when compile diagnosis is needed.

If task lease/checkpoint errors appear, remove the unsupported legacy Python entry or stale process and reinstall the current Direct entries. Those errors are not part of the default Direct or Node Strict contract.

## Node Strict session is unfinished or orphaned

Node Strict is the sole supported Strict surface. Start it with `strict_begin` and use that returned session only for its conversation. It has no Python peer, cross-server authorization, or automatic pairing; removed Python `taskAuthorization`/planner artifacts cannot authorize Node mutations.

MCP transport cannot observe the model's final-answer delivery. The model must explicitly call `strict_complete` immediately before its final answer (`strict_fail` or `strict_cancel` for those outcomes). If a connection/process ends, its TTL expires, or its process restarts before completion, the session becomes nonblocking `orphaned` state. Begin a new session, or call `strict_resume` only after explicit user approval. An orphan never blocks Direct calls, another conversation, or another project.

## Mutation semantic advisory reports problems

Direct writes may return bounded `semanticAdvisory` findings from the local Unreal
API denylist. They are informational and never close or open a write/build gate.
Review the finding, read the affected source, and let the model or user decide
whether to revise, validate further, or build. Exact path scope, SHA-256 compare-
and-swap, atomic commit/rollback, mutation-size limits, and delete approval remain
hard safety boundaries.

## Runtime debugging in Direct Mode

The default catalog does not expose the legacy `unreal_runtime_debug_session` lifecycle. Use direct logs, bounded source reads, builds, and declared Automation tests to gather evidence. Advisory RAG/runtime-config validators can help analyze that evidence but do not authorize or block a patch. Do not claim a runtime fix from textual plausibility alone; report which build, test, trace, crash, or timeout evidence was actually observed.

The old prepare/experiment/candidate-comparison gate belongs to the unsupported historical Python controller and is not exposed by any supported server. It is not a Node `strict_begin` feature.
