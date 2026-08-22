# BYOI Knowledge Setup (UE 5.x)

Build your local RAG index from a licensed Epic UE install. **Never commit** `data/` or `*.sqlite` to git.

## Quick start

```powershell
cd UE5_Local_LLM_MCP_lmstudio
.\scripts\installer_support\Configure-Knowledge.ps1
```

The configuration command selects one installed UE 5.x version, writes the local
workspace configuration, then runs source collection, public-symbol collection,
index build, and `doctor`. Add `-SkipBuild` when you only
want to write the configuration. The script prints the exact supported commands
to run later.

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

## Direct update commands

| Command | Action |
|---------|--------|
| `collect-source -Root <UE root>\Engine\Source` | Re-collect source from one licensed Unreal installation |
| `collect-symbols -Root <UE root>\Engine\Source -Tier public -SymbolScope engine` | Re-collect public engine symbols |
| `build` | Atomically rebuild the configured index |
| `set-project -ProjectFile <path.uproject>` | Select one exact project for Direct RAG calls |
| `refresh -RefreshScope project_source` | Refresh the selected project's source without launching the Editor |
| `doctor` | Report factual configuration, binding, and index health |

## Legal

See [EPIC_NOTICE.md](../EPIC_NOTICE.md). Index chunks are derived from your licensed UE install only.
