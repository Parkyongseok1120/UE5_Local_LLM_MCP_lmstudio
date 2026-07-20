# Indexing tiers

Three indexing tiers control install and `.\rag.ps1 index-full` behavior. Settings live in
`%USERPROFILE%\.lmstudio\config\unreal-workspace.json`:

- `indexingTier`: `lite` | `standard` | `full`
- `editorExportDir`: optional override; default is `{ActiveProject}/Saved/LmStudioMetadataExports`
- `autoEditorExport`: run automatic Editor export during indexing (default `true`)

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

- **Entire UE Engine source text** under `Engine\Source` (`collect-source`)
- Large disk use (multi-GB). Use only when you need deep engine implementation lookup.

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
