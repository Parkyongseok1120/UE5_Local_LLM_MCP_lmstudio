<img width="1920" height="1080" alt="Image" src="https://github.com/user-attachments/assets/cd25e0fe-d6fd-4ea8-be24-d1606bb644aa" />


# UE5_Local_LLM_MCP_lmstudio 1.3.3

> **Stable v1.3.3 release:** Direct Model Mode remains the supported default. This release makes search results directly reusable with exact project identity, bounds Direct RAG evidence inside its serialized response envelope, and clarifies that Evidence-First contract lookup is optional rather than a tool-order authority. Existing receipt/CAS/atomic-write boundaries and the default-OFF context compactor policy remain unchanged. The stack is designed for multiple Unreal Engine versions and projects. See the [1.3.3 release notes](docs/Release_Notes_1_3_3.md) and [Integrated Installer](docs/Integrated_Installer.md).

Local **RAG + MCP stack** for using local LLMs in LM Studio as Unreal Engine 5.x C++ assistants.

<p align="center">
  <a href="README.md"><img alt="English" src="https://img.shields.io/badge/Language-English-blue"></a>
  <a href="README.ko.md"><img alt="Korean" src="https://img.shields.io/badge/Language-%ED%95%9C%EA%B5%AD%EC%96%B4-green"></a>
</p>

---

## ☕ Support This Project

If this project has been useful to you, please consider sponsoring — it helps keep development going.

[![Sponsor on GitHub](https://img.shields.io/badge/Sponsor-%E2%9D%A4-red?logo=github)](https://github.com/sponsors/Parkyongseok1120)

👉 **[https://github.com/sponsors/Parkyongseok1120](https://github.com/sponsors/Parkyongseok1120)**

---

## Project Status

> **Project Status — August 2026**
>
> **Current product label: 1.3.3 (stable).** The supported runtime uses Direct Model Mode, scoped file-version receipts, provenance-bound RAG generations, bounded build/Automation processes, and provenance-aware durable continuity. The optional context-continuity plugin is not chat-activated by the installer, so its single host-owned chat toggle remains OFF by default. MCP servers provide capabilities and enforce filesystem, process, build, and project safety; they do not own the model's task plan or tool sequence.
>
> Stable v1.3.3 component metadata is Node agent MCP 0.3.21, Evidence-First MCP server 1.1.1, Context Compactor 0.4.51/revision 98, and portable manifest 2.1.16. Search results pair reusable scoped URIs with exact project identity, Direct RAG reserves its serialized envelope and bounds match metadata, and Evidence-First contract lookup is optional rather than a routine preflight.
>
> A release-operator live E2E run with Qwen 3.8 27B completed active-project discovery, repeated RAG/search/read rounds across a real Unreal project, a large architecture report, and follow-up continuity without reproducing the prior mid-session JSON truncation. This is a functional workflow check, not a benchmark score or universal host certification.
>
> Automated source, package, installer, safety, and cross-platform gates define release readiness. A passing release gate is not a universal compatibility claim for every host, Unreal project, engine build, plugin, or editor-runtime combination.

## Documentation Hub

<p>
  <a href="docs/Project_Overview.md"><img alt="Project Overview" src="https://img.shields.io/badge/Docs-Project%20Overview-blue?logo=gitbook"></a>
  <a href="docs/Release_Notes_1_3_3.md"><img alt="1.3.3 Notes" src="https://img.shields.io/badge/Release-1.3.3-blue?logo=github"></a>
  <a href="docs/Model_Measurement_Results.md"><img alt="Model Results" src="https://img.shields.io/badge/Docs-Model%20Results-purple?logo=gitbook"></a>
  <a href="docs/Version_Performance_History.md"><img alt="Version Performance" src="https://img.shields.io/badge/Docs-Version%20Performance-green?logo=gitbook"></a>
</p>

## Model Guidance

**Qwen 3.8 27B is highly recommended as the primary validated operating model for this stack.** Its v1.3.2 live E2E run completed a long real-project RAG/read/report workflow without the prior context truncation. Muse Glimmer is under testing and is not yet a validated recommendation. Qwen 3.5, community Qwen 3.6 27B checkpoints, and GPT-OSS are not currently recommended.

Historical live-test scores and timing records are intentionally omitted from this README. See [Model Measurement Results](docs/Model_Measurement_Results.md) only when historical measurement evidence is needed; those archived results are not current model recommendations or a v1.3.3 quality score.

> **BYOI** = Bring Your Own Index. This repo ships **tooling only**: not Epic source, not a pre-built `rag.sqlite`.

### OSS clone vs Portable ZIP

| Distribution | Index | Install |
|--------------|-------|---------|
| **GitHub clone (this repo)** | You build `rag.sqlite` locally (`rag.ps1 build`) | Root `INSTALL.bat` / `install.sh` |
| **Portable ZIP** | May include a pre-built index | Root `INSTALL.bat` / `install.sh` |

See [`docs/VERSIONING.md`](docs/VERSIONING.md) for product vs component version numbers.

Extract a Portable ZIP to a stable directory and retain it after installation:
the LM Studio RAG/Agent MCP entries execute from that extracted runtime tree.
Portable packages exclude `node_modules`, so do not use `--skip-deps` on the
first Unreal install. The installer now fails before writing `mcp.json` if the
pinned Agent SDK is not already resolvable.

## Quick Install

```text
git clone https://github.com/Parkyongseok1120/UE5_Local_LLM_MCP_lmstudio.git
cd UE5_Local_LLM_MCP_lmstudio
# Windows: INSTALL.bat
# Ubuntu Linux/macOS: ./install.sh
```

On a freshly installed supported host, these launchers download a pinned,
SHA-256-verified uv seed and install managed Python 3.12 under the selected
user state-home before continuing. They do not register Python system-wide or
modify the system PATH. Running `python3 install.py` directly still requires a
host Python 3.10+ interpreter.

The unified installer asks for SAFE, STANDARD, FULL, or CUSTOM. When an Unreal adapter is included, it presents a numbered SAFE/AGENT authority choice and shows the final authority in a confirmation summary. SAFE installs the generic coding-reasoning layer and LM Studio integration without a project adapter. STANDARD adds read-only Unreal adapters. All LM Studio/Unreal profiles install and pin the context compactor so it is available, but never chat-activate it; the host-owned chat toggle remains OFF until the user enables it for a long chat. FULL remains read-only unless AGENT authority is explicitly confirmed. See [Integrated Installer](docs/Integrated_Installer.md).

### One installer, two platform launchers

`INSTALL.bat` and `install.sh` are platform launchers for the same `install.py` implementation. Their small pre-Python helpers only bridge a clean machine into that implementation; they do not duplicate profile, component, or installation logic. There are no separate SAFE, AGENT, RAG, Cline, or context-compactor installers. Choose those options inside the integrated installer. `installer/` contains bootstrap runtime code and validated manifests; advanced maintenance tools live under `scripts/installer_support/`.

### Direct Model Mode is the default

The normal `unreal-rag` and `unreal-agent` entries are capability providers. The model may search, read, edit, validate, build, or test in the order appropriate for the request. You do **not** start `unreal_task_start`, create a server plan, acquire route authorization, or commit synthesis before using a capability. Read/write containment, optimistic concurrency, command allowlists, explicit delete approval, and SAFE/AGENT authority still apply.

> **Important — select the real LLM as the chat model.**
>
> 1. Load and select the actual instruction/tool-calling model in LM Studio's **model dropdown**. Qwen 3.8 27B is the current highly recommended, primary validated model.
> 2. Leave the top-level **`codex/unreal-context-compactor`** switch **OFF** in that chat's **plugin panel**. The installer does not enable this host-owned switch; verify it is OFF in every new or existing chat.
> 3. Start Local Server and enable the default `unreal-rag` and `unreal-agent` MCP entries.

The default setup does not run the compactor. Installation and pinning only make it available in the panel; they do not add it to a chat. For a long chat that needs bounded continuity, enable the single top-level `codex/unreal-context-compactor` switch for that chat. Handler invocation is the activation boundary; there is no second enable control. `Observe only` remains available for measurement without rewriting model-facing history. The plugin does not choose the model, change sampling, filter MCP tools, or grant write/build authority.

This command verifies the installed plugin's source layout and compiled prediction-loop wiring. It does **not** prove chat-level activation:

```shell
cd lmstudio-context-compactor-plugin
npm run status
```

The context plugin is a continuity aid, not a prerequisite for Direct MCP authority. Cline, CLI, Ollama, custom, and remote clients can use the MCP capability servers without the LM Studio chat plugin.

### Multiple projects and Unreal versions

One MCP installation can serve multiple Unreal projects and installed UE versions. `set_active_project` provides a convenient default, but Direct file, search, edit, log, command, build, and Automation tools accept an exact `.uproject` path or exact discovered project name through their advertised `project`, `projectRoot`, or `hint` field where applicable. A per-call project selector overrides the active project for that call only; it does not create route ownership or retarget another chat.

Build and Automation calls resolve the selected project's engine association and may also accept an explicit `engineRoot`. This allows UE 5.x projects on different engine installations to share the same server. Prefer exact selectors: an ambiguous project name returns an error instead of silently choosing another project.

RAG generations are engine-bound sibling shards. An exact project selector routes to the matching shard, and one call never merges evidence from projects bound to different engines. Existing-file reads and successful mutations return an opaque `fileVersionReceipt`; every later edit must explicitly pass that receipt or a valid raw `expectedHash`. Same-session evidence is never selected automatically, and external changes fail closed with `FILE_VERSION_CONFLICT`.

For portable builds, `target=Editor` resolves the selected project's canonical, configured preferred, or sole discovered custom Editor target; an explicit non-Editor target is unchanged. Build and Automation share the bounded process runner and timeout process-tree termination.

### Strict is a separate manual opt-in

Keep the installer-managed `unreal-rag` and `unreal-agent` entries unchanged. The only supported Strict surface is a separately named Node entry:

- Copy `unreal-agent` to `unreal-agent-strict` and point it at `lmstudio-unreal-agent-mcp/src/strict-server.js`.
- Node Strict owns a conversation-scoped lifecycle beginning with `strict_begin`; reads and searches remain task-free while mutations and long-running capabilities require that live Strict session.

The removed Python controller is not a supported Strict entry and cannot authorize Node mutations. The portable package excludes its monolithic MCP entry and Strict manifest. Avoid exposing Node Strict beside the same Direct tool surface unless duplicate-name debugging is intentional.

Node MCP transport cannot observe when the selected model emits its final chat answer. Therefore, immediately before the final answer, the model must call `strict_complete` explicitly (or `strict_fail` / `strict_cancel` for those outcomes). Connection/process shutdown, TTL expiry, and process restart make unfinished Node sessions `orphaned`; an orphan does not block Direct Mode, another conversation, or another project. `strict_resume` requires explicit user approval.

### Rider + Cline (optional)

For JetBrains Rider + [Cline](https://github.com/cline/cline) instead of LM Studio chat:

```powershell
python install.py --profile custom --components codex,lmstudio,unreal,cline --cline-settings C:\path\to\cline_mcp_settings.json
# Add AGENT authority only for a trusted project:
python install.py --profile custom --components codex,lmstudio,unreal,cline --cline-settings C:\path\to\cline_mcp_settings.json --enable-agent-mode --accept-agent-risk
```

See [Rider_Cline_Smoke_Checklist.md](docs/Rider_Cline_Smoke_Checklist.md) and [cline_unreal_agent_system.md](prompts/cline_unreal_agent_system.md). In Direct mode, use the same straightforward flow as LM Studio: select the exact project, inspect/search, read before editing, then validate and run the Rider/UBT build when useful.

> **Required — disable LM Studio's built-in `js-code-sandbox` (JavaScript/TypeScript Code Sandbox).**  
> In LM Studio, turn off or hide the default **JavaScript/TypeScript Code Sandbox** plugin for Unreal coding chats. That sandbox uses a different working directory and is **not** rooted at your active `.uproject`; letting the model use it for file I/O causes wrong paths, broken edits, and conflicts with `unreal-agent`. Use only `unreal-rag` + `unreal-agent` MCP tools (`read_file`, `replace_in_file`, `write_file` for new files). Remove `lmstudio/js-code-sandbox:*`, `mcp/unreal-agent:*`, and `mcp/unreal-rag:*` broad auto-approval patterns from `%USERPROFILE%\.lmstudio\settings.json` and restart LM Studio; the MCP wildcards would suppress host confirmation for deletion and explicitly authorized Editor launch. The installer and `scripts/patch_mcp_config.py` perform this cleanup while preserving unrelated settings. Details: [LMStudio_MCP_Tool_Discipline.md](docs/LMStudio_MCP_Tool_Discipline.md).

```powershell
.\rag.ps1 set-project -ProjectFile C:\Projects\MyGame\MyGame.uproject
.\rag.ps1 refresh -RefreshScope project_source
.\rag.ps1 doctor
```

Use safe mode first. Enable file writes and UBT only for trusted projects:

```powershell
python install.py --profile standard --yes --enable-agent-mode --accept-agent-risk
python install.py --profile standard --yes
```

Ask the selected LM Studio chat model and let it call `unreal_rag_search` or
`unreal_symbol_lookup`. The portable `rag.ps1` is maintenance-only; it does not
run a model, wrapper, planner, evaluation harness, or query-side controller.

## Real-Use Session Tips

Holdout evals run in fresh, bounded turns. In **long LM Studio chats**, context grows with every tool result, build log, and retry. Keep the context compactor's top-level chat switch OFF by default. When continuity is needed, enable that single switch for the affected chat; the handler then measures and compacts during multi-round tool work. It replaces only older model-facing history with deterministic factual memory and never owns task routes, required-next-tool commands, planner state, or synthesis gates. Runtime-local file-version receipts are never durable compaction memory: a compacted file observation keeps its canonical project/path and observed SHA but requires a fresh read before mutation.

| Symptom in LM Studio logs | What to do |
|---|---|
| `request (...) exceeds the available context size (54272)` | Confirm that the actual LLM is selected. For a long chat, enable the single `codex/unreal-context-compactor` chat-plugin switch before context pressure becomes critical. If the request already exceeds the window, use a suitable context length or start a new chat with a 5–10-line factual handoff. `npm --prefix lmstudio-context-compactor-plugin run status` verifies installed source/build wiring only. |
| `failed to restore kv cache` / `cache size limit reached` | Same as above — session memory is saturated. New chat is faster than raising context alone. |
| `Model failed to generate a tool call` after a long edit loop | Stop, summarize changed files + remaining errors, new chat. |
| `js-code-sandbox` appears in logs during Unreal work | Disable it (see Quick Install note above). |

Practical rules for day-to-day Unreal project work:

- **One bounded task per chat** when possible (e.g. “fix these 3 compile errors”, not “implement the whole dev console”).
- **Do not paste full UBT/linker logs** into chat. Use `read_unreal_logs`: `mode=tail` for recent failures, `mode=first_error` to scan from byte zero for the original cause, and `mode=range` with `cursorByte`/`nextCursorByte` for bounded traversal.
- **Header-then-.cpp is normal.** `write_file` on a new header may show advisory `CPP_DEFINITION_MISSING` until the matching `.cpp` is written — that is expected, not a rollback trigger on its own.
- **Avoid invented UE APIs** the model often hallucinates: `UCharacterMovementComponent::DisableGravity()`, `UWorld::GetURL()`, `SpawnActor(..., &FTransform)`, `GEngine->GetWorld()`. Prefer `GravityScale`, `GetMapName()` + `OpenLevel`/`ServerTravel`, `SpawnTransform` by value, and the owning actor/subsystem's `GetWorld()`.
- **Compact tool responses:** `build_unreal_project` returns a one-line summary + up to 40 likely errors + its timestamped `fullLogPath` under `.agent/logs` (not full stdout/stderr). `read_unreal_logs` defaults to the newest bounded tail and exposes whether the source was truncated. The chat plugin retains bounded factual continuity such as the latest real user request, active objective, continuation antecedent, current work and unresolved items, canonical project/path file observations, recent tool outcomes, and recent build/test state. It deliberately removes runtime-local mutation receipts, snapshot registration counters, task/route/control/synthesis internals, and required-next-tool directives.

Automatic compaction extends a session but cannot shrink an oversized system prompt/tool schema or repair a saturated KV cache. If it cannot restore the hard safety margin, start a fresh chat with a short factual handoff containing the exact project, current request, files already changed, and remaining build/test errors.

Details: [LMStudio_MCP_Tool_Discipline.md](docs/LMStudio_MCP_Tool_Discipline.md), [Troubleshooting.md](docs/Troubleshooting.md).

Full requirements, Mac remote setup, model profiles, and security notes are in [Project_Overview.md](docs/Project_Overview.md).

## More Docs

| Topic | File |
|---|---|
| 1.3.3 release notes | [docs/Release_Notes_1_3_3.md](docs/Release_Notes_1_3_3.md) |
| Detailed project overview | [docs/Project_Overview.md](docs/Project_Overview.md) |
| Model measurement results | [docs/Model_Measurement_Results.md](docs/Model_Measurement_Results.md) |
| Version performance history | [docs/Version_Performance_History.md](docs/Version_Performance_History.md) |
| 36-case holdout difficulty | [docs/Holdout_Case_Difficulty.md](docs/Holdout_Case_Difficulty.md) |
| RAG setup reference | [docs/RAG_Setup.md](docs/RAG_Setup.md) |
| Safe vs agent mode | [docs/Safe_Agent_Mode.md](docs/Safe_Agent_Mode.md) |
| Model profiles | [docs/Model_Profiles.md](docs/Model_Profiles.md) |
| LM Studio MCP tool discipline | [docs/LMStudio_MCP_Tool_Discipline.md](docs/LMStudio_MCP_Tool_Discipline.md) |
| Troubleshooting | [docs/Troubleshooting.md](docs/Troubleshooting.md) |
| Security | [SECURITY.md](SECURITY.md) |

## Summary

1.3.3 is the current stable Direct Model Mode release. Scoped SHA-256 file-version receipts, receipt-chained edits, bounded process execution, RAG provenance, response-envelope budgets, exact project identity, durable continuity sanitization, installer paths, and package hygiene are guarded by automated checks. The optional context compactor remains OFF by default and does not inherit file-mutation authority through compacted history.

Qwen 3.8 27B is the highly recommended primary operating model. Muse Glimmer remains under testing. Qwen 3.5, community Qwen 3.6 27B checkpoints, and GPT-OSS are not currently recommended.

If you want local LLMs for Unreal C++ with less hallucination, select the real model, search evidence first, read the exact project source, then answer or patch. Improve RAG, validation, safety boundaries, and failure analysis first; use fine-tuning later only when the workflow is already measured on real project errors.

---

## ☕ Support This Project

If this project has been useful to you, please consider sponsoring — it helps keep development going.

[![Sponsor on GitHub](https://img.shields.io/badge/Sponsor-%E2%9D%A4-red?logo=github)](https://github.com/sponsors/Parkyongseok1120)

👉 **[https://github.com/sponsors/Parkyongseok1120](https://github.com/sponsors/Parkyongseok1120)**
