# LM Studio Unreal Agent Setup

Use this guide for **LM Studio basic chat** with `unreal-rag` + `unreal-agent` MCP.

## 1. Prerequisites

```powershell
# Run from the stable directory where this repository/portable ZIP was extracted.
cd C:\path\to\UE5_Local_LLM_MCP_lmstudio
.\rag.ps1 doctor
```

The factual health payload should identify the configured index and project
binding. The saved evaluation baseline uses UE 5.8, but the Direct MCP resolves
the selected `.uproject` descriptor and supports multiple installed UE 5.x
versions. Pass an exact per-call project selector and, when discovery cannot
resolve a custom/source build, an explicit `engineRoot`. Exact project selection
also chooses its compatible engine-bound RAG shard; one call does not merge
projects owned by different engine shards.

## 2. MCP configuration

File: `$HOME\.lmstudio\mcp.json`

Required servers:

- `unreal-rag` — active-project selection, factual search/symbol evidence, health, and index refresh/status
- `unreal-agent` — read/write files, UBT build

The normal entries run **Direct Model Mode**. Their catalog is stable and is not reduced by task state, route phase, or `MCP_ESSENTIAL_TOOLS`. Direct calls do not require `unreal_task_start`, `unreal_agent_plan`, `taskAuthorization`, a required-next-tool handoff, or synthesis acknowledgement. Filesystem containment, scoped snapshot/CAS checks, command allowlists, deletion approval, and SAFE/AGENT authority still apply. See [LMStudio_MCP_Tool_Discipline.md](LMStudio_MCP_Tool_Discipline.md).

After path changes:

```powershell
cd C:\path\to\UE5_Local_LLM_MCP_lmstudio
python install.py --profile standard --yes
.\rag.ps1 doctor
```

Re-run the same installer profile/options originally used when Agent write/build
authority was enabled; `standard` above is the read-only/default repair example.

Restart LM Studio so MCP servers reload.

## 2a. Node Strict (optional)

Do not change the installer-managed Direct entries in place. The sole supported Strict implementation is a separately named Node entry:

- Node Strict: copy `unreal-agent` to `unreal-agent-strict` and point it at `lmstudio-unreal-agent-mcp/src/strict-server.js`. Call `strict_begin` to create its conversation-scoped session. Reads/searches remain free; mutations and long-running tools require that session.

The old Python task/route/planner controller is unsupported, is not an MCP configuration option, and is omitted from portable packages. `MCP_EXECUTION_MODE` does not select a supported Python mode. Node Strict has no Python peer, shared session, cross-server authorization, or automatic handoff. Avoid exposing Node Strict beside the same Direct tool names unless duplicate-name debugging is intentional.

Before the model sends its final answer in Node Strict, it must explicitly call `strict_complete`, because MCP transport cannot observe final-answer delivery. Use `strict_fail` or `strict_cancel` for those outcomes. An unfinished session becomes nonblocking `orphaned` state on connection/process shutdown, TTL expiry, or restart; `strict_resume` requires explicit user approval.

## 2b. Context compactor (multi-turn chats)

> **Select the real LLM; keep the compactor disabled by default.**
>
> 1. Load and select the actual model in the **model dropdown**. Qwen 3.8 27B is the current validated recommendation; Muse Glimmer is under testing and is not yet a validated recommendation.
> 2. Create or open a chat.
> 3. Leave the top-level **`codex/unreal-context-compactor`** switch **OFF** in that chat's **plugin panel**. Turn it off manually in an older chat that retained an opt-in.
> 4. Leave the nested **Enable transparent compaction** switch OFF. It is separate from top-level activation.

Installation pins the plugin for availability but does not activate it for a chat. The default Direct setup therefore uses the real LLM without the compactor. Only a deliberate two-switch, per-chat opt-in runs the prediction loop; that optional path passes the selected model's MCP tools through unchanged and may retain bounded factual continuity. It cannot plan, route, authorize a write/build, require a next tool, or declare completion.

This command verifies the installed source/build wiring, not chat-level activation:

```powershell
cd <repo>\lmstudio-context-compactor-plugin
npm run status
```

## 3. System prompt

Direct Mode uses [`prompts/lmstudio_direct_model_system.md`](../prompts/lmstudio_direct_model_system.md). It leaves reasoning, tool selection, stopping, and the final answer with the LLM selected in LM Studio while asking for focused evidence, edits, and honest verification. Tool schemas describe their own arguments; do not add task, route, planner, gate, or required-next-tool instructions.

Every current Direct profile uses the same [`lmstudio_direct_model_system.md`](../prompts/lmstudio_direct_model_system.md). Qwen 3.8 27B is the primary validated recommendation. Muse Glimmer is under testing only; Qwen 3.5, Qwen 3.6 27B, and GPT-OSS profiles or scorecards are historical compatibility/evaluation material rather than current recommendations. Do not combine the Direct prompt with an older model-specific or `compact_mcp_base` prompt. Historical evaluation prompts encode removed planner/task gates, do not describe the Node `strict_begin` lifecycle, and are excluded from the portable Direct runtime.

**Qwen / thinking models:** if visible reasoning causes prose instead of a requested tool call, turn thinking off for bounded edit/build turns. This is a model sampling choice, not a Direct MCP gate.

## 4. Session start (every chat)

No server-owned task bootstrap is required. A useful first pass is:

1. `unreal_get_active_project`
2. If it is not the requested project, pass the exact `.uproject`/project name on the call or use `unreal_set_active_project` / `set_active_project`.
3. Check `unreal_rag_health` when RAG evidence is needed.
4. Use `get_workspace_info` when you need the agent's roots and safety flags.
5. Search or inspect, read the exact target, retain its `fileVersionReceipt`, then use `replace_in_file`; `write_file` is only for brand-new files.

These are practical suggestions, not a required server sequence. The selected model may omit irrelevant steps or call `build_unreal_project` immediately when the user asks only for a build diagnosis.

Do not use `run_javascript`, `js-code-sandbox`, Deno file APIs, Node `fs`, or browser/code-sandbox tools for project file I/O. If LM Studio exposes the JavaScript/TypeScript Code Sandbox plugin, hide or disable it for Unreal coding chats.

Do not paste saved N-turn/task templates into a Direct chat. They are historical evaluation inputs, not supported MCP instructions or portable runtime assets.

## 5. Standard loop

```
exact project -> search/inspect -> read + retain receipt -> replace_in_file -> optional static_validate_project -> build/test when useful
```

Rules:

- Do **not** paste full `.cpp` in chat when MCP write is available.
- Do not claim a successful build or test without its output. A source-only task may still finish without a build when that limitation is stated.
- Existing source files are patch-only. `write_file` is for brand-new files; existing `.h`, `.cpp`, and `.cs` writes are blocked by default in `unreal-agent`.
- Direct writes enforce containment, create-only/patch-only rules, scoped snapshot/CAS concurrency, size limits, atomicity, and locks. Prefer `fileVersionReceipt`; a valid raw `expectedHash` remains compatible, and a reliable same-session latest snapshot may be resolved automatically. Successful mutations return a new receipt for consecutive edits. Re-read after `FILE_VERSION_CONFLICT`, `FILE_SNAPSHOT_*`, or uncertain external state. Narrow semantic denylist findings are success-response advisories only; analyzer findings or unavailability never authorize or block a Direct write/build. `VALIDATE_ON_WRITE` does not run project-wide static validation or gate a Direct write/build.
- `static_validate_project` is an independent advisory diagnostic. Its findings do not authorize, roll back, or block `build_unreal_project`.
- `build_unreal_project` resolves and runs the selected project/version immediately when `ALLOW_UNREAL_BUILD=1`; no task, plan, code-sketch, or static-validation certificate is required. `target=Editor` resolves the selected project's canonical, configured preferred, or sole discovered custom Editor target, while an explicit non-Editor target is unchanged.
- Build and Automation share a bounded process runner. Timeout terminates the process tree; stdout/stderr and the persisted `fullLogPath` can be bounded head/tail projections with omitted-byte metadata rather than complete raw output.

## 6. RAG query hints

| Need | Optional RAG hint | Notes |
|------|----------|-------|
| New component evidence | `prototype_component` | Prefer component-related engine/project examples |
| Code evidence | `code_sketch` | Retrieval ranking hint only; it does not draft or validate a plan |
| Compile error evidence | `compile_fix` | Include the diagnostic text in the query |
| Runtime crash evidence | `runtime_debug` | Include the log or callstack in the query |
| Refactor evidence | `refactor_r0`..`r4` | Historical ranking aliases only; they do not create stages or gate Direct tools |

Use the single Direct system prompt for these requests. Historical fixed-order
prototype/refactor presets are not shipped. They remain quarantined under
`legacy_eval/prompts` only in the development repository because they referenced
removed planner and validation-gate tools.

## 7. Large codegen

Default Direct exposes the immediate `build_unreal_project` diagnostic and does not expose a model-driving compile loop. The old `unreal_start_compile_loop`, `unreal_compile_loop_status`, `unreal_cancel_compile_loop`, and `unreal_generate_compile_loop` MCP tools are unsupported and are not shipped in the portable runtime. Keep the selected chat model in control of any subsequent read, edit, build, or test call.

## 8. Troubleshooting

| Symptom | Fix |
|---------|-----|
| RAG MCP fails to start | From the current checkout/package, re-run the same `python install.py --profile ... --yes` command used for installation, run `.\rag.ps1 doctor`, then restart LM Studio. Avoid WindowsApps Python. |
| write blocked | `ALLOW_WRITE=1` in unreal-agent env |
| `static_validate_project` reports findings | Treat them as advisory diagnostics; fix relevant findings or build immediately to obtain authoritative UBT/UHT output |
| Direct call returns `status=no_new_information` | This chat echoed a still-valid `repeatReceipt` from a full Direct RAG/Node read, or a Node failure repeated. Omit an unknown receipt to receive full content. Direct RAG has no pagination token; use `nextDetailLevel` only when a result is truncated. |
| Slow search | Use `hybrid=false` on search for faster FTS-only (Phase H tuning) |

## 9. Rider + Cline (주력 IDE)

Primary: **JetBrains Rider** for Unreal C++ build/debug.  
Agent: **Cline** with MCP — see [`docs/Cline_Rider_Unreal_Agent_Setup.md`](Cline_Rider_Unreal_Agent_Setup.md).

Install MCP into Cline:

```powershell
python install.py --profile custom --components codex,lmstudio,unreal,cline --cline-settings C:\path\to\cline_mcp_settings.json
```

## 10. Static model recommendations

Choose and load the model in LM Studio itself. MCP servers do not select, switch, or retune the model by task, phase, retry, or turn.

| Profile | Use |
|---------|-----|
| `qwen3_8_27b` | **Primary validated recommendation** — 64K context, Q4_K_M, parallel 1 |

Muse Glimmer is under testing and has no validated recommendation yet. Legacy
Qwen 3.5, Qwen 3.6 27B, and GPT-OSS entries may remain resolvable for historical
compatibility, but this guide does not recommend them for current Direct use.

Old N-turn prompts and post-build Python planner tools are historical evaluation fixtures, not supported Direct MCP behavior. Direct exposes immediate build/test diagnostics and leaves any subsequent inspection to the selected model.

The portable `rag.ps1` contains no model evaluation or planner commands. Use it
only for the documented collection, index, Direct project selection, refresh,
and health operations; conduct model evaluation in a separate development
checkout so it cannot become runtime authority.
