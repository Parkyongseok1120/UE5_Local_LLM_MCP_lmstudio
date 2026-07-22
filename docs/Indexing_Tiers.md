# Indexing tiers

Three indexing tiers control installer and indexing-pipeline behavior. Settings live in
`~/.lmstudio/config/unreal-workspace.json` (`%USERPROFILE%\.lmstudio\config\unreal-workspace.json` on Windows):

- `indexingTier`: `lite` | `standard` | `full`
- `editorExportDir`: optional override; default is `{ActiveProject}/Saved/LmStudioMetadataExports`
- `autoEditorExport`: run automatic Editor export during indexing (default `true`)

The interactive installer opens a native picker before tier selection. Selecting a `.uproject` sets `activeProject` and adds its folder as a search root; selecting a folder adds it to `projectSearchRoots`. Standard and Full builds refresh those project inputs instead of merely rebuilding from old JSONL files.

## Lite

- Project C++ / config **text** (`collect-projects`)
- `.uasset` / `.umap` **paths only**
- Fastest, smallest index

## Standard (recommended)

Everything in Lite, plus:

- **Engine API symbols** (`UCLASS`, modules, public headers)
- **Project C++ symbols** (parsed from active project `Source/`)
- Module/include graph
- Project profile + architecture brief

## Full

Everything in Standard, plus:

- **Entire UE Engine source text** under `Engine/Source` (`collect-source`)
- Large disk use (multi-GB). Use only when you need deep engine implementation lookup.

When changing from Full to Standard or Lite, the pipeline removes `raw_source.jsonl` before rebuilding. Lite also removes stale symbol, module-graph, and active-project profile inputs so a lower tier cannot silently retain higher-tier data.

## Blueprint / material internals

Binary assets are not parsed from disk. During `INSTALL-*-BUILD-RAG.bat`, the installer can enable automatic Editor export for the selected `.uproject`. The indexing pipeline then exports metadata, ingests JSONL, and rebuilds the index.

Manual re-run:

```powershell
.\rag.ps1 export-editor-metadata
```

## Commands

```powershell
.\rag.ps1 index-full
.\rag.ps1 sync-active-project
.\rag.ps1 collect-symbols
.\rag.ps1 collect-source
scripts\installer_support\Export-EditorMetadata.ps1 -IngestOnly
```

On Linux/macOS, run PowerShell scripts through PowerShell Core, for example:

```sh
pwsh ./scripts/run_index_pipeline.ps1 -Tier standard -WorkspaceRoot "$PWD"
pwsh ./rag.ps1 export-editor-metadata
```

The integrated installer passes its exact `python3` executable into the PowerShell pipeline, so Unix indexing does not fall back to a missing `python` command. Set `UNREAL_ENGINE_ROOT` or use installer `--engine-root` when the engine is outside the documented host common locations.
