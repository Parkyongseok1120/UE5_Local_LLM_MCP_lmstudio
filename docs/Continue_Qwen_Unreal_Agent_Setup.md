# Continue + LM Studio Qwen Unreal Agent Setup (Legacy)

> **Deprecated:** Use **[Cline + Rider](Cline_Rider_Unreal_Agent_Setup.md)** instead.  
> Rider = primary IDE. Cline = MCP agent. This Continue doc remains for migration only.

This workspace ships a local Unreal RAG index and MCP server for Continue/LM Studio agent work.

## What this fixes

- Continue no longer relies on LM Studio `AUTODETECT` alone.
- The model is explicitly marked as `tool_use` capable for Agent mode.
- Sampling is lowered for more deterministic code edits.
- The Unreal RAG MCP server is available to Continue as `unreal-rag`.
- Legacy Continue indexing is re-enabled with `disableIndexing: false`.
- Workspace rules force RAG lookup, minimal patches, no duplicate header/cpp rewrites, and build verification after edits.
- Static Unreal validation now catches additional common local-model mistakes: constructor-only `CreateDefaultSubobject`, constructor `SpawnActor`, missing RPC `_Implementation`, component TimerManager misuse, missing direct includes, missing `FTimerHandle` include, and `NewObject` without an explicit Outer.

## Required IDE behavior

- Use Continue Agent mode for code edits.
- Keep terminal/build tools enabled.
- If Continue asks before terminal commands, approve the Unreal build command.
- If the model edits code but does not run a build, tell it: "Run the build now. Do not summarize until you inspect the output."

## Installed files

- `.continue/rules/01-qwen-unreal-agent-compile-loop.md`
- `config/continue_qwen_unreal_agent.config.yaml`
- `config/continue_continuerc.json`
- `RAG_Project_Guidelines/Unreal_Programming/10_Unreal_Codegen_Accuracy_Gates.md`
- `~/.continue/config.yaml` after installation
- `~/.continue/.continuerc.json` after installation

## Useful commands

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\rag.ps1 lmstudio-models
powershell -NoProfile -ExecutionPolicy Bypass -File .\rag.ps1 test-build-logs
powershell -NoProfile -ExecutionPolicy Bypass -File .\rag.ps1 test-unreal-readiness
```

For a real Unreal project:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\rag.ps1 ubt-feedback `
  -ProjectFile "C:\Path\To\YourProject\YourProject.uproject" `
  -UbtTarget "YourProjectEditor" `
  -Mode compile_fix `
  -Question "Fix the latest compile error with the smallest safe patch."
```
