# Cline + Rider Unreal Direct MCP Setup

This setup supports multiple Unreal versions and multiple projects. Rider is
the interactive C++ IDE; Cline chooses and sequences the bounded Direct MCP
tools. Neither MCP server owns an agent task, plan, route, or final-answer phase.

## 1. Prerequisites

From this repository or the root of an installed portable package:

```powershell
.\rag.ps1 doctor
```

In a development checkout, the additional repository-layout check is
`.\scripts\installer_support\Verify-UnrealMcp.ps1`; portable packages do not
need or ship that development-only verifier. Do not assume a fixed directory
name or Unreal version. LM Studio is required only
if it is the model provider selected in Cline; the MCP servers themselves do not
proxy model inference.

## 2. Rider role

1. Open the target `.uproject` in Rider.
2. Confirm that Rider resolved the intended Unreal engine association/toolchain.
3. Use Rider for normal navigation, interactive builds, debugging, and project
   structure inspection.
4. Keep the MCP shared default aligned with
   `rag.ps1 set-project -ProjectFile C:\path\Game.uproject` or
   `unreal_set_active_project` when a default is useful. For cross-project work,
   prefer an exact project selector on every project-scoped call.

The tooling must not hard-code a particular Unreal version. Project association,
registered engines, or an explicit selector determines the engine used to build.

## 3. Cline MCP setup

Template: [`config/cline_mcp_settings.template.json`](../config/cline_mcp_settings.template.json)

### VS Code + Cline extension

1. Open Cline > MCP Servers > Configure MCP Servers.
2. Add `unreal-rag` and `unreal-agent` from the template.
3. Select any Cline provider/model with reliable tool calling. If using LM
   Studio, configure its local OpenAI-compatible endpoint.
4. Restart Cline after changing the MCP configuration and verify both servers'
   static tool catalogs.

Common Windows settings path:

`%APPDATA%\Code\User\globalStorage\saoudrizwan.claude-dev\settings\cline_mcp_settings.json`

For Cline CLI:

`%USERPROFILE%\.cline\data\settings\cline_mcp_settings.json`

Install helper:

```powershell
python install.py --profile custom --components codex,lmstudio,unreal,cline --cline-settings C:\path\to\cline_mcp_settings.json
```

## 4. Project rules and prompt

Cline reads workspace rules from [`.clinerules`](../.clinerules). Copy equivalent
rules into each Unreal repository where the same patch discipline should apply.
Use [`prompts/cline_unreal_agent_system.md`](../prompts/cline_unreal_agent_system.md)
as the Direct MCP system prompt.
Use the [Direct smoke checklist](Rider_Cline_Smoke_Checklist.md) after installation.

## 5. Direct workflow

```text
unreal_get_active_project (or an exact project selector)
  -> unreal_rag_search / unreal_symbol_lookup
  -> search_files / read_file_range / read_file
  -> replace_in_file with current hash/CAS, or write_file for a new file
  -> optional advisory static_validate_project
  -> Rider Build, or enabled build_unreal_project
  -> inspect current output/logs
  -> model writes the final answer
```

Rules:

- Existing `.h`, `.hpp`, `.cpp`, `.c`, `.cc`, `.cxx`, and `.cs` files are
  patch-only. Re-read after a CAS/hash mismatch instead of overwriting an
  external change.
- Use a unique replacement and exact project-relative path. If it does not
  match, read a narrower range and derive a new patch from current content.
- `static_validate_project` is an advisory diagnostic, not a write/build gate.
- Build does not require a plan, validation token, task session, or synthesis
  checkpoint. MCP build execution still requires its explicit safety enablement.
- A `repeatReceipt` may be echoed only by the same conversation that retained the
  original successful content. Calls without it receive full results.
- Do not call historical workflow-controller, task-recovery, route-ownership,
  or write-gate tools.
- Avoid introducing C++ namespaces unless the target Unreal project requires
  them.
- Do not claim success without the current verification evidence appropriate to
  the request.

| Surface | Responsibility |
|---------|----------------|
| Rider | Interactive C++ editing, UBT builds, debugger, project structure |
| Cline model | Tool selection, sequencing, retry decisions, final answer |
| `unreal-rag` MCP | Project selection, retrieval, symbols, index health/refresh |
| `unreal-agent` MCP | Bounded reads, searches, CAS-safe mutations, validation, builds, logs |
| LM Studio (optional) | Cline model provider only; not an MCP control plane |

## 6. LM Studio Chat

For direct LM Studio chat, use
[`LMStudio_Unreal_Agent_Setup.md`](LMStudio_Unreal_Agent_Setup.md). The chat-level
context-compactor toggle must be enabled in the LM Studio UI for each chat where
compaction is wanted.

## 7. Troubleshooting

| Issue | Fix |
|-------|-----|
| Cline MCP catalog is empty | Rerun the integrated installer with the Cline component, restart Cline, then inspect MCP stderr |
| Wrong project | Pass the exact `.uproject` selector or deliberately update the shared default |
| Slow search | Narrow project/path/query; use lexical-only search if the Direct tool exposes that option |
| CAS/hash mismatch | Re-read current content and create a new bounded patch; do not force overwrite |
| Static validation reports an issue | Treat it as advisory evidence, fix relevant findings, then run the real build |
| Build is disabled | Build in Rider or explicitly enable the documented MCP build switch |
| Model tries a generic sandbox | Cancel it and continue with the bounded MCP file tools |
| A repeated call returns full content | Expected unless the same conversation echoed its valid opaque receipt |

Legacy Continue and workflow-controller documents are migration history, not the
recommended Direct path.
