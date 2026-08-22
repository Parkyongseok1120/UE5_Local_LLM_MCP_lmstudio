# Indexing tiers

Three indexing tiers control installer and indexing-pipeline behavior. Settings live in
`~/.lmstudio/config/unreal-workspace.json` (`%USERPROFILE%\.lmstudio\config\unreal-workspace.json` on Windows):

- `indexingTier`: `lite` | `standard` | `full`
- `editorExportDir`: optional override; default is `{ActiveProject}/Saved/LmStudioMetadataExports`

The interactive installer opens a native picker before tier selection. Selecting a `.uproject` sets `activeProject` and adds its folder as a search root; selecting a folder adds it to `projectSearchRoots`. Standard and Full builds refresh those project inputs instead of merely rebuilding from old JSONL files.

## Lite

- Project C++ / config **text** (`collect-projects`)
- `.uasset` / `.umap` **paths only**
- Fastest, smallest index

## Standard (recommended)

Everything in Lite, plus:

- **Engine API symbols** (`UCLASS`, modules, public headers)
- **Project C++ symbols** (parsed from active project `Source/`)
- Project profile + architecture brief

## Full

Everything in Standard, plus:

- **Entire UE Engine source text** under `Engine/Source` (`collect-source`)
- Large disk use (multi-GB). Use only when you need deep engine implementation lookup.

When changing from Full to Standard or Lite, the pipeline removes `raw_source.jsonl` before rebuilding. Lite also removes stale engine-symbol and active-project profile inputs so a lower tier cannot silently retain higher-tier data. Legacy `raw_module_graph.jsonl` and module-graph reports are retired inputs and are pruned from every newly committed Direct generation.

## Blueprint / material internals

Binary assets are not parsed from disk. `install.py --build-rag` does not launch
Unreal Editor, run export scripts, install the graph exporter plugin, or mutate a
`.uproject`. Produce Editor metadata explicitly, then ingest the existing exports
with the no-launch command:

```powershell
pwsh -NoProfile -File .\rag.ps1 refresh -RefreshScope editor_metadata
```

Only add `-AllowEditorLaunch` when starting Unreal Editor for that manual refresh
is intentional:

```powershell
pwsh -NoProfile -File .\rag.ps1 refresh -RefreshScope editor_metadata -AllowEditorLaunch
```

## Commands

```powershell
pwsh -NoProfile -File .\rag.ps1 refresh
pwsh -NoProfile -File .\rag.ps1 collect-symbols -Root C:\Projects\MyGame\Source -SymbolScope project -ProjectName MyGame
pwsh -NoProfile -File .\rag.ps1 collect-source -Root C:\UE_5.6\Engine\Source
pwsh -NoProfile -File .\rag.ps1 build-incremental
```

The integrated installer itself is Python-only. On Ubuntu Linux/macOS, `pwsh` is
used only if you choose to run the optional maintenance wrapper:

```sh
python3 install.py --profile standard --yes --build-rag --index-tier standard
pwsh -NoProfile -File ./rag.ps1 refresh -RefreshScope editor_metadata
```

`install.py --build-rag` invokes its managed Python executable directly without a
shell wrapper. Set `UNREAL_ENGINE_ROOT` or use installer `--engine-root` when the
engine is outside the documented host common locations.
