# UE5_Local_LLM_MCP_lmstudio 1.2.1

> **Platform: Windows 10/11 only.** All install scripts are PowerShell/BAT. macOS and Linux are not supported.

Local **RAG + MCP stack** for using local LLMs in LM Studio as Unreal Engine 5.x C++ assistants.

---

## ☕ Support This Project

If this project has been useful to you, please consider sponsoring — it helps keep development going.

[![Sponsor on GitHub](https://img.shields.io/badge/Sponsor-%E2%9D%A4-red?logo=github)](https://github.com/sponsors/Parkyongseok1120)

👉 **[https://github.com/sponsors/Parkyongseok1120](https://github.com/sponsors/Parkyongseok1120)**

---

## Project Status

> **Project Status — July 2026**
>
> The initial goal of this project — building a local Unreal Engine agent workflow capable of approaching Claude Sonnet 4-level code assistance — has been substantially achieved. The 36-case live UBT holdout benchmark (Pass@K: 36/36, Pass@1: 29/36) reflects where the system currently stands.
>
> Over the next **~4 months**, development will be limited to minor bug fixes and stability improvements due to academic commitments. Broader platform support (macOS, Linux) and additional LLM frontends beyond LM Studio (e.g., Ollama, Open WebUI) are on the roadmap but will not receive active development during this period.
>
> I appreciate your patience and understanding.

---

> **프로젝트 현황 — 2026년 7월**
>
> 이 프로젝트의 초기 목표였던 "로컬 환경에서 Claude Sonnet 4에 근접한 수준의 Unreal Engine 에이전트 워크플로우 구축"은 상당 부분 달성되었습니다. 36-case 라이브 UBT 홀드아웃 벤치마크(Pass@K: 36/36, Pass@1: 29/36) 결과가 현재 시스템의 수준을 잘 보여주고 있습니다.
>
> 학업 일정상 향후 **약 4개월간**은 소소한 버그 수정 및 안정화 위주의 업데이트만 이루어질 예정입니다. macOS·Linux 지원과 LM Studio 외 다른 LLM 프론트엔드(Ollama, Open WebUI 등) 연동은 로드맵에 있지만, 이 기간 동안은 적극적인 개발이 어려울 것 같습니다.
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

KPI numbers are internal UE RAG/MCP/UBT scorecard results, **not** external standardized model benchmarks. Do not claim any local model is generally Sonnet-grade.

[![Evaluation Claims & Model Ranking](https://img.shields.io/badge/Docs-Evaluation%20Claims%20%26%20Model%20Ranking-blue?logo=gitbook)](docs/Evaluation_Claim_Guardrail.md)

> **BYOI** = Bring Your Own Index. This repo ships **tooling only**: not Epic source, not a pre-built `rag.sqlite`.

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

---

## 🍎 Mac mini / Mac Studio as LLM Server

Run LM Studio + your model on a Mac, Windows PC handles UE / UBT / this project.

**Mac**: Load model in LM Studio → Developer → Local Server → enable **LM Link**. No MCP install needed on Mac.  
**Windows**: Install this project normally, then use `--url http://<MAC_IP>:1234/v1` instead of `localhost`.

```powershell
# verify connection from Windows
Invoke-RestMethod -Uri "http://<MAC_IP>:1234/v1/models"
```

[![Full Mac Setup Guide](https://img.shields.io/badge/Docs-Mac%20Remote%20Setup-blue?logo=gitbook)](docs/Mac_Remote_Setup.md)

## PowerShell `rag.ps1` Setup

```powershell
.\rag.ps1 collect-projects -CopyProjectText
.\rag.ps1 collect-symbols
.\rag.ps1 collect-module-graph
.\rag.ps1 build
.\rag.ps1 doctor
```

If PowerShell blocks the script with an execution policy error, prefix with `powershell -NoProfile -ExecutionPolicy Bypass -File`.  
After rebuilding, restart LM Studio so `unreal-rag` reloads the new `rag.sqlite`.

[![Full RAG Setup Guide](https://img.shields.io/badge/Docs-RAG%20Setup%20Reference-blue?logo=gitbook)](docs/RAG_Setup.md)

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
| Evaluation claims & model ranking | [docs/Evaluation_Claim_Guardrail.md](docs/Evaluation_Claim_Guardrail.md) |
| RAG setup reference | [docs/RAG_Setup.md](docs/RAG_Setup.md) |
| Mac mini / Mac Studio remote setup | [docs/Mac_Remote_Setup.md](docs/Mac_Remote_Setup.md) |
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

---

## ☕ Support This Project

If this project has been useful to you, please consider sponsoring — it helps keep development going.

[![Sponsor on GitHub](https://img.shields.io/badge/Sponsor-%E2%9D%A4-red?logo=github)](https://github.com/sponsors/Parkyongseok1120)

👉 **[https://github.com/sponsors/Parkyongseok1120](https://github.com/sponsors/Parkyongseok1120)**
