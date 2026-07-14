# LM Studio MCP Tool Discipline

Guide for **LM Studio basic chat** with `unreal-rag` + `unreal-agent` MCP. This is the chat path, not the automated wrapper path.

## Wrapper vs Chat

| Path | Orchestrator | Enforcement |
|------|--------------|-------------|
| `lmstudio_unreal_wrapper.py` | Yes; injects plan JSON | JSON schema, edit limits, static validation, UBT loop |
| LM Studio chat | Yes, if the model calls `unreal_agent_plan` | System prompt, Essential Tools, tool descriptions, **advisory** `writeGate` / gates, hard loop guards on the MCP servers |

Weak local models fail when too many tools are exposed. Use **Essential Tools** mode for chat.

## Essential Tools Mode

Set this in both MCP server env blocks in `%USERPROFILE%\.lmstudio\mcp.json`:

```json
"MCP_ESSENTIAL_TOOLS": "1"
```

Re-run installer or:

```powershell
python scripts/patch_mcp_config.py
```

Restart LM Studio after changes.

### Hotfix restart (read-loop / evidence guards)

After pulling Hotfix 1–3 commits that change `lmstudio-unreal-agent-mcp` (`sha256Text` import, failure repeat block, coverage-based read guard), **fully restart the `unreal-agent` MCP server** in LM Studio (or restart LM Studio). An old Node process will keep the previous livelock behavior (returning prior ranges as `ok: true`).

Confirm after restart:

- Novel `read_file_range` windows are still served (not replaced by an older range body).
- Identical re-reads return `READ_REPEAT_DETECTED` with `cached: true`.
- Evidence stagnation returns `isError: true` / `EVIDENCE_STAGNATION` (repeat escalation: `EVIDENCE_STAGNATION_REPEAT`) with no substituted prior code body.
- Cached covering ranges never cross files (file A body is never returned for file B).

### unreal-rag (12 tools)

- `unreal_get_active_project`
- `unreal_set_active_project`
- `unreal_rag_health`
- `unreal_agent_plan` - call first after `unreal_get_active_project`
- `unreal_rag_search`
- `unreal_symbol_lookup`
- `unreal_agent_session`
- `unreal_rag_capabilities`
- `unreal_code_sketch_claim_validate` - verify drafted APIs before showing code sketches
- `unreal_review_claim_validate` - batch-validate review findings (including by-design / header-contract false positives)
- `unreal_diagram_validate`
- `unreal_project_status`

### unreal-agent (14 tools)

- `get_workspace_info`, `get_active_project`
- `list_directory`, `read_file`, `read_file_range`, `read_symbol`, `search_files`
- `replace_in_file`, `write_file`, `static_validate_project`
- `build_unreal_project`, `read_unreal_logs`
- `write_session_handoff` - save a compact resume note before a fresh chat; safe-mode allowed; overwrites only `.agent/handoff/latest.md`
- `record_bootstrap_step` - record bootstrap cache steps so later chats can skip healthy checks

### Extended tools (`MCP_EXTENDED_TOOLS=1`)

Enable when you need refresh, compile loop jobs, extra claim validators, refactor helpers, or cleanup:

**unreal-rag:** `unreal_rag_refresh`, `unreal_start_rag_refresh`, `unreal_rag_refresh_status`, compile-loop tools, editor metadata, asset graph, material/blueprint validators, refactor manager, render report.

**unreal-agent:** `propose_file_deletions`, `delete_file` (requires `ALLOW_SOURCE_DELETE=1` and a matching deletion plan token), `set_active_project`, refactor scan/plan tools.

With `MCP_ESSENTIAL_TOOLS=1` alone, extended tools stay hidden to reduce model confusion.

## Advisory contracts (chat path)

In LM Studio chat, `writeGate` and plan `gates` are **advisory** (prompt/orchestrator text). The wrapper path can enforce harder. Still obey `writeGate.writesAllowed=false`. Server-side loop guards (`READ_REPEAT_*`, `EVIDENCE_STAGNATION*`, `TOOL_REPEAT_BLOCKED`, mutation duplicate) are hard.

## Logic review (false-bug guard)

When reviewing gameplay/cinematic logic (not compile errors):

1. Read the sibling `.h` UENUM / field comments **before** calling `read_symbol` / concluding a bug from `.cpp` alone.
2. Label every finding `Bug` | `ByDesign` | `Ambiguous` | `NeedsRuntimeProof`.
3. Intentional early returns that match header contracts (e.g. AuthoredWorld = keep asset transform) are **ByDesign**, not "missing logic".
4. Batch findings through `unreal_review_claim_validate` — it rejects false missing/unused claims **and** logic-missing claims that contradict by-design header text (`by_design_contract`, `header_contract_unread`).

## Validate-on-write

When agent mode enables writes, `patch_mcp_config.py` sets `VALIDATE_ON_WRITE=1` by default. `write_file` / `replace_in_file` run static validation and fail closed on project-wide errors (duplicate basenames, bad includes, etc.). Fix findings before `build_unreal_project`.

Validation on write runs under a time budget (`VALIDATE_ON_WRITE_TIMEOUT_MS`, default 45000). If validation exceeds the budget it fails **open**: the write succeeds and the response notes `validation skipped (time budget); run static_validate_project before build`. When you see that note, run `static_validate_project` before building. Real findings still fail closed and roll back.

## Write Safety and Flow

### 3-Tier write blocking

| Tier | Scope | Blocks write? | Rollback? |
|------|-------|---------------|-----------|
| A — structural | `write_file` create-only, patch-only source, basename collision, protected paths | Yes | N/A (disk not written) |
| B — compile-readiness | static validator `severity=error` on the written file | Yes | Yes |
| B-deferred | counterpart-file flow (`CPP_DEFINITION_MISSING`, RPC/native-event impl missing) | No (intentional) | No |
| C — GC/runtime quality | new advisory warnings (UPROPERTY, delegate/timer teardown, Cast null, etc.) | **No** | No |

**Advisory ≠ write permission:** Tier C warnings never override Tier A/B. GC/runtime advisories do not allow `write_file` on existing paths.

### Generation self-check (non-blocking)

Before introducing new UObject/Component/Subsystem APIs in a write turn, call `unreal_code_sketch_claim_validate` on the draft surface. Fix `known_bad` findings before writing; the tool does not auto-block writes.

`write_file` is **create-only**. It creates brand-new files and refuses to overwrite any file that already exists (every extension, not just source). To modify an existing file, use `replace_in_file`.

The only override is the server env var `ALLOW_EXISTING_SOURCE_WRITE=1` in `%USERPROFILE%\.lmstudio\mcp.json`. It is a deliberate human-set escape hatch for one-off manual operations: the model cannot enable it through any tool argument, the server logs a startup warning while it is on, and `get_workspace_info` reports `allowExistingSourceWrite=true`. Unset it immediately after use.

- If `write_file` returns `blocked because file already exists`: switch to `replace_in_file`. **Do not retry `write_file`** on that path.
- On a tool timeout (`MCP error -32001`): never immediately retry the same write. First verify state with `list_directory` / `read_file`. If the file now exists, switch to `replace_in_file`; if the situation is unclear, stop and summarize for the user. A timeout is a hard-stop signal.
- After a successful `write_file` / `replace_in_file`: report the changed file in one line, then continue to the next planned step automatically. Do not ask "continue?" after every file — successful work never waits for the user.
- Hard-stop checkpoints (report status, then wait for the user) trigger only on risk signals: (a) any tool timeout, (b) static validation failure / rollback, (c) "Model failed to generate a tool call", (d) the same failure repeating. Successful writes are not a stop signal.
- If a write response says `rollback skipped ... (conflict)`: another operation changed the file. Stop, `read_file` the current content, and reconcile before editing again.
- **Duplicate-call loop breaker:** the server rejects byte-identical repeated `write_file` / `replace_in_file` calls. A consecutive identical repeat is rejected immediately; a non-consecutive identical retry is allowed once (e.g. retry after fixing a dependency file) and rejected from the third attempt within ~15 minutes. The rejection message (`identical ... call already attempted`) means the model is looping: re-read the file, change the patch, or stop and summarize for the user.

## Forbidden Tools

Do not use LM Studio's JavaScript/code sandbox for Unreal project file work:

- `run_javascript`
- `lmstudio/js-code-sandbox`
- `Deno.readTextFile` / `Deno.writeTextFile`
- Node `fs` / CommonJS `require`

That sandbox has its own working directory and is not rooted at the active `.uproject`. Project file I/O must go through `unreal-agent`: `read_file_range`, `read_file`, `replace_in_file`, and `write_file` only for brand-new files.

If LM Studio auto-approves this sandbox, remove these patterns from `%USERPROFILE%\.lmstudio\settings.json` `chat.skipToolConfirmationPatterns`:

```json
"lmstudio/js-code-sandbox:run_javascript",
"lmstudio/js-code-sandbox:*"
```

Restart LM Studio after changing that setting. If the plugin is still shown to the model, hide or disable the JavaScript/TypeScript Code Sandbox plugin in LM Studio for Unreal coding chats.

## Required Chat Order

1. `unreal_get_active_project`
2. `unreal_agent_plan`
3. Follow returned `toolPolicy`
4. Obey returned `writeGate`
5. Use returned `checkpoints` before moving to the next tool
6. Stop according to returned `stopConditions`

For edit tasks:

- Do not write when `writeGate.writesAllowed=false`.
- Read every target file before `replace_in_file` or `write_file`.
- Prefer `replace_in_file` with `expectedOccurrences=1` for existing files.
- Use `write_file` only for brand-new files. Existing `.h`, `.hpp`, `.cpp`, `.c`, `.cc`, `.cxx`, and `.cs` files are patch-only.
- Run `build_unreal_project` after C++ or `Build.cs` edits.
- If cleanup appears to require deleting files, finish all edits first, call `propose_file_deletions`, report the count/path/file name/reason/if-not-deleted impact/if-deleted impact, and wait for explicit user approval before `delete_file`.
- On UBT failure, search only the current error context with `mode=compile_fix`, then patch the smallest failing surface.

## Diagram Output

When the user asks for a diagram, or when explaining structure, dependencies, ownership, Blueprint or Material graph flow, shader pipeline, or runtime call order, show Mermaid first. Put ASCII/text only after the Mermaid block as a fallback for clients that do not render Mermaid.

## Session Bootstrap

Paste [`prompts/lmstudio_session_bootstrap.md`](../prompts/lmstudio_session_bootstrap.md) as the **first user message** every chat.

## Context Budget and Session Handoff

`build_unreal_project` is compact by default: it returns a one-line `summary`, up to 40 likely error lines, and `.agent/logs/latest-build.log` as `fullLogPath`. Raw stdout/stderr is omitted unless `verboseOutput=true`. `read_unreal_logs` defaults to the first error cluster from the newest log (one file, 60 tail lines).

All unreal-agent results have a final `MCP_AGENT_RESULT_MAX_CHARS` safety ceiling (default 32000 characters). Narrow the tool arguments when a response reports truncation; do not immediately request verbose output.

Before context/KV-cache overflow, a failed tool-call loop, or a risky stop:

1. Call `write_session_handoff` with changed files, open errors, next steps, and failed approaches.
2. Start a fresh chat and paste [`prompts/lmstudio_session_bootstrap.md`](../prompts/lmstudio_session_bootstrap.md).
3. Ask the model to read `.agent/handoff/latest.md` and continue from the smallest next step.

The handoff tool writes only to the fixed `.agent/handoff/latest.md` artifact path under `WORKSPACE_ROOT`. It overwrites that file on every call, does not require `ALLOW_WRITE=1`, and never writes project source files. Safe mode still performs this one artifact write by design.

## Model and System Prompt

| Profile / model | System prompt |
|-----------------|---------------|
| `qwen3_6_27b` (primary) | [`lmstudio_qwen36_27b_compact_system.md`](../prompts/lmstudio_qwen36_27b_compact_system.md) + base |
| `gpt_oss_*` | [`lmstudio_gpt_oss_compact_system.md`](../prompts/lmstudio_gpt_oss_compact_system.md) + base |
| `qwen3_5_9b`, `qwen3_8b` | [`lmstudio_qwen35_9b_compact_system.md`](../prompts/lmstudio_qwen35_9b_compact_system.md) + base |

Always include [`lmstudio_compact_mcp_base.md`](../prompts/lmstudio_compact_mcp_base.md) for one-tool-per-turn and read-before-write discipline.

## Model-Specific Notes

### Qwen 3.6 27B

- Primary wrapper + Pass@K KPI model.
- Enable Essential Tools; use compact system prompt + base rules.
- **Thinking leak:** disable visible reasoning in LM Studio or use execute/`compile_fix_patch` turns with thinking OFF. Do not print "thinking process" in visible chat.
- For `module_fix` / `GameplayTags` / `Build.cs` errors: read full `*.Build.cs` from project state, then patch the file — do not answer with explanation only.

### GPT OSS 20B

- JSON argument drift is common; prefer one file per patch turn even though the profile allows 2.
- Context is 32768 in sampling profile.

### Qwen 3.5 9B

- Keep API names and paths in English; Korean summaries are OK.
- Context should be at least 24576 for compact profiles.

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| Model answers without tools | Resend session bootstrap; check Essential Tools ON |
| Wrong paths (`Documents` vs project) | Call `unreal_get_active_project`; use returned root |
| Writes on review/runtime tasks | Re-call `unreal_agent_plan`; obey `writeGate.writesAllowed=false` |
| Model calls `run_javascript` / `js-code-sandbox` | Start a new chat with the bootstrap prompt, remove sandbox auto-approval, and hide/disable the sandbox plugin if available |
| Hallucinated analysis | Force `read_file` before claims or edits |
| False logic bugs (early return = "missing") | Read sibling `.h` UENUM/field docs first; classify `ByDesign`/`Ambiguous`; run `unreal_review_claim_validate` |
| `READ_REPEAT_DETECTED` / `evidenceStatus=cached` | Stop re-reading that path; use returned `content` and finish |
| `EVIDENCE_STAGNATION` / `EVIDENCE_STAGNATION_REPEAT` | Stop evidence tools; finish from existing context. Restart `unreal-agent` if Hotfix 3+ is not loaded |
| `TOOL_REPEAT_BLOCKED` | Identical call failed internally twice — do **not** retry same args; handoff / fresh chat. Distinct from evidence stagnation |
| Repeated no-op patch / `MUTATION_REPEAT_BLOCKED` | Re-read file, patch only missing current text, set `expectedOccurrences=1` |
| Tool not in list | Essential mode hides advanced tools; use Extended or wrapper/Cline for clangd/graph/deletion |
| `unreal_rag_refresh` times out | Re-run `python scripts/patch_mcp_config.py` so `unreal-rag` has `"timeout": 420000` (7 minutes, ms) and `unreal-agent` has `"timeout": 720000` in `%USERPROFILE%\.lmstudio\mcp.json`, then restart LM Studio. Prefer `unreal_start_rag_refresh` + `unreal_rag_refresh_status` for long refresh. Use `scope=project_source` when Editor metadata is not needed. |
| Write blocked for basename collision | Use `search_files` to find the existing file; patch with `replace_in_file` on that path. Extended mode: after duplicate cleanup, call `propose_file_deletions`, get approval, then `delete_file` with `ALLOW_SOURCE_DELETE=1`. Essential: report path and stop. |
| `identical ... call already attempted` | The model repeated a byte-identical edit (stuck loop). `read_file` the current content, change the patch, or checkpoint and summarize. If loops persist, start a fresh chat with the session bootstrap. |
| RAG `repeatDetected` / `ok=false` after detail escalate | Pass `continuationToken` with `detailLevel=nextDetailLevel`, or answer from prior matches / `search_files` |
| `UHT_MACRO_IN_CONDITIONAL_BLOCK` on write | Reflection macros (`UCLASS`/`UPROPERTY`/`UFUNCTION`/`GENERATED_BODY`) sit inside a preprocessor conditional UHT cannot parse (e.g. `#if !UE_BUILD_SHIPPING`). Declare them unconditionally in the header; guard only the `.cpp` implementation. `WITH_EDITOR` / `WITH_EDITORONLY_DATA` blocks are allowed. |
| `GENGINE_WORLD_CONTEXT` on write | Code resolves worlds via `GEngine->GetWorld()` / `GEngine->GetGameInstance()`. Use the owning subsystem/actor `GetWorld()` or an explicit `UWorld*` parameter; get the game instance from `World->GetGameInstance()`. |

## Sampling Metadata

Profiles may include:

- `mcpEssentialTools: true`
- `recommendedSystemPrompt: "prompts/..."`
- `mcpToolDiscipline: "one_tool_per_turn"`

Inspect:

```powershell
python scripts/load_sampling_preset.py --sampling-profile qwen3_6_27b --show-profile
python scripts/bench_lmstudio_mcp.py
```
