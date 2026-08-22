# Evidence-First Unreal MCP — portable Direct runtime

This package provides a small default RAG server and a separate Unreal project
capability server for LM Studio, Cline, and other MCP clients. It contains no
prebuilt Epic/Unreal source index; build or select an index for your own
projects.

## Install

- Windows: run `INSTALL.bat`.
- Ubuntu Linux or Apple Silicon macOS: run `./install.sh`.
- Keep the extracted directory at a stable path after installation because the
  MCP configuration launches files from this runtime tree.

The installer defaults to read-only authority. Enable AGENT authority only for
a trusted project and only with the separate risk acknowledgement.

See [the integrated installer guide](docs/Integrated_Installer.md) and
[the LM Studio setup guide](docs/LMStudio_Unreal_Agent_Setup.md).

## Supported MCP surfaces

The installer-managed `unreal-rag` entry launches
`scripts/unreal_rag_direct.py`. Its eight task-free tools cover active-project
selection, factual RAG search/symbol lookup, health/status, synchronous refresh,
and capability discovery.

The installer-managed `unreal-agent` entry launches
`lmstudio-unreal-agent-mcp/src/direct-server.js`. It provides bounded project,
read, log, mutation, static-validation, build, Automation, and command
capabilities under the configured SAFE/AGENT authority.

The only supported Strict implementation is the separately configured Node
`strict-server.js`. The removed Python task/route/planner controller is not
shipped and `MCP_EXECUTION_MODE` does not switch either Direct entry.

See [Direct tool discipline](docs/LMStudio_MCP_Tool_Discipline.md),
[SAFE/AGENT authority](docs/Safe_Agent_Mode.md), and
[troubleshooting](docs/Troubleshooting.md).

## Portable RAG maintenance

The packaged `rag.ps1` is a 10-command collection/index/project/refresh/status
launcher, not a model or workflow controller.

```powershell
.\rag.ps1 set-project -ProjectFile C:\Projects\MyGame\MyGame.uproject
.\rag.ps1 collect-projects -Root C:\Projects\MyGame
.\rag.ps1 collect-symbols -Root C:\Projects\MyGame\Source -SymbolScope project -ProjectName MyGame
.\rag.ps1 collect-module-graph
.\rag.ps1 build
.\rag.ps1 doctor
```

`refresh` defaults to project source and never starts Unreal Editor. Existing
Editor exports can be ingested with `-RefreshScope editor_metadata`; launching
Editor additionally requires the explicit `-AllowEditorLaunch` switch.

See [RAG maintenance](docs/RAG_Setup.md). Rider/Cline users can follow the
[Direct Cline setup](docs/Cline_Rider_Unreal_Agent_Setup.md) and use the shipped
[Direct Cline system prompt](prompts/cline_unreal_agent_system.md).

Report security issues according to [SECURITY.md](SECURITY.md).
