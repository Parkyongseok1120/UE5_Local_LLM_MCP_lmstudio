# Safe vs Agent Mode

Default install uses **Safe mode** (read-only unreal-agent):

- `ALLOW_WRITE=0`
- `ALLOW_COMMANDS=0`
- `ALLOW_UNREAL_BUILD=0`

Enable agent mode when you trust the project and want file writes + UBT:

```powershell
python install.py --profile standard --yes --enable-agent-mode --accept-agent-risk
```

Return to SAFE authority by rerunning the same integrated installer without the agent flags:

```powershell
python install.py --profile standard --yes
```

RAG search (`unreal-rag`) remains read-only in both modes.
