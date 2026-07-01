# Editor Metadata Export

Blueprint-heavy and asset-heavy Unreal projects need Editor exports because C++ and text scans cannot see `.uasset` graph structure by themselves.

> Optional track: Editor export is not required for the base release check. On VRAM-limited machines, skip Editor and ingest pre-exported JSONL. On high-spec machines, run exports in Editor Python when convenient.

## Export scripts

Run these scripts in UE Editor Python or an Editor Utility:

| Script | Output type |
|--------|-------------|
| `tools/ue_export/export_blueprint_metadata.py` | Blueprint class, variables, functions, graph/node/pin summary, dependencies |
| `tools/ue_export/export_material_metadata.py` | Material/Material Instance parent, expressions, parameters, dependencies |
| `tools/ue_export/export_animation_metadata.py` | SkeletalMesh, AnimBlueprint, AnimSequence, AnimMontage, Notify, LevelSequence metadata |
| `tools/ue_export/export_asset_registry.py` | Asset registry summary |
| `tools/ue_export/export_project_settings.py` | DefaultGame/Engine/Input.ini keys |
| `tools/ue_export/export_level_metadata.py` | Map assets |

Example:

```python
exec(open(r'...\export_blueprint_metadata.py').read())
export_blueprint_metadata('/Game', r'C:\export\bp.jsonl')

exec(open(r'...\export_material_metadata.py').read())
export_material_metadata('/Game', r'C:\export\materials.jsonl')

exec(open(r'...\export_animation_metadata.py').read())
export_animation_metadata('/Game', r'C:\export\animation.jsonl')
```

## Ingest

Export paths are resolved automatically from the active `.uproject`:

- Default export folder: `{ProjectRoot}/Saved/LmStudioMetadataExports`
- Fallback (no active project): `%LOCALAPPDATA%/LmStudio/UnrealMetadataExports`
- Content path: `editorExportContentPath` in `unreal-workspace.json` (default `/Game`)

### Blueprint node/pin exporter plugin

UE 5.8 protects `EdGraph.Nodes` from Python, so full Blueprint node and pin links require the C++ editor plugin. Install it once per project:

```powershell
.\rag.ps1 install-editor-graph-plugin
```

Or install for an explicit project:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\installer\Install-EditorGraphPlugin.ps1 -ProjectFile C:\Path\Game.uproject
```

The installer copies `tools\ue_plugins\LmStudioGraphExporter` into `<ProjectRoot>\Plugins`, enables it in the `.uproject`, hash-checks existing project plugin copies, updates stale copies, and runs UnrealBuildTool when the module needs compiling. Close Unreal Editor before installing. Then run:

```powershell
.\rag.ps1 export-editor-metadata
```

When the plugin is present, `export_blueprint_metadata.py` uses it automatically and exports Blueprint `graphs`, `nodes`, `pins`, and `graph_links`. Without the plugin, the Python fallback still exports parent class, graph names, variables, and dependencies where UE exposes them.

### Install / indexing (automatic)

During `INSTALL-*-BUILD-RAG.bat`, after you pick a project, the installer asks whether to enable automatic Editor export and then asks whether to install the Blueprint graph exporter plugin. If you answer `N`, plugin installation is skipped and Blueprint export uses the limited Python fallback.

If automatic export is enabled, the indexing pipeline runs:

1. Unreal Editor export (headless or live Editor watcher)
2. JSONL ingest
3. Index rebuild

You can re-run manually:

```powershell
.\rag.ps1 export-editor-metadata
```

Folder-scoped export:

```powershell
.\rag.ps1 export-editor-metadata -Question "/Game/06_Environment/BossStage"
```

Sync only (auto-export when stale):

```powershell
.\rag.ps1 sync-editor-metadata
```

Watch the active project and refresh after source/config or Content asset changes:

```powershell
.\rag.ps1 watch-active-project
```

Legacy manual Editor Python (optional):

```python
exec(open(r'...\run_all_exports.py', encoding='utf-8').read())
run_all_metadata_exports(r'C:\UnrealExports', content_path='/Game')
run_all_metadata_exports(r'C:\UnrealExports', content_path='/Game/06_Environment/BossStage')
export_materials_only(r'C:\UnrealExports', content_path='/Game')
```

Register Editor menu (optional):

```python
exec(open(r'...\register_export_menu.py', encoding='utf-8').read())
register_lmstudio_export_menu(r'C:\UnrealExports', content_path='/Game')
```

MCP tools for agents:

- `unreal_editor_metadata_status` — freshness vs project uassets
- `unreal_sync_editor_metadata` — ingest export dir + rebuild index
- `unreal_asset_graph_lookup` — lookup any material/blueprint by path or name
- `unreal_material_claim_validate` / `unreal_blueprint_claim_validate` — verify wire/pin claims

Legacy per-file ingest:

Convenience commands:

```powershell
.\rag.ps1 collect-blueprint-metadata -Question C:\export\bp.jsonl -ProjectName MyGame
.\rag.ps1 collect-material-metadata -Question C:\export\materials.jsonl -ProjectName MyGame
.\rag.ps1 collect-animation-metadata -Question C:\export\animation.jsonl -ProjectName MyGame
```

## RAG source tags

- `unreal_blueprint_metadata`
- `unreal_material_metadata`
- `unreal_animation_metadata`
- `unreal_skeletal_mesh_metadata`
- `unreal_anim_blueprint_metadata`
- `unreal_anim_montage_metadata`
- `unreal_sequencer_metadata`
- `unreal_asset_registry`
- `unreal_project_settings`
- `unreal_level_metadata`

Graph builder and project-aware behavior consume summarized nodes. Direct `.uasset` graph mutation still belongs in Unreal Editor automation, but these exports give the agent the asset map required before making those Editor-side changes.
