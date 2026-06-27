# Safe vs Agent Mode

Default install uses **Safe mode** (read-only unreal-agent):

- `ALLOW_WRITE=0`
- `ALLOW_COMMANDS=0`
- `ALLOW_UNREAL_BUILD=0`

Enable agent mode when you trust the project and want file writes + UBT:

```powershell
.\installer\Enable-AgentMode.ps1
# or reinstall with:
.\installer\Install-UnrealMcp.ps1 -EnableAgentMode
```

Disable again:

```powershell
.\installer\Disable-AgentMode.ps1
```

RAG search (`unreal-rag`) remains read-only in both modes.
