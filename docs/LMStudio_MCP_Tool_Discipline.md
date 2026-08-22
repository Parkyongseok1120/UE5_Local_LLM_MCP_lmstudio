# LM Studio MCP Tool Discipline

Guide for **LM Studio chat** with the supported `unreal-rag` + `unreal-agent` MCP surfaces.

## Supported paths

| Path | Workflow owner | Enforcement |
|------|----------------|-------------|
| LM Studio chat, default MCP entries | Selected LLM | Direct capability schemas plus filesystem, process, concurrency, deletion, and authority safety |
| Separately named Node Strict entry | Selected LLM within one Strict session | Conversation-scoped `strict_begin` lifecycle around mutation/long-running capabilities |

The default chat path is **Direct Model Mode**. `unreal-rag` and `unreal-agent` do not import or reconstruct the legacy task controller. The model chooses which capability to call and whether another diagnostic is useful.

## Stable Direct catalog

`tools/list` is stable for the life of a Direct MCP process. It is not reduced by task state, route phase, `MCP_ESSENTIAL_TOOLS`, `MCP_EXTENDED_TOOLS`, a previous failure, or another chat. Direct schemas omit `taskAuthorization` and lifecycle tools such as `unreal_agent_plan` and `unreal_task_*`.

`unreal-rag` exposes exactly eight factual RAG capabilities in `config/stable_tool_manifest.json`: active-project get/set, search, symbol lookup, health, rebuild status, synchronous refresh, and capability inventory. It does not expose graph planning, task state, route steering, architecture gates, code-sketch gates, or model-driving compile loops. `unreal-agent` exposes its 20 project discovery, bounded file/log read, conflict-safe edit, deletion approval, advisory static validation, build, Automation, and allowlisted-command capabilities. The manifest and live `tools/list` are the authoritative counts.

Call-time boundaries in Direct mode are concrete safety checks:

- `ALLOW_WRITE`, `ALLOW_COMMANDS`, and `ALLOW_UNREAL_BUILD` control mutation/process authority.
- Project containment and exact per-call project resolution prevent cross-project writes.
- Existing-file edits require current version evidence. Prefer the opaque `fileVersionReceipt` returned by a read or the immediately preceding mutation; a valid raw `expectedHash` remains compatible, and a reliable same-session latest snapshot may be resolved automatically. External changes fail with `FILE_VERSION_CONFLICT`; missing, invalid, or scope-mismatched snapshot evidence fails closed with `FILE_SNAPSHOT_*` errors.
- `write_file` is create-only; `replace_in_file` patches an existing file. A deletion proposal returns both its approval token and `fileVersionReceipt`; deletion accepts that receipt or a compatible raw hash plus explicit user approval and `ALLOW_SOURCE_DELETE=1`.
- Build and Automation resolve the selected `.uproject` and matching installed engine/version before execution. For builds, the portable `Editor` target alias resolves the selected project's canonical `<ProjectName>Editor`, configured preferred Editor target, or sole discovered custom `*Editor` target; an explicit non-Editor target is unchanged.
- Exact project selection also routes RAG to the compatible engine-bound index shard. A single RAG call does not merge projects that belong to different engine shards.

No task lease, route owner, planner gate, required next tool, or synthesis acknowledgement participates in those checks.

## LM Studio context plugin

Select the actual LLM in the model dropdown. Qwen 3.8 27B is the current validated recommendation; Muse Glimmer is under testing and is not yet a validated recommendation. Keep the top-level `codex/unreal-context-compactor` switch OFF in the chat's plugin panel, and keep its nested `Enable transparent compaction` switch OFF. Installation/pinning is availability, not activation. The compactor is an optional prediction-loop chat plugin, not a proxy model, MCP authority source, tool filter, or `targetModel` selector.

The optional path may replace older model-facing history with deterministic factual continuity state while retaining the latest real user request and recent complete turns. That bounded state can include the active objective, a continuation antecedent for elliptical follow-ups, active project, current work status, unresolved items, and relevant file/tool/build facts such as `fileVersionReceipt`. It deliberately strips task/route/control/synthesis internals and required-tool directives and never becomes workflow authority. Installation and `npm run status` verify availability and source/build wiring only. Existing chats can retain an old top-level opt-in, so turn that switch off manually.

## Direct duplicate behavior

After updating the MCP or plugin runtime, fully restart the affected MCP process or LM Studio so the new source is actually loaded.

A successful Direct RAG search or Node read is concise only when the caller echoes the opaque `repeatReceipt` from its own preceding full result and the query plus observable state still match. Without that receipt, identical success calls return their full evidence even inside one MCP process. A repeated failed Node call may still return a bounded failed duplicate.

```json
{"ok":true,"duplicate":true,"status":"no_new_information"}
```

For a receipt-confirmed successful observation, this is a concise successful result, not a forced recovery or an instruction to call another tool. A repeated failure remains `ok=false` with a short non-retryable duplicate diagnostic. Reuse evidence only when it exists in the current chat; otherwise omit the receipt and the server returns the full result. Direct RAG does not advertise a fake pagination cursor: a truncated result exposes only its actionable `nextDetailLevel`. Direct responses expose at most one canonical retry object and one optional advisory suggestion; they do not expose required-next-tool chains.

## Node Strict

The sole supported Strict implementation is the optional, separately named Node entry `unreal-agent-strict`, which launches `src/strict-server.js`. It owns a small conversation-scoped lifecycle beginning with `strict_begin`; reads/searches remain task-free while mutations and long-running calls require the live session.

There is no supported Python Strict entry and no cross-server pairing protocol. The old Python task/route/planner controller is an unsupported repository-local historical artifact and is omitted from the portable package. Do not configure it through `MCP_EXECUTION_MODE` or treat its task authorization as current MCP behavior.

Node MCP transport cannot detect delivery of the model's final answer. The model must call `strict_complete` immediately before that final answer, or `strict_fail` / `strict_cancel` for those outcomes. Connection/process shutdown, TTL expiry, and process restart leave unfinished Node sessions `orphaned`; they never block Direct Mode, another conversation, or another project. Resuming an orphan requires `strict_resume` with explicit user approval.

A Direct caller must not invent or request the Node Strict protocol.

The model-driving Python workflow tools—`unreal_start_compile_loop`, `unreal_compile_loop_status`, `unreal_cancel_compile_loop`, and deprecated alias `unreal_generate_compile_loop`—are not part of any supported MCP catalog or portable runtime. Direct uses `build_unreal_project` for an immediate UBT/UHT diagnostic while the selected chat model remains in charge.

## Logic review (false-bug guard)

When reviewing gameplay/cinematic logic (not compile errors):

1. Read the sibling `.h` UENUM / field comments **before** calling `read_symbol` / concluding a bug from `.cpp` alone.
2. Label every finding `Bug` | `ByDesign` | `Ambiguous` | `NeedsRuntimeProof`.
3. Intentional early returns that match header contracts (e.g. AuthoredWorld = keep asset transform) are **ByDesign**, not "missing logic".
4. Cite the header/implementation evidence directly. If a separately configured evidence-first capability is available, it may be used as an optional audit aid, never as a Direct answer or build gate.

## Validate-on-write

`VALIDATE_ON_WRITE` is a legacy server setting. The default Direct `write_file`, `replace_in_file`, and `apply_edit_bundle` paths do not run project-wide static validation and do not require a validator certificate. They enforce their own hard mutation safety: project containment, target/create rules, size limits, scoped read/mutation snapshots, atomic/CAS writes, locks, and deletion approval. Narrow semantic denylist findings are advisory evidence only; a finding or unavailable analyzer never authorizes or blocks the mutation.

`static_validate_project` is a separate **advisory** capability. It returns `validationOk`, findings, and scan metadata, but also reports `advisory=true` and `blocksBuild=false`. A model may run it before or after an edit, skip it, fix a relevant finding, or proceed directly to UBT/UHT. Its findings never authorize a write, roll back an edit, or close the build tool.

`build_unreal_project` is an immediate diagnostic/execution capability. When `ALLOW_UNREAL_BUILD=1` and project/engine resolution succeeds, it runs without consulting a task, plan, code-sketch result, `VALIDATE_ON_WRITE`, or `static_validate_project`. Passing `target=Editor` selects the resolved project's canonical, configured preferred, or sole discovered custom Editor target; any explicit non-Editor target is preserved. Build output is the authoritative compile result; a static scan is useful supplementary evidence, not a prerequisite.

## Write Safety and Flow

### Direct write boundaries

| Boundary | Example | Effect |
|----------|---------|--------|
| Authority | `ALLOW_WRITE=0` | Mutation is rejected before disk access |
| Project/target safety | path escapes project, existing target passed to `write_file`, protected path | Mutation is rejected |
| Concurrency | missing usable version evidence, stale receipt/hash, scope-mismatched receipt, occurrence mismatch, path lock | Mutation is rejected with `FILE_SNAPSHOT_REQUIRED`, `FILE_SNAPSHOT_INVALID`, `FILE_SNAPSHOT_SCOPE_MISMATCH`, `FILE_VERSION_CONFLICT`, or the applicable match/lock error; re-read when current evidence is unavailable or stale |
| Prospective semantic denylist advisory | known unsafe Unreal API pattern or unavailable analyzer | Successful mutation reports a bounded non-blocking warning; build remains separately callable |
| Advisory project scan | `static_validate_project` finding | No automatic rollback and no build block |

**Advisory ≠ write permission:** validator warnings cannot bypass authority, containment, snapshot/CAS, target, or delete-approval checks.

### Generation self-check (non-blocking)

`unreal_code_sketch_claim_validate` and `unreal_architecture_reasoning` are not part of the supported MCP surface. Their repository-local implementations belong to the unsupported historical Python controller and are omitted from portable runtime packaging. Direct can inspect the same source with factual RAG and project reads, then use immediate build/test diagnostics without importing either gate.

`write_file` is **create-only**. It creates brand-new files and refuses to overwrite any file that already exists (every extension, not just source). Direct Mode has no existing-file override for this tool; use `replace_in_file` with the current `fileVersionReceipt` (preferred) or a compatible raw `expectedHash`.

- If `write_file` returns `blocked because file already exists`: switch to `replace_in_file`. **Do not retry `write_file`** on that path.
- On a tool timeout (`MCP error -32001`): never immediately retry the same write. First verify state with `list_directory` / `read_file`. If the file now exists, switch to `replace_in_file`; if the situation is unclear, stop and summarize for the user. A timeout is a hard-stop signal.
- After a successful `write_file` / `replace_in_file`: report the changed file briefly, then choose the next useful diagnostic or edit. There is no server-required next step.
- Pause for user direction on genuine risk or scope ambiguity: an uncertain timeout state, rollback failure, conflicting external edit, destructive approval, or a requested scope change. An advisory static finding alone is not a server stop signal.
- If a write response says `rollback skipped ... (conflict)`: another operation changed the file. Stop, `read_file` the current content, and reconcile before editing again.
- **Direct repetition:** successful Direct RAG searches and Node reads are condensed only when the caller echoes the state-bound `repeatReceipt` from its prior full result. Without it, identical calls return full content. Direct RAG has no pagination token; use `nextDetailLevel` only when returned. Repeated Node failures may be automatically bounded but remain failures. Do not interpret any duplicate status as a recovery gate. For consecutive edits, use the `fileVersionReceipt` returned by the immediately preceding mutation, or let a reliable same-session snapshot resolve it automatically. Re-read after `FILE_VERSION_CONFLICT`, after snapshot expiry/restart, or whenever external state is uncertain.

## Forbidden Tools

Do not use LM Studio's JavaScript/code sandbox for Unreal project file work:

- `run_javascript`
- `lmstudio/js-code-sandbox`
- `Deno.readTextFile` / `Deno.writeTextFile`
- Node `fs` / CommonJS `require`

That sandbox has its own working directory and is not rooted at the active `.uproject`. Project file I/O must go through `unreal-agent`: `read_file_range`, `read_file`, `replace_in_file`, and `write_file` only for brand-new files.

Remove these broad patterns from `%USERPROFILE%\.lmstudio\settings.json` `chat.skipToolConfirmationPatterns`:

```json
"lmstudio/js-code-sandbox:run_javascript",
"lmstudio/js-code-sandbox:*",
"mcp/unreal-agent:*",
"mcp/unreal-rag:*"
```

The MCP wildcards would also suppress the host confirmation needed for destructive deletion and explicitly authorized Editor launch. Re-running the supported installer removes all four patterns while preserving unrelated settings. Restart LM Studio after changing that setting. If the sandbox plugin is still shown to the model, hide or disable the JavaScript/TypeScript Code Sandbox plugin in LM Studio for Unreal coding chats.

## Practical Direct Flow

Direct Mode has no required chat order. A common implementation flow is:

1. Select or pass the exact `.uproject`/project name for the current call.
2. Search or inspect only the evidence relevant to the request.
3. Read each exact target and retain its `fileVersionReceipt`; retain the SHA-256 only when a raw `expectedHash` is needed for compatibility.
4. Patch existing files with `replace_in_file`, passing the receipt when the transport cannot supply a reliable session, or create new files with `write_file`.
5. Optionally run `static_validate_project` for supplementary findings.
6. Run `build_unreal_project` or Automation whenever the user needs compile/runtime evidence.

The model may omit irrelevant steps, revisit a file after changed evidence, or call a build immediately for a build-diagnosis request. A tool suggestion is advisory. No `toolPolicy`, `writeGate`, checkpoint, or stop condition owns the sequence.

For edit tasks:

- Do not write when `ALLOW_WRITE=0`, the user requested analysis only, or the target/scope is not established.
- Read every existing target before its first `replace_in_file` and retain the returned receipt. A successful mutation issues a new receipt for a consecutive edit; re-read on a version conflict or when external state may have changed. For a new file, inspect the containing module/directory and confirm the path does not already exist before `write_file`.
- Prefer `replace_in_file` with `expectedOccurrences=1` for existing files.
- Use `write_file` only for brand-new files. Existing `.h`, `.hpp`, `.cpp`, `.c`, `.cc`, `.cxx`, and `.cs` files are patch-only.
- Run `build_unreal_project` after C++ or `Build.cs` edits when compile proof is part of the requested outcome; it does not require a prior static validator.
- If cleanup appears to require deleting files, finish all edits first, call `propose_file_deletions`, retain each proposal's `fileVersionReceipt`, report the count/path/file name/reason/if-not-deleted impact/if-deleted impact, and wait for explicit user approval plus the LM Studio tool-confirmation prompt before `delete_file`. Keep `ALLOW_SOURCE_DELETE=0` unless deletion is deliberately enabled for that session.
- On UBT failure, inspect the bounded current error context, read the implicated source, and patch the smallest failing surface.

## Diagram Output

When the user asks for a diagram, or when explaining structure, dependencies, ownership, Blueprint or Material graph flow, shader pipeline, or runtime call order, show Mermaid first. Put ASCII/text only after the Mermaid block as a fallback for clients that do not render Mermaid.

## Session Bootstrap

No MCP task bootstrap is required. Confirm the exact project and safety flags only when they are relevant. Historical workflow bootstrap prompts are not part of the portable Direct product.

## Context Budget and Session Handoff

Build and Automation share one bounded process owner. It keeps bounded head and tail captures for stdout and stderr, records omitted-byte metadata, terminates the process tree on timeout, and persists only that bounded projection when the process exceeds the capture budget. `build_unreal_project` is compact by default: it returns a one-line `summary`, up to 40 likely error lines, and the timestamped bounded process log path under `.agent/logs` as `fullLogPath`; `verboseOutput=true` returns the bounded captured streams, not guaranteed unbounded raw output. `read_unreal_logs` defaults to a bounded tail; use `mode=first_error` to scan from byte zero and `mode=range` with `cursorByte`/`nextCursorByte` when exact traversal is needed. Always inspect `truncated`/`hasMore` and process capture metadata.

Direct responses are bounded and remain valid JSON; errors use a much smaller ceiling than successful evidence payloads. An oversized success is returned as retryable `OUTPUT_LIMIT_EXCEEDED` without partial evidence or a cursor; narrow the byte/line/detail/result arguments before retrying.

For long LM Studio chats, keep the actual LLM selected and leave `codex/unreal-context-compactor` OFF by default. Prefer a suitable context length or a fresh chat with a concise factual handoff. Compaction is available only as a deliberate two-switch, per-chat experiment and never preserves or creates task routes, required tool commands, or workflow authority.

If context/KV cache still saturates, start a fresh chat with a short factual handoff containing the exact project, current request, files already changed/observed, latest build/test result, open errors, and failed approaches. Direct Mode does not require a `write_session_handoff` artifact or a resume checkpoint.

## Model and System Prompt

| Profile / model | System prompt |
|-----------------|---------------|
| All default Direct profiles | [`lmstudio_direct_model_system.md`](../prompts/lmstudio_direct_model_system.md) |

Historical model-specific planner prompts are not shipped. Direct profiles use [`prompts/lmstudio_direct_model_system.md`](../prompts/lmstudio_direct_model_system.md), which leaves reasoning, tool choice, stopping, and the final answer with the model selected in LM Studio.

## Model Selection Notes

- **Qwen 3.8 27B** is the primary currently recommended and validated model for the Direct runtime.
- **Muse Glimmer** is under testing only. Do not present it as a validated recommendation until current Direct-mode evidence is published.
- Qwen 3.5, Qwen 3.6 27B, and GPT-OSS references in saved scorecards are historical measurements, not current recommendations.
- For `module_fix` / `GameplayTags` / `Build.cs` errors, read the full current `*.Build.cs` before patching and report the actual build evidence.

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| Model answers without requested tool evidence | Ask it to call the relevant Direct capability; verify the default `unreal-rag` / `unreal-agent` entries are enabled |
| Wrong paths (`Documents` vs project) | Pass the exact `.uproject`/project name on the call, or inspect/set the active project |
| Write unexpectedly allowed on analysis-only request | Disable AGENT authority (`ALLOW_WRITE=0`) or restate the no-write scope; Direct has no planner write gate |
| `CONTEXT_COMPACTOR_NOT_ACTIVE` / compactor authority error | A legacy Strict server/config is active. Default Direct never gates MCP authority on compactor telemetry. Keep the chat plugin OFF, restore the Direct MCP entries with the installer, and restart the stale processes |
| `TASK_AUTH_*`, `TASK_ROUTE_*`, `requiredNextTool`, or synthesis recovery | An unsupported legacy Python process or stale config is active. Re-run the installer, verify `unreal-rag` launches `scripts/unreal_rag_direct.py`, and restart; do not invent authorization |
| `STRICT_SESSION_INVALID` | A Node Strict session is absent, waiting, terminal, orphaned, owned by another connection, or bound to different arguments. Inspect `strict_status`; begin a new session or explicitly approve `strict_resume` for an orphan |
| Model calls `run_javascript` / `js-code-sandbox` | Remove sandbox auto-approval, hide/disable that plugin, and restate that project I/O must use Direct `unreal-agent` tools |
| Hallucinated analysis | Force `read_file` before claims or edits |
| False logic bugs (early return = "missing") | Read sibling `.h` UENUM/field docs first, classify `ByDesign`/`Ambiguous`, and cite the actual contract; no claim-validator gate is required |
| `status=no_new_information` | The caller echoed a successful Direct RAG/Node read's still-valid `repeatReceipt`, or a Node failure repeated. Reuse evidence only when it exists in this chat; otherwise omit the receipt for a full result. No recovery tool is required |
| `FILE_VERSION_CONFLICT` or repeated/no-op patch | Another writer changed the file or the exact text no longer applies. Re-read, retain the new receipt, and patch only text that still exists with the exact occurrence count. |
| `FILE_SNAPSHOT_REQUIRED`, `FILE_SNAPSHOT_INVALID`, or `FILE_SNAPSHOT_SCOPE_MISMATCH` | Read the exact file in the exact selected project and pass its new `fileVersionReceipt`. Do not transfer receipts across project, path, conversation/session owner, process restart, or expiry; a valid raw `expectedHash` remains compatible. |
| Tool not in list | Confirm the Direct process is current. Removed task/planner/synthesis and Python compile-loop tools are not supported; Node lifecycle tools exist only in Node Strict |
| `unreal_rag_refresh` times out | Re-run the same supported `python install.py --profile ... --yes` command used for this installation so the managed MCP entries and timeouts are regenerated, then restart LM Studio. Refresh is synchronous and defaults to `scope=project_source`, which never starts Unreal Editor. `scope=editor_metadata` or `all` ingests existing exports without launching Editor unless the caller explicitly sets `allowEditorLaunch=true`. |
| Write target blocked | Existing files require `replace_in_file`; keep writes under the selected project's allowed roots and use `write_file` only for a new path |
| `static_validate_project` reports errors | Treat findings as advisory. Fix relevant issues or run `build_unreal_project` immediately for authoritative UBT/UHT diagnostics |
| Build seems blocked by missing validation/plan | An unsupported legacy process/stale config is active or `ALLOW_UNREAL_BUILD=0`. Direct build does not consult validation, task, or plan state |
| RAG `scope=project_miss` / `projectMatchCount=0` / `freshnessAdvisory` | The selected project filter returned no current project rows; guideline/engine text is not project-code evidence. Direct may return one advisory `search_files` suggestion carrying the same project selector, while the model remains free to read that project's Source, change the query, or answer from other sufficient evidence. |
| Inventory / "what's missing" loops on RAG only | Use `search_files` and direct Source reads; RAG absence alone is not proof of project absence |
| `UHT_MACRO_IN_CONDITIONAL_BLOCK` on write | Reflection macros (`UCLASS`/`UPROPERTY`/`UFUNCTION`/`GENERATED_BODY`) sit inside a preprocessor conditional UHT cannot parse (e.g. `#if !UE_BUILD_SHIPPING`). Declare them unconditionally in the header; guard only the `.cpp` implementation. `WITH_EDITOR` / `WITH_EDITORONLY_DATA` blocks are allowed. |
| `GENGINE_WORLD_CONTEXT` on write | Code resolves worlds via `GEngine->GetWorld()` / `GEngine->GetGameInstance()`. Use the owning subsystem/actor `GetWorld()` or an explicit `UWorld*` parameter; get the game instance from `World->GetGameInstance()`. |

## Static LM Studio recommendations

Sampling profiles contain only user-selected load/chat recommendations: context
length, quantization, parallel requests, one static sampling object, the Direct
system prompt, and two bounded write-safety preferences. They contain no task
mode, turn preset, planner policy, required tool order, Strict prompt, or
Compactor ownership setting. The selected model may issue one or several tool
calls whenever its own tool-calling implementation supports that behavior.

The installer writes the static LM Studio preset under
`~/.lmstudio/config-presets/evidence-first-code-audit.preset.json`. Inspect that
JSON when confirming the installed recommendations, run `./rag.ps1 doctor` for
read-only index health, and use `unreal_rag_health` plus `get_workspace_info` for
the live Direct MCP check. Repository-only sampling and benchmark helpers are
not shipped in the portable package and are not part of the installed workflow.
