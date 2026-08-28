# Evidence-First Unreal MCP — portable Direct runtime

This package provides a small default RAG server and a separate Unreal project
capability server for LM Studio, Cline, and other MCP clients. It contains no
prebuilt Epic/Unreal source index; build or select an index for your own
projects.

## Install

- Windows: run `INSTALL.bat`.
- Ubuntu Linux or Apple Silicon macOS: run `./install.sh`.
- A clean supported host does not need Python preinstalled: the launcher
  downloads a pinned, SHA-256-verified uv seed and installs managed Python 3.12
  in the user state-home without changing the system-wide Python or PATH.
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

Reads and mutations issue scoped `fileVersionReceipt` values for existing-file
CAS. Every later edit explicitly passes that receipt or a valid raw
`expectedHash`; same-session evidence is never selected automatically. Build
and Automation share one bounded process runner, and
`target=Editor` resolves the selected project's canonical, configured preferred,
or sole discovered custom Editor target without rewriting an explicit non-Editor
target.

Exact project selection supports multiple projects and Unreal versions by
routing RAG to the compatible engine-bound sibling shard; one call does not
merge projects owned by different engine shards. The optional context compactor
retains bounded factual objective/work/file/tool/build continuity but never
becomes a planner, router, tool authority, or completion authority. It is installed
for availability without activating LM Studio's host-owned chat toggle; verify that
toggle is OFF per chat. Enable that single toggle only for a long chat that needs
bounded continuity; handler invocation is the activation boundary.

Qwen 3.8 27B is the highly recommended primary validated model. Its v1.3.2
live E2E run completed long real-project RAG/read/report work without the prior
context truncation. Muse Glimmer is under testing and is not yet a validated
recommendation. Qwen 3.5,
Qwen 3.6 27B, and GPT-OSS references are historical compatibility/evaluation
material, not current recommendations.

The only supported Strict implementation is the separately configured Node
`strict-server.js`. The removed Python task/route/planner controller is not
shipped and `MCP_EXECUTION_MODE` does not switch either Direct entry.

See [Direct tool discipline](docs/LMStudio_MCP_Tool_Discipline.md),
[SAFE/AGENT authority](docs/Safe_Agent_Mode.md), and
[troubleshooting](docs/Troubleshooting.md).

## Portable RAG maintenance

The packaged `rag.ps1` is a bounded collection/index/project/refresh/status
maintenance launcher, not a model or workflow controller.

```powershell
.\rag.ps1 set-project -ProjectFile C:\Projects\MyGame\MyGame.uproject
.\rag.ps1 refresh -RefreshScope project_source
.\rag.ps1 doctor
```

`refresh` defaults to project source and never starts Unreal Editor. Existing
Editor exports can be ingested with `-RefreshScope editor_metadata`; launching
Editor additionally requires the explicit `-AllowEditorLaunch` switch.

See [RAG maintenance](docs/RAG_Setup.md). Rider/Cline users can follow the
[Direct Cline setup](docs/Cline_Rider_Unreal_Agent_Setup.md) and use the shipped
[Direct Cline system prompt](prompts/cline_unreal_agent_system.md).

Report security issues according to [SECURITY.md](SECURITY.md).
