# Cline + Rider Unreal Agent Setup (UE 5.8)

**Primary IDE:** JetBrains Rider for Unreal C++ editing, UBT builds, and debugging.
**AI agent:** Cline with `unreal-rag` and `unreal-agent` MCP tools.

Use this path when you want Rider to remain the source of truth for project state while Cline performs small, tool-backed edits.

## 1. Prerequisites

```powershell
cd $HOME\.lmstudio\Unreal58-RAG
.\rag.ps1 doctor
.\installer\Verify-UnrealMcp.ps1
```

LM Studio should have a tool-capable local model loaded at `http://localhost:1234/v1`.

## 2. Rider Role

1. Open the target Unreal project in Rider through its `.uproject` or generated solution.
2. Confirm the UE 5.8 toolchain in Rider build settings.
3. Use Rider for normal C++ navigation, build, rebuild, debugging, and project structure inspection.
4. Keep MCP `activeProject` aligned through `.\rag.ps1 pick-project` or the MCP project selection tools.

Rider owns manual IDE confidence. Cline owns RAG-assisted inspection, small patches, and optional agent UBT runs.

## 3. Cline MCP Setup

Template: [`config/cline_mcp_settings.template.json`](../config/cline_mcp_settings.template.json)

### VS Code + Cline Extension

1. Open Cline > MCP Servers > Configure MCP Servers.
2. Add `unreal-rag` and `unreal-agent` from the template.
3. Configure LM Studio as provider: `http://localhost:1234/v1`.
4. Enable tool use.

Common Windows path:

`%APPDATA%\Code\User\globalStorage\saoudrizwan.claude-dev\settings\cline_mcp_settings.json`

### Cline CLI

`%USERPROFILE%\.cline\data\settings\cline_mcp_settings.json`

Install helper:

```powershell
.\installer\Install-ClineUnrealMcp.ps1
```

## 4. Project Rules

Cline reads workspace rules from [`.clinerules`](../.clinerules). Copy or symlink equivalent rules into Unreal game repositories when you want the same patch discipline there.

## 5. Agent Workflow

```text
unreal_agent_session / unreal_rag_search
  -> read_file_range / read_file
  -> replace_in_file for existing files
  -> write_file only for brand-new files
  -> Rider Build or build_unreal_project
  -> read log / read_unreal_logs on failure
```

Rules:

- Existing `.h`, `.hpp`, `.cpp`, `.c`, `.cc`, `.cxx`, and `.cs` files are patch-only.
- Do not use LM Studio `run_javascript`, `js-code-sandbox`, Deno file APIs, Node `fs`, or browser/code-sandbox tools for project file I/O.
- If a replacement does not match, re-read a narrower range and retry `replace_in_file`.
- Do not claim success without Rider build output, UBT output, or an explicit user-provided verification note.

| Surface | Role |
|---------|------|
| Rider | C++ editing, UBT, debugger, project structure |
| Cline | MCP tools, RAG, small patches |
| LM Studio | Local LLM API |

## 6. LM Studio Chat

For direct LM Studio chat, use [`docs/LMStudio_Unreal_Agent_Setup.md`](LMStudio_Unreal_Agent_Setup.md).

## 7. Legacy Continue Setup

Continue setup is kept for migration reference only and is not the recommended path. See [`Continue_Qwen_Unreal_Agent_Setup.md`](Continue_Qwen_Unreal_Agent_Setup.md).

## 8. Troubleshooting

| Issue | Fix |
|-------|-----|
| Cline MCP empty | Run `Install-ClineUnrealMcp.ps1`, then restart LM Studio/Cline |
| Wrong project in RAG | Run `pick-project` or use shared `unreal-workspace.json` |
| Slow search | Use `hybrid=false` on `unreal_rag_search` |
| Validation errors | Fix the reported include/reflection/module issue, then rebuild in Rider |
| Model tries JS sandbox | Cancel the tool call and continue with `unreal-agent` file tools |
