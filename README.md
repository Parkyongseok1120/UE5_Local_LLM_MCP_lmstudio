<img width="1920" height="1080" alt="Image" src="https://github.com/user-attachments/assets/cd25e0fe-d6fd-4ea8-be24-d1606bb644aa" />


# UE5_Local_LLM_MCP_lmstudio 1.2.5

> **Platform: Windows 10/11 only.** All install scripts are PowerShell/BAT. macOS and Linux are not supported.

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

> **Project Status — July 2026**
>
> The initial goal of this project — building a local Unreal Engine agent workflow capable of approaching Claude Sonnet 4-level code assistance — has been substantially achieved. **Latest v1.2.5 (2026-07-09):** multifile holdout fixes landed (`UPROPERTY` return-type drift, callback param expansion), followed by regression hardening for NavigationSystem module routing, editor-runtime boundaries, and UObject lifecycle autofix. Dry-run compile gate **36/36** (`20260709-142052`). Live revalidation **36/36 Pass@K**, **36/36 Pass@1** (`20260709-144441-pass1-target`); multifile tier **12/12 Pass@1**.
>
> v1.2.5 is the final planned minor release in the 1.2 line. Future 1.2.x updates, if any, will be limited to simple bug fixes, documentation corrections, and low-risk stability patches. v1.3.0 development is expected to **start roughly 4 months after v1.2.5** and will focus on separated C++ capability, semantic-refactor, runtime-debug, and negative-control scorecards. **The project itself isn't stopping** — I just need to **focus on university coursework and my graduation project** for now, so development is on a brief pause. I'll wrap that up as quickly as I can and see you again in **v1.3.0**!

## Documentation Hub

<p>
  <a href="docs/Project_Overview.md"><img alt="Project Overview" src="https://img.shields.io/badge/Docs-Project%20Overview-blue?logo=gitbook"></a>
  <a href="docs/Model_Measurement_Results.md"><img alt="Model Results" src="https://img.shields.io/badge/Docs-Model%20Results-purple?logo=gitbook"></a>
  <a href="docs/Version_Performance_History.md"><img alt="Version Performance" src="https://img.shields.io/badge/Docs-Version%20Performance-green?logo=gitbook"></a>
  <a href="docs/Roadmap_1_3_0.md"><img alt="v1.3.0 Roadmap" src="https://img.shields.io/badge/Roadmap-v1.3.0-orange?logo=gitbook"></a>
  <a href="docs/Evaluation_Claim_Guardrail.md"><img alt="Evaluation Guardrail" src="https://img.shields.io/badge/Docs-Evaluation%20Guardrail-lightgrey?logo=gitbook"></a>
</p>

## Latest Results

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

> `Harness average attempts=0.389` in the best run means many cases were solved by deterministic static autofix before an LLM edit attempt. It is not a general model reasoning-depth metric.

> **BYOI** = Bring Your Own Index. This repo ships **tooling only**: not Epic source, not a pre-built `rag.sqlite`.

### OSS clone vs Portable ZIP

| Distribution | Index | Install |
|--------------|-------|---------|
| **GitHub clone (this repo)** | You build `rag.sqlite` locally (`rag.ps1 build`) | [`installer/INSTALL-SAFE-MODE.bat`](installer/INSTALL-SAFE-MODE.bat) |
| **Portable ZIP** | May include a pre-built index (see [`installer/README-PORTABLE.md`](installer/README-PORTABLE.md)) | `INSTALL.bat` inside the ZIP |

See [`docs/VERSIONING.md`](docs/VERSIONING.md) for product vs component version numbers.

## Quick Install

```powershell
git clone https://github.com/Parkyongseok1120/UE5_Local_LLM_MCP_lmstudio.git
cd UE5_Local_LLM_MCP_lmstudio
.\installer\INSTALL-SAFE-MODE.bat
.\installer\Configure-Knowledge.ps1
.\rag.ps1 doctor
```

Then load a model in LM Studio, start Local Server, enable `unreal-rag` / `unreal-agent`, and build your index:

### Rider + Cline (optional)

For JetBrains Rider + [Cline](https://github.com/cline/cline) instead of LM Studio chat:

```powershell
.\installer\Install-UnrealMcp.ps1 -InstallCline
# or agent writes/builds:
.\installer\Install-ClineUnrealMcp.ps1 -All -EnableAgentMode
```

See [Rider_Cline_Smoke_Checklist.md](docs/Rider_Cline_Smoke_Checklist.md) and [cline_unreal_agent_system.md](prompts/cline_unreal_agent_system.md). Default workflow: `unreal_task_start` → plan → edit → Rider Build.

> **Required — disable LM Studio's built-in `js-code-sandbox` (JavaScript/TypeScript Code Sandbox).**  
> In LM Studio, turn off or hide the default **JavaScript/TypeScript Code Sandbox** plugin for Unreal coding chats. That sandbox uses a different working directory and is **not** rooted at your active `.uproject`; letting the model use it for file I/O causes wrong paths, broken edits, and conflicts with `unreal-agent`. Use only `unreal-rag` + `unreal-agent` MCP tools (`read_file`, `replace_in_file`, `write_file` for new files). If auto-approval is enabled, remove `lmstudio/js-code-sandbox:*` from `%USERPROFILE%\.lmstudio\settings.json` `chat.skipToolConfirmationPatterns` and restart LM Studio. Details: [LMStudio_MCP_Tool_Discipline.md](docs/LMStudio_MCP_Tool_Discipline.md).

```powershell
.\rag.ps1 collect-source
.\rag.ps1 collect-projects -CopyProjectText
.\rag.ps1 collect-symbols
.\rag.ps1 collect-module-graph
.\rag.ps1 build
```

Use safe mode first. Enable file writes and UBT only for trusted projects:

```powershell
.\installer\Enable-AgentMode.ps1
.\installer\Disable-AgentMode.ps1
```

Ask a question:

```powershell
.\rag.ps1 lmstudio-models
.\rag.ps1 ask -Question "Show me a C++ example of attaching a custom Component to an Actor"
```

## Real-Use Session Tips

Holdout evals run in fresh, bounded turns. **Long LM Studio chats** are a different problem: context grows with every tool result, build log, and retry until requests fail even when MCP is healthy.

| Symptom in LM Studio logs | What to do |
|---|---|
| `request (...) exceeds the available context size (54272)` | **Start a new chat.** Summarize progress in 5–10 lines first. Do not keep retrying in the same thread. |
| `failed to restore kv cache` / `cache size limit reached` | Same as above — session memory is saturated. New chat is faster than raising context alone. |
| `Model failed to generate a tool call` after a long edit loop | Stop, summarize changed files + remaining errors, new chat. |
| `js-code-sandbox` appears in logs during Unreal work | Disable it (see Quick Install note above). |

Practical rules for day-to-day Unreal project work:

- **One bounded task per chat** when possible (e.g. “fix these 3 compile errors”, not “implement the whole dev console”).
- **Do not paste full UBT/linker logs** into chat. Use `read_unreal_logs` or the log file path; share only the first meaningful error slice.
- **Header-then-.cpp is normal.** `write_file` on a new header may show advisory `CPP_DEFINITION_MISSING` until the matching `.cpp` is written — that is expected, not a rollback trigger on its own.
- **Avoid invented UE APIs** the model often hallucinates: `UCharacterMovementComponent::DisableGravity()`, `UWorld::GetURL()`, `SpawnActor(..., &FTransform)`, `GEngine->GetWorld()`. Prefer `GravityScale`, `GetMapName()` + `OpenLevel`/`ServerTravel`, `SpawnTransform` by value, and the owning actor/subsystem's `GetWorld()`.
- **Compact tool responses (v1.2.5):** `build_unreal_project` returns a one-line summary + up to 40 likely errors + `.agent/logs/latest-build.log` path (not full stdout/stderr). `read_unreal_logs` defaults to the newest log and first error cluster. If context pressure builds up, call `write_session_handoff`, start a fresh chat, and resume from `.agent/handoff/latest.md`.

When a long session hits context/KV-cache limits, **new chat + short turns** is still the most reliable recovery. v1.3.0 will add stronger automatic session budgeting.

Details: [LMStudio_MCP_Tool_Discipline.md](docs/LMStudio_MCP_Tool_Discipline.md), [Troubleshooting.md](docs/Troubleshooting.md).

Full requirements, Mac remote setup, model profiles, and security notes are in [Project_Overview.md](docs/Project_Overview.md).

## More Docs

| Topic | File |
|---|---|
| Detailed project overview | [docs/Project_Overview.md](docs/Project_Overview.md) |
| Model measurement results | [docs/Model_Measurement_Results.md](docs/Model_Measurement_Results.md) |
| Version performance history | [docs/Version_Performance_History.md](docs/Version_Performance_History.md) |
| 36-case holdout difficulty | [docs/Holdout_Case_Difficulty.md](docs/Holdout_Case_Difficulty.md) |
| v1.3.0 roadmap | [docs/Roadmap_1_3_0.md](docs/Roadmap_1_3_0.md) |
| Evaluation claims and guardrails | [docs/Evaluation_Claim_Guardrail.md](docs/Evaluation_Claim_Guardrail.md) |
| Sonnet 5 gap plan | [docs/Sonnet5_Gap_Plan.md](docs/Sonnet5_Gap_Plan.md) |
| Eval metrics / telemetry | [docs/Eval_Metrics_Sonnet5_Gap.md](docs/Eval_Metrics_Sonnet5_Gap.md) |
| Holdout eval guide | [docs/Holdout_Eval_Guide.md](docs/Holdout_Eval_Guide.md) |
| RAG setup reference | [docs/RAG_Setup.md](docs/RAG_Setup.md) |
| Mac mini / Mac Studio remote setup | [docs/Mac_Remote_Setup.md](docs/Mac_Remote_Setup.md) |
| Safe vs agent mode | [docs/Safe_Agent_Mode.md](docs/Safe_Agent_Mode.md) |
| Live eval checklist | [docs/Live_Eval_Checklist.md](docs/Live_Eval_Checklist.md) |
| Model profiles | [docs/Model_Profiles.md](docs/Model_Profiles.md) |
| LM Studio MCP tool discipline | [docs/LMStudio_MCP_Tool_Discipline.md](docs/LMStudio_MCP_Tool_Discipline.md) |
| Troubleshooting | [docs/Troubleshooting.md](docs/Troubleshooting.md) |
| Security | [SECURITY.md](SECURITY.md) |

## Summary

Still experimental, but now measured more tightly.

For narrow UE 5.8 compile-fix work, the current community fine-tuned Qwen 3.6 27B local workflow is strong in live UBT validation (36/36 Pass@K, 36/36 Pass@1, 12/12 multifile Pass@1). Qwen 3.5 9B also has a saved compact-model result (35/36 Pass@K, 33/36 Pass@1). Treat these as internal workflow results, not general model equivalence to Claude or GPT-class systems.

If you want local LLMs for Unreal C++ with less hallucination, search evidence first, then answer or patch. Improve RAG, routing, validation, and failure analysis first; use fine-tuning later only when the workflow is already measured on real project errors.

---

## ☕ Support This Project

If this project has been useful to you, please consider sponsoring — it helps keep development going.

[![Sponsor on GitHub](https://img.shields.io/badge/Sponsor-%E2%9D%A4-red?logo=github)](https://github.com/sponsors/Parkyongseok1120)

👉 **[https://github.com/sponsors/Parkyongseok1120](https://github.com/sponsors/Parkyongseok1120)**
