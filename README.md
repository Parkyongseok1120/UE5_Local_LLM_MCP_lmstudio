# UE5_Local_LLM_MCP_lmstudio

Local **RAG + MCP stack** for using local LLMs in LM Studio as Unreal Engine 5.x C++ assistants.

Old name: **Unreal58-RAG**. Officially tested on **UE 5.8**. Other 5.x versions can work, but build your own index from **your** licensed UE install (BYOI).

> **BYOI** = Bring Your Own Index. This repo ships **tooling only** — not Epic source, not a pre-built `rag.sqlite`.

This is not trying to become a fully automatic developer. The first goal is simply to make local models hallucinate less on Unreal C++ — especially `Build.cs`, include, UHT, and project-specific stuff.

```text
Unreal knowledge / API evidence = RAG
Answer tone / format / habits     = LoRA (optional, later)
Workflow (search / files / build) = MCP
```

---

## Minimum requirements

### PC (Windows)

| | Minimum | Recommended (what I actually test on) |
|---|---|---|
| **OS** | Windows 10/11 | Windows 11 |
| **RAM** | 16 GB (RAG Q&A + 8B model) | **32 GB+** (UE Editor + 27B + index build) |
| **GPU VRAM** | 8 GB (7–8B Q4) | **16 GB+** (27B Q4, e.g. Qwen 3.6 27B) |
| **Free disk** | ~30 GB (repo + local index) | **100 GB+** (UE 5.8 + projects + index) |
| **CPU** | 6-core modern CPU | 8-core+ (index collect/build is slow otherwise) |

Also required:

- **Python 3.10+** (real install — not the Windows Store stub)
- **Node.js 20+**
- **LM Studio 0.3+**
- **Licensed Unreal Engine 5.x** (5.8 recommended)

RAG-only Q&A is lighter. Agent file-write + UBT compile loop needs UE installed and more headroom.

### Local model (LM Studio)

| | Minimum | Recommended |
|---|---|---|
| **Size** | **7–8B** instruct/coding model | **24–27B** coding model |
| **Example** | Qwen3-8B (Q4) | **Qwen 3.6 27B** (Q4_K_M) |
| **Context** | 8k (tight; use 2-turn flow) | **32k** |
| **Quant** | Q4 acceptable | Q4_K_M / Q5_K_M |

Below ~7B, Unreal C++ codegen and compile-fix quality drops fast in my experience — fine for lookup/Q&A, not great for multi-file agent work.

8B path: see [docs/Small_Model_Shortcut.md](docs/Small_Model_Shortcut.md) (2-turn, hybrid off, smaller RAG budget).

Hybrid embedding search (`fastembed`) is optional — `pip install fastembed` if you want it.

---

## Quick install

Monorepo layout — one clone has both RAG and agent MCP:

```text
UE5_Local_LLM_MCP_lmstudio/
  rag.ps1
  scripts/
  lmstudio-unreal-agent-mcp/
    src/server.js
```

```powershell
git clone https://github.com/Parkyongseok1120/UE5_Local_LLM_MCP_lmstudio.git
cd UE5_Local_LLM_MCP_lmstudio
.\installer\INSTALL.bat
.\installer\Configure-Knowledge.ps1
.\rag.ps1 doctor
```

Then in LM Studio:

1. Load your local model and start Local Server.
2. Paste system prompt: `prompts/lmstudio_unreal_agent_system.md`
3. Enable MCP: **`unreal-rag`** + **`unreal-agent`**
4. Restart LM Studio if paths do not refresh.

`INSTALL.bat` patches `%USERPROFILE%\.lmstudio\mcp.json` with full paths to Python/Node.  
RAG-only chat works with **`unreal-rag` alone**. File edit / UBT build needs **`unreal-agent`** too.

---

## Quick start

```powershell
.\rag.ps1 collect-source
.\rag.ps1 collect-projects -CopyProjectText
.\rag.ps1 build
.\rag.ps1 query -Question "How do I create a UActorComponent in C++?"
```

With LM Studio Local Server running:

```powershell
.\rag.ps1 lmstudio-models
.\rag.ps1 ask -Question "Show me a C++ example of attaching a custom Component to an Actor"
```

Extra collection for compile/module/symbol help (worth it if you do real C++ work):

```powershell
.\rag.ps1 collect-symbols
.\rag.ps1 collect-module-graph
.\rag.ps1 collect-project-profile -ProjectsRoot "C:\Path\To\YourProject"
.\rag.ps1 build
```

Search modes: `codegen`, `compile_fix`, `module_fix`, `reflection_fix`, `api_lookup`, `runtime_debug` — see [docs/LMStudio_Unreal_Agent_Setup.md](docs/LMStudio_Unreal_Agent_Setup.md).

---

## Important

- **Do not commit** `data/`, `*.sqlite`, or Epic source exports. See [EPIC_NOTICE.md](EPIC_NOTICE.md).
- Not affiliated with Epic Games or LM Studio.
- Codex Python is **not** bundled. Install Python normally; `rag.ps1` may optionally find a Codex cached runtime if you already have it.

Maintainers: `.\installer\Verify-Oss-Ready.ps1` before publishing a fork.

---

## More docs

| Topic | File |
|---|---|
| LM Studio + MCP setup | [docs/LMStudio_Unreal_Agent_Setup.md](docs/LMStudio_Unreal_Agent_Setup.md) |
| Rider + Cline agent | [docs/Cline_Rider_Unreal_Agent_Setup.md](docs/Cline_Rider_Unreal_Agent_Setup.md) |
| BYOI / engine versions | [docs/BYOI_Knowledge_Setup.md](docs/BYOI_Knowledge_Setup.md) |
| Small 8B models | [docs/Small_Model_Shortcut.md](docs/Small_Model_Shortcut.md) |
| Agent MCP details | [lmstudio-unreal-agent-mcp/README.md](lmstudio-unreal-agent-mcp/README.md) |
| Security | [SECURITY.md](SECURITY.md) |

---

## Summary

Still experimental. Structure may change.

But if you want local LLMs for Unreal C++ with less hallucination — search evidence first, then answer — this is a workable starting point. Improve RAG first; use LoRA later only for answer style if you need it.
