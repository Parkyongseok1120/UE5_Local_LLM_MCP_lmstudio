# Safe vs Agent Mode

> **CURRENT DIRECT AUTHORITY SETTING.** Safe/Agent selects concrete process permissions; it is not a task workflow, planner mode, or Strict lifecycle. In Direct Mode these flags gate capabilities without requiring a task, route, checkpoint, static-validation certificate, or compactor state. The sole supported Strict lifecycle is the optional Node `strict_begin` entry; the old Python controller is unsupported and not packaged.

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
