# BYOI Knowledge Setup (UE 5.x)

Build your local RAG index from a licensed Epic UE install. **Never commit** `data/` or `*.sqlite` to git.

## Quick start

```powershell
cd Unreal58-RAG
.\installer\Configure-Knowledge.ps1   # pick UE_5.x, sets indexNamespace
.\rag.ps1 update-engine              # collect source + symbols + graph + build
.\rag.ps1 doctor
```

## Engine version policy

| Version | Support |
|---------|---------|
| **5.8** | Official eval / Sonnet-tier gate |
| **5.5+** | Recommended — same workflow via BYOI |
| **5.4** | Best-effort — wizard warns; eval not verified |

## Index namespace

- `5.8` → `data/unreal58/rag.sqlite`
- `5.5` → `data/unreal55/rag.sqlite`

Configure in `config/workspace.json` (from `config/workspace.json.template`).

## Update commands

| Command | Action |
|---------|--------|
| `update-engine` | Re-collect Epic source, symbols (public tier), module graph, rebuild |
| `update-project` | Sync active `.uproject` into index |
| `update-guidelines` | Refresh `RAG_Project_Guidelines` chunks |

## Legal

See [EPIC_NOTICE.md](../EPIC_NOTICE.md). Index chunks are derived from your licensed UE install only.
