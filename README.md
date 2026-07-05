# UE5_Local_LLM_MCP_lmstudio 1.2.0

> **Platform: Windows 10/11 only.** All install scripts are PowerShell/BAT. macOS and Linux are not supported.

Local **RAG + MCP stack** for using local LLMs in LM Studio as Unreal Engine 5.x C++ assistants.

## Project Status

> **Project Status — July 2026**
>
> The initial goal of this project — building a local Unreal Engine agent workflow capable of approaching Claude Sonnet 4-level code assistance — has been substantially achieved. The 36-case live UBT holdout benchmark (Pass@K: 36/36, Pass@1: 29/36) reflects where the system currently stands.
>
> Over the next **~4 months**, development will be limited to minor bug fixes and stability improvements due to other commitments. Broader platform support (macOS, Linux) and additional LLM frontends beyond LM Studio (e.g., Ollama, Open WebUI) are on the roadmap but will not receive active development during this period.
>
> I appreciate your patience and understanding.

---

> **프로젝트 현황 — 2026년 7월**
>
> 이 프로젝트의 초기 목표였던 "로컬 환경에서 Claude Sonnet 4에 근접한 수준의 Unreal Engine 에이전트 워크플로우 구축"은 상당 부분 달성되었습니다. 36-case 라이브 UBT 홀드아웃 벤치마크(Pass@K: 36/36, Pass@1: 29/36) 결과가 현재 시스템의 수준을 잘 보여주고 있습니다.
>
> 본업 일정상 향후 **약 4개월간**은 소소한 버그 수정 및 안정화 위주의 업데이트만 이루어질 예정입니다. macOS·Linux 지원과 LM Studio 외 다른 LLM 프론트엔드(Ollama, Open WebUI 등) 연동은 로드맵에 있지만, 이 기간 동안은 적극적인 개발이 어려울 것 같습니다.
>
> 넓은 양해 부탁드립니다.

---

As of 2026-07-06, this project is in active KPI-driven local-agent testing for Unreal Engine 5.8. The current focus is not broad feature expansion, but making the LM Studio + RAG + MCP + UBT loop measurable and stable.

### Latest Internal Live Holdout

The strongest current measured run is the UE 5.8 local 36-case live holdout on:

```text
qwen3.6-27b-heretic-uncensored-finetune-neo-code-di-imatrix-max
```

These are internal workflow results, not a public standardized model benchmark.

| Metric | Latest 36-case live run |
|---|---:|
| Pass@K | 36/36 = 100% |
| Pass@1 | 29/36 = 80.6% |
| Average attempts | 1.25 |
| Attempt histogram | 29 cases at 1 attempt, 5 at 2 attempts, 2 at 3 attempts |
| Same-error repeated | 0 |
| no-op edit cases | 4 |
| wrong-file edit cases | 1 in the full run |
| Build.cs false positive cases | 1 in the full run |

Post-run stabilization added a route-specific guard for runtime/editor boundary fixes. A targeted recheck of `editor_runtime_boundary` kept Pass@K at 1/1 and reduced that case's wrong-file / Build.cs false-positive metrics to 0. The full 36-case suite should be rerun after any scoring claim that depends on this post-run guard.

### Improvement Snapshot

Compared with the earliest saved 5-case live baseline in this repo:

| Metric | Early 5-case live baseline | Latest 36-case live run | Change |
|---|---:|---:|---:|
| Pass@K | 3/5 = 60% | 36/36 = 100% | +40 percentage points |
| Pass@1 | 3/5 = 60% | 29/36 = 80.6% | +20.6 percentage points |
| Average attempts | 2.6 | 1.25 | 51.9% lower |
| Suite size | 5 cases | 36 cases | 7.2x larger |

The biggest remaining weakness is `multifile_refactor`: latest tier result is Pass@1 7/12 = 58.3%, Pass@K 12/12 = 100%, average attempts 1.5. Compile-fix, module dependency, and UHT/reflection cases are much stronger than small multi-file refactor cases.

### Agent Trust

This repo targets a **Sonnet 4.5-oriented Unreal C++ workflow**, but this is a workflow target, not a claim that any local model equals Claude Sonnet. Live UBT validation is the source of truth for compile-fix claims.

- **Pass@K live compile-fix** is the primary agent KPI.
- **Pass@1** is the quality/stability KPI; it matters more for comparing refactor ability.
- **Wrong-file edits**, **Build.cs false positives**, **same-error repeats**, and **no-op edits** are tracked because passing eventually is not enough.
- **LM Studio MCP chat** is experimental: enable Essential Tools, session bootstrap, and compact system prompts. Verify with `python scripts/bench_lmstudio_mcp.py`.
- Do not treat chat-only runs as production automation.

## Evaluation Claim Guardrail

Current Tier/KPI numbers are internal UE RAG/MCP/UBT scorecard results, not external standardized model benchmarks. Do not claim that Qwen, GPT OSS, or any other local model is generally Sonnet-grade.

Safer wording: inside this Unreal-specific validation loop, Qwen 3.6 27B currently behaves like a strong local compile-fix agent with practical results in the lower-to-mid Sonnet 4 workflow band for narrow UE C++ compile-fix tasks, while still below Sonnet 4.5 expectations for Pass@1 multi-file refactor stability.

The forward target remains a Sonnet 4.5-oriented local Unreal workflow. This is a target, not a current model-grade claim. See [docs/Sonnet45_Target_Plan.md](docs/Sonnet45_Target_Plan.md).

Sonnet 5 is tracked only as a gap-analysis target for future workflow improvements around long-context agentic coding, tool use, retry judgment, and project memory. This does not claim Sonnet 5 equivalence; see [docs/Sonnet5_Gap_Plan.md](docs/Sonnet5_Gap_Plan.md).

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

| Model profile | Evidence level | Practical behavior in this project | Claude Sonnet workflow proxy estimate |
|---|---|---|---|
| `qwen3_6_27b` / `qwen3.6-27b-heretic-uncensored-finetune-neo-code-di-imatrix-max` | Measured on 36-case UE 5.8 live holdout | Current primary profile. 36/36 Pass@K, 29/36 Pass@1, avg attempts 1.25. Strong module, UHT, single-file compile-fix behavior. Multi-file refactor still needs retries. | Lower-to-mid Sonnet 4 for narrow UE compile-fix workflow; not Sonnet 4.5 overall because multifile Pass@1 is 58.3%. |
| `qwen3_5_9b_deepseek_v4_flash` | Profiled/observed, not rerun on latest 36-case live suite | Best compact track when VRAM is limited. Usually follows JSON/tool/patch discipline better than base GPT OSS 20B. | Upper Sonnet 3.7-ish for narrow compile-fix loops; below Sonnet 4 for refactor. Needs fresh 36-case live proof. |
| `qwen3_5_9b` | Profiled/observed, not rerun on latest 36-case live suite | Stable compact baseline for Essential Tools, small patch loops, and focused compile-fix tasks. | Mid-to-upper Sonnet 3.7 for narrow UE compile-fix; below Sonnet 4 on multi-file refactor. |
| `qwen3_8b` | Profiled only | Smaller compact fallback. Useful for RAG Q&A and small fixes with strict prompts. | Sonnet 3.5 to lower Sonnet 3.7 for narrow tasks. |
| `gpt_oss_20b_claude_opus_sonnet_reasoning_i1` | Profiled/experimental | Community reasoning fine-tune profile. Better theoretical reasoning budget than base GPT OSS 20B, but still needs stable MCP/JSON verification. | Upper Sonnet 3.7-ish estimate if tool discipline holds; not proven on 36-case live. |
| `gpt_oss_20b` | Profiled/observed variable stability | Useful, but more variable in MCP/JSON/tool-call loops; prefer Qwen 9B or Qwen 27B when available. | Around Sonnet 3.5 for this workflow. |
| `gpt_oss_small` | Profiled only | Lightweight fallback for simple inspect/patch tasks. | Below Sonnet 3.5 for this workflow. |
| `gpt_oss_120b`, `qwen_coder_large`, `generic_large` | Configured, not currently proven in this local 36-case report | Potentially stronger if local hardware can run them well, but this repo has no current 36-case live KPI for them. | Unknown until measured; do not infer quality from parameter count alone. |

In short: **Qwen 3.6 27B is currently the only profile with a saved 36-case UE 5.8 live holdout result in this README.** Qwen 3.5 9B-family models remain valuable because this agent stack rewards tool-call, patch, symbol-lookup, and validation discipline. This does **not** mean a smaller model is generally smarter than a larger model; it means it may fit this Unreal RAG/MCP/UBT automation loop better.

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

Still experimental, but now measured more tightly.

For narrow UE 5.8 compile-fix work, the current Qwen 3.6 27B local workflow is strong in live UBT validation. For multi-file refactor and broader engineering judgment, it still needs better Pass@1 stability and repeated 36-case+ live runs before stronger claims.

If you want local LLMs for Unreal C++ with less hallucination, search evidence first, then answer or patch. Improve RAG, routing, validation, and failure analysis first; use fine-tuning later only when the workflow is already measured on real project errors.
