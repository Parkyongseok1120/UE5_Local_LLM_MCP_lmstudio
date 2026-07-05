# UE5_Local_LLM_MCP_lmstudio

> **Platform: Windows 10/11 only.** All install scripts are PowerShell/BAT. macOS and Linux are not supported.

Local **RAG + MCP stack** for using local LLMs in LM Studio as Unreal Engine 5.x C++ assistants.

## Project Status / 프로젝트 상태

** EN : ** As of June 2026, this project is in active real-world testing and KPI-driven agent improvements, including Pass@K compile-fix, MCP tool-call reliability, and Build.cs write reliability. No further feature updates are expected before the end of August.

** KR : ** 2026년 6월 기준, 이 프로젝트는 실전 테스트와 KPI 기반 에이전트 개선을 진행 중입니다. 주요 개선 항목은 Pass@K compile-fix, MCP tool-call 안정성, Build.cs write 안정성입니다. 8월 말 전까지는 추가 기능 업데이트가 없을 예정입니다.


### Agent trust (internal KPI)

This repo targets a **Sonnet 4.5-oriented Unreal C++ agent workflow**, not a claim that any local model matches cloud Sonnet quality.

- **Pass@K live compile-fix** is the primary agent KPI (core gate **3/3** on **Qwen 3.6 27B**; ceiling suite **13-case dry-run** includes 10 `module_fix` families).
- Single-file C++ fixes score well; **module `Build.cs` fixes are harder** — track with ceiling Pass@K live, not chat-only runs.
- **LM Studio MCP chat** is experimental: enable Essential Tools, session bootstrap, and compact system prompts. Verify with `python scripts/bench_lmstudio_mcp.py`.
- Do not treat chat-only runs as production automation.

## Evaluation Claim Guardrail

Current Tier/KPI numbers are internal UE RAG/MCP/UBT scorecard results, not an external standardized model benchmark. Do not claim that Qwen 27B itself is Sonnet 4-grade.

Safer wording: for UE C++ compile-fix/project-review only, the system has shown practical behavior near upper Sonnet 3.7 to lower Sonnet 4 range inside this validation loop. See [docs/Evaluation_Risk_Register.md](docs/Evaluation_Risk_Register.md) and [docs/Real_Project_Validation_Plan.md](docs/Real_Project_Validation_Plan.md).

The forward target is now a Sonnet 4.5-oriented local Unreal workflow. This is a target, not a current model-grade claim. See [docs/Sonnet45_Target_Plan.md](docs/Sonnet45_Target_Plan.md).

Sonnet 5 is tracked only as a gap-analysis target for future workflow improvements around long-context agentic coding, tool use, retry judgment, and project memory. This does not claim model-grade equivalence; see [docs/Sonnet5_Gap_Plan.md](docs/Sonnet5_Gap_Plan.md).

Compact-model optimization tracks include Qwen 3.5 9B, Qwen3.5-9B-DeepSeek-V4-Flash-GGUF, GPT OSS 20B, and gpt-oss-20b-claude-opus-sonnet-reasoning-i1-GGUF community fine-tunes. See [docs/Model_Profiles.md](docs/Model_Profiles.md).

### Observed Local Model Ranking

This ranking is **not a global model benchmark**. It is the observed behavior inside this repository's Unreal-specific loop:

```text
LM Studio
+ MCP Essential Tools
+ strict project-filtered RAG
+ Unreal symbol / range lookup
+ static validation
+ UBT compile wrapper
```

Within that loop, the current practical ranking is:

| Model profile | Practical behavior in this project | Claude Sonnet rough equivalent |
|---|---|---|
| `qwen3_6_27b` | Best local track for broader Unreal C++ agent work, deeper retrieval, and limited refactor loops. | Upper Sonnet 3.7 to lower Sonnet 4, UE C++ only |
| `qwen3_5_9b` / `qwen3_5_9b_deepseek_v4_flash` | Usually more reliable than base GPT OSS 20B for MCP tool calls, compact patch loops, and compile-fix workflows. | Upper Sonnet 3.5 to mid Sonnet 3.7, narrow UE tasks |
| `gpt_oss_20b` | Useful, but more variable in MCP/JSON/tool-call loops; prefer Qwen 9B or Qwen 27B when available. | Around Sonnet 3.5 for this workflow |

In short: **Qwen 3.5 9B can outperform base GPT OSS 20B in this project** because the agent stack favors models that follow tool-call, patch, symbol-lookup, and validation discipline reliably. This does **not** mean Qwen 9B is generally smarter than every 20B model; it means it is a better fit for this Unreal RAG/MCP/UBT automation loop.

Old name: **Unreal58-RAG**. Officially tested on **UE 5.8**. Other 5.x versions can work, but build your own index from **your** licensed UE install (BYOI).

> **BYOI** = Bring Your Own Index. This repo ships **tooling only**: not Epic source, not a pre-built `rag.sqlite`.

The first goal is to make local models hallucinate less on Unreal C++, especially `Build.cs`, include, UHT, project-specific code, and asset metadata.

```text
Unreal knowledge / API evidence = RAG
Answer tone / format / habits     = LoRA (optional, later)
Workflow (search / files / build) = MCP
```

## Minimum Requirements

### PC

| | Minimum | Recommended |
|---|---|---|
| OS | Windows 10/11 | Windows 11 |
| RAM | 16 GB | 32 GB+ |
| GPU VRAM | 8 GB for 7-9B Q4 | 16 GB+ for 20-27B Q4 |
| Free disk | ~30 GB | 100 GB+ |
| CPU | 6-core modern CPU | 8-core+ |

Also required:

- Python 3.10+ (real install, not the Windows Store stub)
- Node.js 20+
- LM Studio 0.3+
- Licensed Unreal Engine 5.x (5.8 recommended)

RAG-only Q&A is lighter. Agent file-write + UBT compile loop needs UE installed and more headroom.

### Local Model

| | Minimum | Recommended |
|---|---|---|
| Size | 7-9B instruct/coding model | 20-27B coding/reasoning model |
| Examples | Qwen3-8B, Qwen 3.5 9B | Qwen 3.6 27B (GPT OSS 20B: optional, variable MCP stability) |
| Context | 8k | 16k-32k+ |
| Quant | Q4 acceptable | Q4_K_M / Q5_K_M |

Small-model path: [docs/Small_Model_Shortcut.md](docs/Small_Model_Shortcut.md).  
Community fine-tune path: [docs/Community_Finetune_Model_Optimization.md](docs/Community_Finetune_Model_Optimization.md).

Hybrid embedding search (`fastembed`) is optional: `pip install fastembed`.

## Quick Install

```powershell
git clone https://github.com/Parkyongseok1120/UE5_Local_LLM_MCP_lmstudio.git
cd UE5_Local_LLM_MCP_lmstudio
.\installer\INSTALL-SAFE-MODE.bat
.\installer\Configure-Knowledge.ps1
.\rag.ps1 doctor
```

To install and build the RAG index in one step, run one of:

```powershell
.\installer\INSTALL-SAFE-MODE-BUILD-RAG.bat
.\installer\INSTALL-AGENT-MODE-BUILD-RAG.bat
```

Use safe mode first unless you intentionally want MCP file writes, commands, and Unreal builds enabled.

Then in LM Studio:

1. Load your local model and start Local Server.
2. Paste system prompt: `prompts/lmstudio_unreal_agent_system.md`
3. Enable MCP: `unreal-rag` and `unreal-agent`
4. Restart LM Studio if paths do not refresh.

`INSTALL-SAFE-MODE.bat` patches `%USERPROFILE%\.lmstudio\mcp.json` with full paths to Python/Node.

## PowerShell `rag.ps1` Setup

If PowerShell blocks `.\rag.ps1` with an execution policy error, keep the system policy unchanged and run it with a per-command bypass:

```powershell
cd "$env:USERPROFILE\Documents\Git\UE5_Local_LLM_MCP_lmstudio"
powershell -NoProfile -ExecutionPolicy Bypass -File .\rag.ps1 doctor
```

Build a useful local RAG index for your active Unreal project:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\rag.ps1 collect-projects -CopyProjectText
powershell -NoProfile -ExecutionPolicy Bypass -File .\rag.ps1 collect-symbols
powershell -NoProfile -ExecutionPolicy Bypass -File .\rag.ps1 collect-module-graph
powershell -NoProfile -ExecutionPolicy Bypass -File .\rag.ps1 build
powershell -NoProfile -ExecutionPolicy Bypass -File .\rag.ps1 doctor
```

For a minimal guideline-only index, run:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\rag.ps1 build
```

When writing docs, issues, or logs, avoid hard-coding a personal Windows username such as `C:\Users\<name>\...`. Prefer:

```powershell
$env:USERPROFILE\Documents\Git\UE5_Local_LLM_MCP_lmstudio
%USERPROFILE%\Documents\Git\UE5_Local_LLM_MCP_lmstudio
C:\Path\To\YourProject
```

After rebuilding the index, restart LM Studio MCP servers or restart LM Studio so `unreal-rag` reloads the new `rag.sqlite`.

### Shader / Material / Blueprint Knowledge

Project text indexing already includes `.usf` and `.ush` shader files:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\rag.ps1 collect-projects -CopyProjectText
powershell -NoProfile -ExecutionPolicy Bypass -File .\rag.ps1 build
powershell -NoProfile -ExecutionPolicy Bypass -File .\rag.ps1 query -Mode shader -Question "USF USH GlobalShader RenderCore RHI plugin setup"
```

For Material and Blueprint graph analysis, export metadata from Unreal Editor first, then ingest it:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\rag.ps1 collect-material-metadata -Question C:\Path\To\materials.jsonl -ProjectName MyGame
powershell -NoProfile -ExecutionPolicy Bypass -File .\rag.ps1 collect-blueprint-metadata -Question C:\Path\To\blueprints.jsonl -ProjectName MyGame
powershell -NoProfile -ExecutionPolicy Bypass -File .\rag.ps1 build
```

Use `-Mode material_analysis` for material node screenshots/parameter inventory and `-Mode blueprint_analysis` for Blueprint variables, functions, nodes, and pins. Screenshot answers must separate visible facts from guesses.

For reliable Blueprint node/pin/link analysis, install the editor graph exporter plugin into each Unreal project you want to inspect:

```powershell
.\rag.ps1 pick-project
.\rag.ps1 install-editor-graph-plugin
```

During `INSTALL-SAFE-MODE-BUILD-RAG.bat` and `INSTALL-AGENT-MODE-BUILD-RAG.bat`, the setup asks:

```text
Install Blueprint graph exporter plugin into this active project? [Y/n]
```

Choose `Y` to copy `tools\ue_plugins\LmStudioGraphExporter` into `<YourProject>\Plugins\LmStudioGraphExporter`, enable it in the project's `.uproject`, and build the editor module with UnrealBuildTool when needed. Existing project copies are hash-checked against this repo's plugin source; stale copies are updated automatically by the installer. Choose `N` to skip installation; metadata export will still run, but Blueprint graph details can fall back to the limited Python exporter on UE versions where Editor Python cannot read every graph node.

What improves after installing the plugin:

- Blueprint and AnimBlueprint exports include real graph nodes, pins, and links.
- Local-model answers can verify actual asset wiring instead of guessing from names only.
- Claim validation and `blueprint_analysis` become much better at finding missing events, disconnected pins, and parameter usage.
- The install is per project and portable: it does not modify the Unreal Engine installation, and project paths are resolved from the active `.uproject` instead of a hard-coded user folder.

If you prefer double-click / one-command setup, use:

```powershell
.\installer\INSTALL-SAFE-MODE-BUILD-RAG.bat
```

For trusted projects where the agent may write files and run UBT builds:

```powershell
.\installer\INSTALL-AGENT-MODE-BUILD-RAG.bat
```

## Safe vs Agent Mode

Default install is read-only safe mode (`ALLOW_WRITE=0`). Enable file writes and UBT only when you trust the project:

```powershell
.\installer\Enable-AgentMode.ps1
.\installer\Disable-AgentMode.ps1
```

See [docs/Safe_Agent_Mode.md](docs/Safe_Agent_Mode.md) and [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Quick Start

```powershell
.\rag.ps1 collect-source
.\rag.ps1 collect-projects -CopyProjectText
.\rag.ps1 build
.\rag.ps1 query -Question "How do I create a UActorComponent in C++?"
```

`query` hides the full system prompt by default to keep compact-model and console output small. Use `-PrintPrompts` when you need to debug the exact assembled prompt.
If `-Project` is omitted, `query` uses the active `.uproject` from the shared workspace config so mixed-project indexes do not bleed into compact-model answers.
Long LM Studio wrapper retry loops compact old chat turns into a deterministic `Conversation compact summary`; this project-side history compaction is independent from Codex `.codex/config.toml`.

With LM Studio Local Server running:

```powershell
.\rag.ps1 lmstudio-models
.\rag.ps1 ask -Question "Show me a C++ example of attaching a custom Component to an Actor"
```

Extra collection for compile/module/symbol help:

```powershell
.\rag.ps1 collect-symbols
.\rag.ps1 collect-module-graph
.\rag.ps1 collect-project-profile -ProjectsRoot "C:\Path\To\YourProject"
.\rag.ps1 build
```

## Model Profiles

Use `UNREAL_RAG_MODEL_PROFILE` to force a profile:

```powershell
$env:UNREAL_RAG_MODEL_PROFILE = "qwen3_5_9b_deepseek_v4_flash"
python scripts/load_sampling_preset.py --show-profile
```

Common profiles:

- `qwen3_6_27b`
- `qwen3_5_9b`
- `qwen3_5_9b_deepseek_v4_flash`
- `gpt_oss_20b`
- `gpt_oss_20b_claude_opus_sonnet_reasoning_i1`
- `gpt_oss_small`

See [docs/Model_Profiles.md](docs/Model_Profiles.md).

## Important

- Do not commit `data/`, `*.sqlite`, or Epic source exports. See [EPIC_NOTICE.md](EPIC_NOTICE.md).
- Not affiliated with Epic Games, LM Studio, OpenAI, Anthropic, Qwen, or GPT OSS model publishers.
- Codex Python is not bundled. Install Python normally; `rag.ps1` may optionally find a Codex cached runtime if you already have it.

### ⚠️ Files you must never commit

The following files are gitignored and contain machine-specific paths. **Do not force-add them:**

| File | Why |
|---|---|
| `config/workspace.json` | Public placeholder / installer-generated local config; keep real local paths out of commits |
| `config/workspace.local.json` | Optional ignored local override pattern for private machine paths |
| `lmstudio-unreal-agent-mcp/config/agent-mcp.json` | Generated by installer; contains your local paths |
| `PORTABLE_ROOT.txt` | Generated by installer; contains your username and Python path |
| `data/` | RAG indexes; may contain Epic source excerpts |

### ⚠️ Agent mode security

Default install is **read-only safe mode** (`ALLOW_WRITE=0`, `ALLOW_COMMANDS=0`). Enable file writes and UBT only for projects you trust:

```powershell
.\installer\Enable-AgentMode.ps1   # enables writes/builds for active project
.\installer\Disable-AgentMode.ps1  # reverts to read-only
```

When `ALLOW_COMMANDS=1`, the agent can run allowlisted shell commands. Never enable agent mode for untrusted project paths.

Maintainers: run `.\installer\Verify-Oss-Ready.ps1` before publishing a fork.

## More Docs

| Topic | File |
|---|---|
| Architecture & pipeline | [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) |
| Safe vs agent mode | [docs/Safe_Agent_Mode.md](docs/Safe_Agent_Mode.md) |
| Build.cs parser | [docs/Build_Cs_Parser.md](docs/Build_Cs_Parser.md) |
| Project routing | [docs/Project_Routing.md](docs/Project_Routing.md) |
| Error taxonomy | [docs/Error_Taxonomy.md](docs/Error_Taxonomy.md) |
| clangd policy | [docs/Clangd_Policy.md](docs/Clangd_Policy.md) |
| Blueprint metadata | [docs/Blueprint_Metadata.md](docs/Blueprint_Metadata.md) |
| Architecture understanding | [docs/architecture/Architecture_Understanding_Layer.md](docs/architecture/Architecture_Understanding_Layer.md) |
| Asset automation roadmap | [docs/Asset_Automation_Roadmap.md](docs/Asset_Automation_Roadmap.md) |
| Eval harness | [docs/Eval_Harness.md](docs/Eval_Harness.md) |
| Eval risk register | [docs/Evaluation_Risk_Register.md](docs/Evaluation_Risk_Register.md) |
| Real project validation | [docs/Real_Project_Validation_Plan.md](docs/Real_Project_Validation_Plan.md) |
| Sonnet 4.5 target plan | [docs/Sonnet45_Target_Plan.md](docs/Sonnet45_Target_Plan.md) |
| Sonnet 5 gap plan | [docs/Sonnet5_Gap_Plan.md](docs/Sonnet5_Gap_Plan.md) |
| Eval metrics / telemetry | [docs/Eval_Metrics_Sonnet5_Gap.md](docs/Eval_Metrics_Sonnet5_Gap.md) |
| Holdout eval guide | [docs/Holdout_Eval_Guide.md](docs/Holdout_Eval_Guide.md) |
| Live eval checklist | [docs/Live_Eval_Checklist.md](docs/Live_Eval_Checklist.md) |
| Model profiles | [docs/Model_Profiles.md](docs/Model_Profiles.md) |
| Small models | [docs/Small_Model_Shortcut.md](docs/Small_Model_Shortcut.md) |
| Community fine-tune optimization | [docs/Community_Finetune_Model_Optimization.md](docs/Community_Finetune_Model_Optimization.md) |
| Troubleshooting | [docs/Troubleshooting.md](docs/Troubleshooting.md) |
| LM Studio + MCP setup | [docs/LMStudio_Unreal_Agent_Setup.md](docs/LMStudio_Unreal_Agent_Setup.md) |
| Rider + Cline agent | [docs/Cline_Rider_Unreal_Agent_Setup.md](docs/Cline_Rider_Unreal_Agent_Setup.md) |
| BYOI / engine versions | [docs/BYOI_Knowledge_Setup.md](docs/BYOI_Knowledge_Setup.md) |
| Agent MCP details | [lmstudio-unreal-agent-mcp/README.md](lmstudio-unreal-agent-mcp/README.md) |
| Security | [SECURITY.md](SECURITY.md) |

## Summary

Still experimental. Structure may change.

If you want local LLMs for Unreal C++ with less hallucination, search evidence first, then answer or patch. Improve RAG and validation first; use fine-tuning later only when the workflow is already measured on real project errors.
