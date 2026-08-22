# BYOI Knowledge Setup (UE 5.x)

Build your local RAG index from a licensed Epic UE install. **Never commit** local
engine/project index data, raw exports, or `*.sqlite` files. The repository's
already tracked, non-proprietary `data/baseline` fixtures are the explicit
exception; do not add licensed or machine-local content there.

## Quick start

The integrated installer is the supported managed path:

```powershell
python install.py --profile standard --yes --build-rag --index-tier standard --engine-root C:\UE_5.8 --active-project C:\Projects\MyGame\MyGame.uproject
```

It stores generated data at
`<state-home>/indexes/<engine-namespace>/rag.sqlite` (by default,
`~/.evidence-first/indexes/<engine-namespace>/rag.sqlite`). Pass `--state-home`
to choose another managed state root. A deliberately configured nonstandard
external `indexPath` remains external and is not silently moved.

The following source-checkout helper is retained for developers. Unlike the
integrated installer, it writes `config/workspace.json` and a repository-local
`data/<namespace>/rag.sqlite`:

```powershell
cd UE5_Local_LLM_MCP_lmstudio
.\scripts\installer_support\Configure-Knowledge.ps1
```

The configuration command selects one installed UE 5.x version, writes the local
workspace configuration, then runs source collection, public-symbol collection,
index build, and `doctor`. Add `-SkipBuild` when you only
want to write the configuration. The script prints the exact supported commands
to run later.

## Engine and validation boundary

The resolver and automated fixtures exercise numeric and custom Unreal engine
associations, engine discovery, sibling-shard selection, and cross-engine
rejection. These are code/fixture contract results, not physical certification
for every UE 5.x binary/source build and host combination.

- Apple Silicon has a recorded physical FULL-install pass with UE 5.8 and the
  published Editor-export, API-connectivity, and signing/notarization limits.
- Windows has automated installer/Direct fixtures plus a prior native session
  that reached a real UBT invocation; a clean-machine physical installer
  lifecycle is not claimed.
- Ubuntu/Linux is covered by automation and fixtures; no physical install claim
  is made.
- Universal project, plugin, custom-engine, and Editor-runtime compatibility is
  not claimed.

## Index namespace and provenance

- Managed `5.8` → `<state-home>/indexes/unreal58/rag.sqlite`
- Source-checkout helper `5.8` → `data/unreal58/rag.sqlite`

Numeric versions derive namespaces such as `unreal58`; distinct custom engine
associations receive deterministic sibling namespaces. Every committed shard has
a `build_manifest.json` engine binding. The runtime may choose one matching
sibling shard for an exact project, but never merges searches across engines.

Project evidence is owned by canonical `project_root` plus the exact `.uproject`
stem. Same-name clones at different physical roots stay separate. Legacy rows
without that composite provenance are migrated only when prior descriptor/path
evidence proves one owner; ambiguous migration and mixed-engine raw corpora fail
closed.

For the source-checkout helper, configure `config/workspace.json` from
`config/workspace.json.template`. Integrated installs maintain their absolute
managed index path in the installed shared workspace settings.

## Direct update commands

| Command | Action |
|---------|--------|
| `collect-source -Root <UE root>\Engine\Source` | Re-collect source from one licensed Unreal installation |
| `collect-symbols -Root <UE root>\Engine\Source -Tier public -SymbolScope engine` | Re-collect public engine symbols |
| `build` | Atomically rebuild the configured index |
| `set-project -ProjectFile <path.uproject>` | Select one exact project for Direct RAG calls |
| `refresh -RefreshScope project_source` | Refresh the selected project's source without launching the Editor |
| `doctor` | Report factual configuration, binding, and index health |

A project update starts with the exact descriptor:

```powershell
.\rag.ps1 set-project -ProjectFile C:\Projects\MyGame\MyGame.uproject
.\rag.ps1 refresh -RefreshScope project_source
.\rag.ps1 doctor
```

One call may include multiple exact projects only when they all resolve to the
same engine-bound shard and immutable generation. Cross-engine selections fail
instead of producing a merged answer.

## Legal

See [EPIC_NOTICE.md](../EPIC_NOTICE.md). Engine-source chunks must come from your
licensed UE install. The same local index can also contain private project and
documentation evidence, so do not distribute the database.
