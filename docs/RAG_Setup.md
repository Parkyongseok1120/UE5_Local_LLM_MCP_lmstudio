# RAG Setup — `rag.ps1` Reference

## Execution Policy

If PowerShell blocks `.\rag.ps1` with an execution policy error, keep the system policy unchanged and run it with a per-command bypass:

```powershell
cd "$env:USERPROFILE\Documents\Git\UE5_Local_LLM_MCP_lmstudio"
powershell -NoProfile -ExecutionPolicy Bypass -File .\rag.ps1 doctor
```

## Building the RAG Index

Build a useful local RAG index for your active Unreal project:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\rag.ps1 collect-projects -CopyProjectText
powershell -NoProfile -ExecutionPolicy Bypass -File .\rag.ps1 collect-symbols
powershell -NoProfile -ExecutionPolicy Bypass -File .\rag.ps1 collect-module-graph
powershell -NoProfile -ExecutionPolicy Bypass -File .\rag.ps1 build
powershell -NoProfile -ExecutionPolicy Bypass -File .\rag.ps1 doctor
```

For a minimal guideline-only index:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\rag.ps1 build
```

After rebuilding the index, restart LM Studio MCP servers or restart LM Studio so `unreal-rag` reloads the new `rag.sqlite`.

> When writing docs, issues, or logs, avoid hard-coding a personal Windows username such as `C:\Users\<name>\...`. Prefer `$env:USERPROFILE\...` or `%USERPROFILE%\...`.

## Shader / Material / Blueprint Knowledge

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

Use `-Mode material_analysis` for material node screenshots/parameter inventory and `-Mode blueprint_analysis` for Blueprint variables, functions, nodes, and pins.

## Blueprint Graph Exporter Plugin

For reliable Blueprint node/pin/link analysis, install the editor graph exporter plugin into each Unreal project you want to inspect:

```powershell
.\rag.ps1 pick-project
.\rag.ps1 install-editor-graph-plugin
```

During `INSTALL-SAFE-MODE-BUILD-RAG.bat` and `INSTALL-AGENT-MODE-BUILD-RAG.bat`, the setup asks:

```text
Install Blueprint graph exporter plugin into this active project? [Y/n]
```

Choose `Y` to copy `tools\ue_plugins\LmStudioGraphExporter` into `<YourProject>\Plugins\LmStudioGraphExporter`, enable it in the project's `.uproject`, and build the editor module with UnrealBuildTool when needed. Existing project copies are hash-checked against this repo's plugin source; stale copies are updated automatically by the installer.

**What improves after installing the plugin:**

- Blueprint and AnimBlueprint exports include real graph nodes, pins, and links.
- Local-model answers can verify actual asset wiring instead of guessing from names only.
- Claim validation and `blueprint_analysis` become much better at finding missing events, disconnected pins, and parameter usage.
- The install is per project and portable: it does not modify the Unreal Engine installation.

## One-Command Setup

```powershell
.\installer\INSTALL-SAFE-MODE-BUILD-RAG.bat     # safe (read-only)
.\installer\INSTALL-AGENT-MODE-BUILD-RAG.bat    # agent (writes + UBT)
```
