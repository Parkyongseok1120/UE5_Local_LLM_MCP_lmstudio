# Troubleshooting

## Unreal Editor (optional)

The default `.\rag.ps1 refresh` uses `project_source` and never launches Unreal
Editor. `.\rag.ps1 refresh -RefreshScope editor_metadata` only ingests existing
exports. Add `-AllowEditorLaunch` only when starting Editor is an intended side
effect.

## Python not found

Run the root `INSTALL.bat` (Windows) or `./install.sh` (Ubuntu/macOS) instead of
invoking `install.py` directly. When no usable Python exists, the launcher
downloads pinned uv, verifies its SHA-256, installs managed Python 3.12 under the
selected user state-home, and continues without a system-wide install.

If that first bootstrap fails, keep the complete extracted package together and
check HTTPS access to GitHub Releases. On minimal Ubuntu, install
`ca-certificates curl tar coreutils`; on Windows, Windows PowerShell must be
available. `--skip-runtime-bootstrap` intentionally disables this recovery.
Only as a manual fallback, install Python 3.12 or set
`PYTHON=/path/to/python3.12` for `install.sh`. `PYTHON_EXE` is for installed MCP
runtime resolution and does not replace the launcher's initial interpreter.

## Generic API queries return only project chunks

Use the MCP call's `scope=engine` for engine-only evidence or `scope=mixed` for
engine and exact-project evidence. `scope=auto` uses the built-in classifier;
routing is not controlled by process environment variables.

If `RAG_RAW_MULTI_ENGINE_CORPUS` appears, rebuild separate engine-bound sibling
shards instead of merging incompatible engine provenance into one index. Pass an
exact `.uproject` selector so the Direct server can choose the compatible shard.
Same-name project clones must be selected by exact path; ambiguous legacy Editor
rows are never guessed and require fresh, correctly rooted metadata exports.

## Build.cs index drift after parser fix

```powershell
.\rag.ps1 set-project -ProjectFile C:\Projects\MyGame\MyGame.uproject
.\rag.ps1 refresh -RefreshScope project_source
.\rag.ps1 doctor
```

## Release / install verification

```powershell
python install.py --profile standard --yes
.\rag.ps1 doctor
```

## Agent wrote files unexpectedly

Rerun `python install.py --profile standard --yes` to restore SAFE read-only defaults.

## AGENT write is blocked by compactor/task authority

The default Direct MCP never uses context-compactor telemetry, a task, a route, a planner, or synthesis state as write authority. Select the real LLM in the model dropdown and keep the top-level `codex/unreal-context-compactor` chat-plugin switch OFF by default. For a long chat that needs bounded continuity, enable that single switch; handler invocation activates compaction. Qwen 3.8 27B is the current highly recommended, primary validated model; Muse Glimmer is under testing and is not yet a validated recommendation. Qwen 3.5, Qwen 3.6 27B, and GPT-OSS references are historical rather than current recommendations. The plugin is optional factual continuity support and does not grant or revoke `ALLOW_WRITE`, `ALLOW_COMMANDS`, or `ALLOW_UNREAL_BUILD`.

If a default chat returns `CONTEXT_COMPACTOR_NOT_ACTIVE`, `TASK_AUTH_*`, `TASK_ROUTE_*`, a required-next-tool recovery, or synthesis acknowledgement, an unsupported legacy Python entry or stale process is active. Re-run the installer, verify that `unreal-rag` launches `scripts/unreal_rag_direct.py` and `unreal-agent` launches `src/direct-server.js`, and fully restart LM Studio/MCP processes. The installer removes stale entries pointing at the removed monolithic Python server. Do not repair or invent task authorization for a Direct call. A separately named Node Strict entry returns `STRICT_SESSION_INVALID` for its own `strict_begin` lifecycle; it does not use Python task authorization.

### Multi-turn chat goes off the rails after turn 1

Check that the chat plugin is disabled, then recover context without middleware:

1. **Create a new chat.**
2. Load and select the actual LLM in the model dropdown.
3. Leave the top-level **`codex/unreal-context-compactor`** switch **OFF** in that chat's plugin panel. Turn it off manually in an existing chat that retained the old state.

`npm --prefix lmstudio-context-compactor-plugin run status` verifies installed source/build wiring only; it does not activate the plugin or inspect a chat's top-level toggle. For a long chat, enable the single top-level switch before context pressure becomes critical. If the chat already exceeds context, start a fresh chat with a short factual handoff containing the exact project, current request, observed/changed files, and remaining build/test errors.

Successful Direct RAG searches and Node reads are condensed only when the current caller echoes the opaque, state-bound `repeatReceipt` from its preceding full result; without that receipt, even an identical call in the same process returns full content. Direct RAG does not issue pagination tokens; when evidence is truncated it returns only an actionable `nextDetailLevel`. Repeated Node failures may be automatically bounded but remain `ok=false`. In every case the duplicate marker is evidence, not a required-next-tool instruction.

## Oversized Unreal log may hide the original failure

- `read_unreal_logs mode=tail` reads recent failures.
- `mode=first_error` scans from byte zero for the first actionable error within the bounded scan budget.
- `mode=range cursorByte=N` returns `nextCursorByte` and `hasMore` for deterministic traversal.

When `sourceTruncated=true`, do not claim that the returned tail contains the root cause. Build and Automation use the same bounded process owner: stdout/stderr retain bounded head and tail captures, timeout terminates the process tree, and `fullLogPath` persists that bounded projection rather than guaranteed unlimited raw output when the capture budget is exceeded.

## Direct edit/build after an interruption

Direct Mode has no task lease or checkpoint to recover. Re-establish observable state instead:

1. Confirm or pass the exact project for this call.
2. Read every target that may have changed and retain its current
   `fileVersionReceipt`; a valid raw `expectedHash` remains compatible.
3. Check `ALLOW_WRITE` / `ALLOW_UNREAL_BUILD` in `get_workspace_info`.
4. Recompute the smallest applicable patch; do not retry stale arguments.
5. Run `build_unreal_project` immediately when compile diagnosis is needed.

A successful mutation returns a new receipt; explicitly pass it to the next
edit. The server never selects the latest same-session snapshot automatically.
After a process restart, receipt expiry/eviction, a version conflict, or
uncertain external activity, re-read instead of assuming continuity.

If task lease/checkpoint errors appear, remove the unsupported legacy Python entry or stale process and reinstall the current Direct entries. Those errors are not part of the default Direct or Node Strict contract.

## File snapshot or version errors

| Error | Meaning and recovery |
|-------|----------------------|
| `FILE_VERSION_CONFLICT` | The current whole-file SHA no longer matches the resolved snapshot. Re-read the exact file, reconcile the external change, and patch the current text. |
| `FILE_SNAPSHOT_REQUIRED` | No explicit valid raw `expectedHash` or `fileVersionReceipt` was supplied. Read the file and pass its returned receipt. |
| `FILE_SNAPSHOT_INVALID` | The receipt expired, was evicted, or was not issued by this runtime. Re-read; do not reconstruct or persist opaque receipts across runtime restarts. |
| `FILE_SNAPSHOT_SCOPE_MISMATCH` | The receipt belongs to another project, path, or reliable conversation/session owner. Select and read the exact target; never transfer receipts across those scopes. |

## Node Strict session is unfinished or orphaned

Node Strict is the sole supported Strict surface. Start it with `strict_begin` and use that returned session only for its conversation. It has no Python peer, cross-server authorization, or automatic pairing; removed Python `taskAuthorization`/planner artifacts cannot authorize Node mutations.

MCP transport cannot observe the model's final-answer delivery. The model must explicitly call `strict_complete` immediately before its final answer (`strict_fail` or `strict_cancel` for those outcomes). If a connection/process ends, its TTL expires, or its process restarts before completion, the session becomes nonblocking `orphaned` state. Begin a new session, or call `strict_resume` only after explicit user approval. An orphan never blocks Direct calls, another conversation, or another project.

## Mutation semantic advisory reports problems

Direct writes may return bounded `semanticAdvisory` findings from the local Unreal
API denylist. They are informational and never close or open a write/build gate.
Review the finding, read the affected source, and let the model or user decide
whether to revise, validate further, or build. Exact path scope, receipt-first
snapshot/CAS (with compatible raw `expectedHash`), atomic commit/rollback,
mutation-size limits, and delete approval remain hard safety boundaries.

## Build target or process-output confusion

Use `target=Editor` as the portable alias for the selected project's canonical,
configured preferred, or sole discovered custom Editor target. Explicit
non-Editor targets are passed through unchanged. If target discovery remains
ambiguous, pass the exact discovered target instead of guessing. Inspect process capture metadata before treating
`fullLogPath` as complete; omitted-byte counts mean the log contains bounded
head/tail evidence only.

## Runtime debugging in Direct Mode

The default catalog does not expose the legacy `unreal_runtime_debug_session` lifecycle. Use direct logs, bounded source reads, builds, and declared Automation tests to gather evidence. Advisory RAG/runtime-config validators can help analyze that evidence but do not authorize or block a patch. Do not claim a runtime fix from textual plausibility alone; report which build, test, trace, crash, or timeout evidence was actually observed.

The old prepare/experiment/candidate-comparison gate belongs to the unsupported historical Python controller and is not exposed by any supported server. It is not a Node `strict_begin` feature.
