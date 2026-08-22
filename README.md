<img width="1920" height="1080" alt="Image" src="https://github.com/user-attachments/assets/cd25e0fe-d6fd-4ea8-be24-d1606bb644aa" />


# UE5_Local_LLM_MCP_lmstudio 1.3.0 RC3

> **GitHub prerelease (not stable/release-ready yet):** product metadata is aligned to **1.3.0 RC3**, while `releaseReady` remains `false` until Windows physical install and the remaining release gates pass. The portable reasoning skill, LM Studio MCP, preset, and Node/Python adapters install through one integrated workflow on Windows and Ubuntu Linux; **Apple Silicon macOS** physical FULL install is **PASS**; **Intel macOS** cannot install LM Studio-based components (custom Codex/Cline-only is allowed). Host **Python 3.10+** is required before `./install.sh` can start. See [1.3.0 RC3 notes](docs/Release_Notes_1_3_0_RC3.md) and [Integrated Installer](docs/Integrated_Installer.md).

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
> **Current product label: 1.3.0 RC3 (prerelease).** RC3 packages the verified recovery state machine, atomic mutation journal, canonical project/build proof, Automation scope hardening, and Windows/POSIX path identity from the post-RC2 Develop line. Apple Silicon physical FULL install is recorded as PASS; Windows physical install is still unverified, so stable distribution remains blocked and `releaseReady` stays false.
>
> The current runtime defaults to **Direct Model Mode**: the selected LLM owns tool choice and sequencing, while the MCP servers provide stable capabilities and enforce filesystem, process, and build safety. The default path has no server-owned task, route, planner, or synthesis gate. The saved v1.2.5 measurements and RC3 control-plane validation remain historical evidence, not a new Direct-mode score. Do not treat RC3 as stable until Windows validation and the remaining release gates close.

## Documentation Hub

<p>
  <a href="docs/Project_Overview.md"><img alt="Project Overview" src="https://img.shields.io/badge/Docs-Project%20Overview-blue?logo=gitbook"></a>
  <a href="docs/Release_Notes_1_3_0_RC3.md"><img alt="1.3.0 RC3 Notes" src="https://img.shields.io/badge/Release-1.3.0%20RC3-orange?logo=github"></a>
  <a href="docs/Model_Measurement_Results.md"><img alt="Model Results" src="https://img.shields.io/badge/Docs-Model%20Results-purple?logo=gitbook"></a>
  <a href="docs/Version_Performance_History.md"><img alt="Version Performance" src="https://img.shields.io/badge/Docs-Version%20Performance-green?logo=gitbook"></a>
</p>

## Latest Results

These are the latest saved **v1.2.5 live-model baselines**. A paired 1.3.0 RC3 live rerun has not been completed yet.

| Model / run | Pass@K | Pass@1 | Artifact |
|---|---:|---:|---|
| Qwen 3.6 27B community fine-tune | 36/36 | 36/36 | `20260709-144441-pass1-target` |
| Qwen 3.5 9B | 35/36 | 33/36 | `20260709-153021-qwen35-9b` |

| Model / run | Live wall-clock time |
|---|---:|
| Qwen 3.6 27B community fine-tune | ~33m 37s |
| Qwen 3.5 9B | ~27m 22s |

<p>
  <a href="docs/Holdout_Case_Difficulty.md"><img alt="Holdout Difficulty" src="https://img.shields.io/badge/Docs-36%20Case%20Difficulty-red?logo=gitbook"></a>
</p>

These are internal UE 5.8 RAG/MCP/UBT workflow measurements, not public standardized model benchmarks.
### Model-size and language caveat

The 9B profile is the current **minimum floor**, not a reliability target. It is still under active stabilization, and tool selection, argument construction, repeated MCP calls, and long edit/build loops can remain unstable even when the MCP server and validation logic are healthy. This is a model-capability limitation, not automatically an MCP bug. The gap between the measured 9B and 27B workflows is large enough that their Pass@1 numbers should not be read as equivalent agent behavior.

For autonomous multi-step Unreal work, prefer a **24B–27B instruction/tool-calling model**. Keep 9B for bounded, short tasks after the target file, symbol, and intended change are already known. For Korean-first use, validate the exact local checkpoint: Qwen3 advertises support for 100+ languages and tool calling, while coding-specialized models such as Devstral Small 2 may be stronger at codebase operations but should not be assumed to have the same Korean fluency. See the [Qwen3 model card](https://huggingface.co/Qwen/Qwen3-30B-A3B) and [Devstral Small 2 model card](https://huggingface.co/mistralai/Devstral-Small-2-24B-Instruct-2512).

Historical RC3 workflow tests showed that deterministic handoffs cannot make a small model retain long evidence chains or produce exact tool calls reliably. That model-side limitation still matters, but the supported runtime no longer inserts the old Python task/route/planning/synthesis transitions. Their source remains only as unsupported historical/evaluation material and is omitted from the portable package.

> `Harness average attempts=0.389` in the best run means many cases were solved by deterministic static autofix before an LLM edit attempt. It is not a general model reasoning-depth metric.

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

The unified installer asks for SAFE, STANDARD, FULL, or CUSTOM. When an Unreal adapter is included, it presents a numbered SAFE/AGENT authority choice and shows the final authority in a confirmation summary. SAFE installs the generic coding-reasoning layer and LM Studio integration without a project adapter. STANDARD adds read-only Unreal adapters. FULL adds the context compactor but remains read-only unless AGENT authority is explicitly confirmed. See [Integrated Installer](docs/Integrated_Installer.md).

### One installer, two platform launchers

`INSTALL.bat` and `install.sh` are thin platform launchers for the same `install.py` implementation. There are no separate SAFE, AGENT, RAG, Cline, or context-compactor installers. Choose those options inside the integrated installer. `installer/` contains bootstrap runtime code and validated manifests; advanced maintenance tools live under `scripts/installer_support/`.

### Direct Model Mode is the default

The normal `unreal-rag` and `unreal-agent` entries are capability providers. The model may search, read, edit, validate, build, or test in the order appropriate for the request. You do **not** start `unreal_task_start`, create a server plan, acquire route authorization, or commit synthesis before using a capability. Read/write containment, optimistic concurrency, command allowlists, explicit delete approval, and SAFE/AGENT authority still apply.

> **Important — select the real LLM as the chat model.**
>
> 1. Load and select the actual instruction/tool-calling model you want to use, such as Qwen, in LM Studio's **model dropdown**.
> 2. Create or open the chat and enable **`codex/unreal-context-compactor`** in that chat's **plugin panel**.
> 3. Keep the actual LLM selected. `unreal-context-compactor` is a chat plugin, not a model or proxy model, and it has no `targetModel` to configure.
> 4. Start Local Server and enable the default `unreal-rag` and `unreal-agent` MCP entries.

The plugin measures pressure with the selected model and compacts only older model-facing chat history when needed. It does not choose the model, change sampling, filter MCP tools, or grant write/build authority. The installer makes the plugin available and pins its revision, but LM Studio does not currently expose durable proof that it is enabled for a particular chat; confirm the chat-level toggle in the plugin panel.

This command verifies the installed plugin's source layout and compiled prediction-loop wiring. It does **not** prove chat-level activation:

```shell
cd lmstudio-context-compactor-plugin
npm run status
```

The context plugin is a continuity aid, not a prerequisite for Direct MCP authority. Cline, CLI, Ollama, custom, and remote clients can use the MCP capability servers without the LM Studio chat plugin.

### Multiple projects and Unreal versions

One MCP installation can serve multiple Unreal projects and installed UE versions. `set_active_project` provides a convenient default, but Direct file, search, edit, log, command, build, and Automation tools accept an exact `.uproject` path or exact discovered project name through their advertised `project`, `projectRoot`, or `hint` field where applicable. A per-call project selector overrides the active project for that call only; it does not create route ownership or retarget another chat.

Build and Automation calls resolve the selected project's engine association and may also accept an explicit `engineRoot`. This allows UE 5.x projects on different engine installations to share the same server. Prefer exact selectors: an ambiguous project name returns an error instead of silently choosing another project.

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
.\rag.ps1 collect-source -Root C:\UE_5.6\Engine\Source
.\rag.ps1 collect-projects -Root C:\Projects\MyGame
.\rag.ps1 collect-symbols -Root C:\Projects\MyGame\Source -SymbolScope project -ProjectName MyGame
.\rag.ps1 build
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

Holdout evals run in fresh, bounded turns. In **long LM Studio chats**, context grows with every tool result, build log, and retry. With the actual LLM selected and `codex/unreal-context-compactor` enabled for the chat, the plugin measures that model's tokenizer budget and replaces only older model-facing history with deterministic factual memory before the hard margin is exhausted. It does not preserve or generate task routes, required-next-tool commands, planner state, or synthesis gates.

| Symptom in LM Studio logs | What to do |
|---|---|
| `request (...) exceeds the available context size (54272)` | Confirm that the actual LLM is selected and `codex/unreal-context-compactor` is enabled in this chat's plugin panel. `npm --prefix lmstudio-context-compactor-plugin run status` verifies installed source/build wiring only. If pressure remains too high, use a larger context or start a new chat with a 5–10-line factual handoff. |
| `failed to restore kv cache` / `cache size limit reached` | Same as above — session memory is saturated. New chat is faster than raising context alone. |
| `Model failed to generate a tool call` after a long edit loop | Stop, summarize changed files + remaining errors, new chat. |
| `js-code-sandbox` appears in logs during Unreal work | Disable it (see Quick Install note above). |

Practical rules for day-to-day Unreal project work:

- **One bounded task per chat** when possible (e.g. “fix these 3 compile errors”, not “implement the whole dev console”).
- **Do not paste full UBT/linker logs** into chat. Use `read_unreal_logs`: `mode=tail` for recent failures, `mode=first_error` to scan from byte zero for the original cause, and `mode=range` with `cursorByte`/`nextCursorByte` for bounded traversal.
- **Header-then-.cpp is normal.** `write_file` on a new header may show advisory `CPP_DEFINITION_MISSING` until the matching `.cpp` is written — that is expected, not a rollback trigger on its own.
- **Avoid invented UE APIs** the model often hallucinates: `UCharacterMovementComponent::DisableGravity()`, `UWorld::GetURL()`, `SpawnActor(..., &FTransform)`, `GEngine->GetWorld()`. Prefer `GravityScale`, `GetMapName()` + `OpenLevel`/`ServerTravel`, `SpawnTransform` by value, and the owning actor/subsystem's `GetWorld()`.
- **Compact tool responses:** `build_unreal_project` returns a one-line summary + up to 40 likely errors + its timestamped `fullLogPath` under `.agent/logs` (not full stdout/stderr). `read_unreal_logs` defaults to the newest bounded tail and exposes whether the source was truncated. The chat plugin retains factual memory such as the latest real user request, observed/modified files, recent tool outcomes, and recent build/test state; it deliberately removes task/route/control/synthesis internals and required-next-tool directives.

Automatic compaction extends a session but cannot shrink an oversized system prompt/tool schema or repair a saturated KV cache. If it cannot restore the hard safety margin, start a fresh chat with a short factual handoff containing the exact project, current request, files already changed, and remaining build/test errors.

Details: [LMStudio_MCP_Tool_Discipline.md](docs/LMStudio_MCP_Tool_Discipline.md), [Troubleshooting.md](docs/Troubleshooting.md).

Full requirements, Mac remote setup, model profiles, and security notes are in [Project_Overview.md](docs/Project_Overview.md).

## More Docs

| Topic | File |
|---|---|
| 1.3.0 RC3 release notes | [docs/Release_Notes_1_3_0_RC3.md](docs/Release_Notes_1_3_0_RC3.md) |
| 1.3.0 RC2 release notes | [docs/Release_Notes_1_3_0_RC2.md](docs/Release_Notes_1_3_0_RC2.md) |
| 1.3.0 Beta5 release notes (was RC2) | [docs/Release_Notes_1_3_0_Beta5.md](docs/Release_Notes_1_3_0_Beta5.md) |
| 1.3.0 Beta4 release notes (was RC1) | [docs/Release_Notes_1_3_0_Beta4.md](docs/Release_Notes_1_3_0_Beta4.md) |
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

1.3.0 RC3 is a GitHub prerelease (`releaseReady` false). The new `v1.3.0-rc3` tag does not rewrite any earlier RC/Beta tag. Legacy Strict transition/recovery behavior, atomic rollback, project proof, installer paths, and release hygiene remain guarded by automated checks; the default Direct entries do not invoke that task workflow. GUI E2E and a new paired live-model score are not claimed.

For narrow UE 5.8 compile-fix work, the current community fine-tuned Qwen 3.6 27B local workflow is strong in live UBT validation (36/36 Pass@K, 36/36 Pass@1, 12/12 multifile Pass@1). Qwen 3.5 9B also has a saved compact-model result (35/36 Pass@K, 33/36 Pass@1). Treat these as internal workflow results, not general model equivalence to Claude or GPT-class systems.

If you want local LLMs for Unreal C++ with less hallucination, select the real model, search evidence first, read the exact project source, then answer or patch. Improve RAG, validation, safety boundaries, and failure analysis first; use fine-tuning later only when the workflow is already measured on real project errors.

---

## ☕ Support This Project

If this project has been useful to you, please consider sponsoring — it helps keep development going.

[![Sponsor on GitHub](https://img.shields.io/badge/Sponsor-%E2%9D%A4-red?logo=github)](https://github.com/sponsors/Parkyongseok1120)

👉 **[https://github.com/sponsors/Parkyongseok1120](https://github.com/sponsors/Parkyongseok1120)**
