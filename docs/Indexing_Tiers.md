# Indexing tiers

Three indexing tiers control installer and indexing-pipeline behavior. Shared
selection settings live in `~/.lmstudio/config/unreal-workspace.json`
(`%USERPROFILE%\.lmstudio\config\unreal-workspace.json` on Windows):

- `indexingTier`: `lite` | `standard` | `full`
- `editorExportDir`: compatibility override for standalone/manual helpers; Direct
  refresh always uses `{ExactProjectRoot}/Saved/LmStudioMetadataExports`

Generated index data does not live beside that settings file. Integrated installs
default to `<state-home>/indexes/<namespace>/rag.sqlite`, where the default state
home is `~/.evidence-first` and `--state-home` can relocate it. A user-supplied
nonstandard external `indexPath` remains user-managed instead of being silently
rewritten.

The interactive installer opens a native picker before tier selection. Selecting
an exact `.uproject` sets `activeProject` and adds its folder as a search root;
selecting a folder adds discovery input to `projectSearchRoots`. Before a Standard
or Full build, discovery is resolved to a frozen set of exact existing
descriptors. The active descriptor cannot be silently excluded, and every project
in one build must resolve to the selected engine binding. Incompatible projects
are excluded with reported reasons instead of being mixed into the shard.

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

## Ownership and shard invariants

Every project row carries the canonical descriptor parent as `project_root` and
the descriptor stem as `project`. Those two fields form the owner identity, so
same-name clones at different roots remain isolated. Legacy rows are upgraded
only when path containment or a prior descriptor inventory proves one unique
owner; ambiguous or incomplete legacy data fails provenance validation.

Each committed generation also records its engine binding. Versioned or custom
engine associations use separate sibling namespaces under the managed indexes
root. The runtime can switch to one matching sibling shard for an exact project,
but it never performs a cross-engine merged call. All exact project selectors in
one call must resolve to the same engine-bound shard and immutable generation.

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
pwsh -NoProfile -File .\rag.ps1 set-project -ProjectFile C:\Projects\MyGame\MyGame.uproject
pwsh -NoProfile -File .\rag.ps1 refresh
pwsh -NoProfile -File .\rag.ps1 collect-symbols -Root C:\UE_5.6\Engine\Source -Tier public -SymbolScope engine
pwsh -NoProfile -File .\rag.ps1 collect-source -Root C:\UE_5.6\Engine\Source
pwsh -NoProfile -File .\rag.ps1 build-incremental
pwsh -NoProfile -File .\rag.ps1 doctor
```

Use `set-project` plus `refresh -RefreshScope project_source` for project C++ and
`Build.cs` changes. A low-level project-symbol invocation must provide both the
project name and project root; the packaged launcher does not expose an unsafe
name-only shortcut.

The integrated installer itself is Python-only. On Ubuntu Linux/macOS, `pwsh` is
used only if you choose to run the optional maintenance wrapper:

```sh
python3 install.py --profile standard --yes --build-rag --index-tier standard
pwsh -NoProfile -File ./rag.ps1 refresh -RefreshScope editor_metadata
```

`install.py --build-rag` invokes its managed Python executable directly without a
shell wrapper. Set `UNREAL_ENGINE_ROOT` or use installer `--engine-root` when the
engine is outside the documented host common locations.

## Validation boundary

The Windows, Ubuntu, and macOS automation exercises installer, collector, path,
and shard contracts with controlled fixtures. That is not the same as physical
certification of every host, engine build, project, plugin, and Editor runtime.
The recorded physical FULL-install pass is Apple Silicon with UE 5.8, subject to
the documented Editor-export, LM Studio API-connectivity, and
signing/notarization limitations. A prior native Windows session reached real RAG,
MCP, and UBT activity, but is not a clean-machine installer-lifecycle proof. No
physical Linux install claim or universal compatibility claim is made.
