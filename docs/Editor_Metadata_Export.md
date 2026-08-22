# Editor Metadata Export

Blueprint-heavy and asset-heavy Unreal projects need Editor exports because C++ and text scans cannot see `.uasset` graph structure by themselves.

> Optional track: Editor export is not required for the base release check. On VRAM-limited machines, skip Editor and ingest pre-exported JSONL. On high-spec machines, run exports in Editor Python when convenient.

## Export scripts

Run these scripts in UE Editor Python or an Editor Utility:

| Script | Output type |
|--------|-------------|
| `tools/ue_export/export_blueprint_metadata.py` | Blueprint graph/variables/functions |
| `tools/ue_export/export_material_metadata.py` | Material/MI/MF/ML/MPC |
| `tools/ue_export/export_texture_metadata.py` | Texture2D/Cube/RenderTarget settings |
| `tools/ue_export/export_mesh_metadata.py` | StaticMesh/GeometryCollection slots & LOD |
| `tools/ue_export/export_world_look_metadata.py` | PostProcess/Sky/Fog/DataLayer |
| `tools/ue_export/export_structured_asset_metadata.py` | DataTable, Niagara, AI, Audio, Input, UI, GAS |
| `tools/ue_export/export_animation_metadata.py` | Anim + PoseAsset/Skeleton/PhysicsAsset/ControlRig/IK |
| `tools/ue_export/export_fmod_metadata.py` | FMOD Event/Bank (when plugin present) |
| `tools/ue_export/export_asset_registry.py` | Asset registry summary |
| `tools/ue_export/export_project_settings.py` | DefaultGame/Engine/Input.ini keys |
| `tools/ue_export/export_level_metadata.py` | Map assets |

Example:

```python
import sys
editor_tools = r'...\tools\ue_export'
sys.path.insert(0, editor_tools)

exec(open(editor_tools + r'\export_blueprint_metadata.py', encoding='utf-8').read())
export_blueprint_metadata('/Game', r'C:\export\bp.jsonl')

exec(open(editor_tools + r'\export_material_metadata.py', encoding='utf-8').read())
export_material_metadata('/Game', r'C:\export\materials.jsonl')

exec(open(editor_tools + r'\export_animation_metadata.py', encoding='utf-8').read())
export_animation_metadata('/Game', r'C:\export\animation.jsonl')
```

## Ingest

When a manual refresh is requested, its export path is resolved from the exact
selected `.uproject`:

- Default export folder: `{ProjectRoot}/Saved/LmStudioMetadataExports`
- Fallback (no active project): `%LOCALAPPDATA%/LmStudio/UnrealMetadataExports`
- Content path: `editorExportContentPath` in `unreal-workspace.json` (default `/Game`)

### Blueprint node/pin exporter plugin

UE 5.8 protects `EdGraph.Nodes` from Python, so full Blueprint node and pin links
require the C++ editor plugin. The integrated installer does not copy or enable
this plugin. If you deliberately opt into that project mutation, copy
`tools\ue_plugins\LmStudioGraphExporter` into `<ProjectRoot>\Plugins` and enable
it in the `.uproject` yourself. Close Unreal Editor before changing the project.

When the plugin is present, `export_blueprint_metadata.py` uses it automatically and exports Blueprint `graphs`, `nodes`, `pins`, and `graph_links`. Without the plugin, the Python fallback still exports parent class, graph names, variables, and dependencies where UE exposes them.

### Manual export and refresh

The integrated installer and `install.py --build-rag` never launch Unreal Editor,
run these exporters, copy the graph plugin, or modify the `.uproject`. Produce
exports deliberately in Editor Python, then ingest existing JSONL without starting
Editor:

```powershell
pwsh -NoProfile -File .\rag.ps1 refresh -RefreshScope editor_metadata
```

Starting Unreal Editor from the maintenance wrapper is a separate, explicit opt-in:

```powershell
pwsh -NoProfile -File .\rag.ps1 refresh -RefreshScope editor_metadata -AllowEditorLaunch
```

Manual Editor Python (optional) can produce a folder-scoped export before the
no-launch ingest:

The aggregate helper is `tools/ue_export/run_all_exports.py`; pass its directory
explicitly so execution does not depend on the Editor process working directory.

```python
editor_tools = r'...\tools\ue_export'
exec(open(editor_tools + r'\run_all_exports.py', encoding='utf-8').read())
run_all_metadata_exports(r'C:\UnrealExports', content_path='/Game', tools_dir=editor_tools)
run_all_metadata_exports(r'C:\UnrealExports', content_path='/Game/06_Environment/BossStage', tools_dir=editor_tools)
export_materials_only(r'C:\UnrealExports', content_path='/Game', tools_dir=editor_tools)
```

Register Editor menu (optional):

```python
editor_tools = r'...\tools\ue_export'
menu_script = editor_tools + r'\register_export_menu.py'
menu_scope = {'__file__': menu_script}
exec(compile(open(menu_script, encoding='utf-8').read(), menu_script, 'exec'), menu_scope)
menu_scope.get('register_lmstudio_export_menu')(r'C:\UnrealExports', content_path='/Game')
```

The default Direct MCP exposes `unreal_rag_refresh` for the same bounded refresh
and `unreal_rag_search` for factual indexed lookup. Its default
`allowEditorLaunch=false` only ingests existing exports; a launch requires both
`scope=editor_metadata|all` and `allowEditorLaunch=true`. The removed editor
workflow, asset-graph, and claim-validation MCP tools are not part of the supported
eight-tool RAG catalog.

## RAG source tags

- `unreal_blueprint_metadata`
- `unreal_material_metadata`
- `unreal_structured_metadata`
- `unreal_animation_metadata`
- `unreal_skeletal_mesh_metadata`
- `unreal_anim_blueprint_metadata`
- `unreal_anim_montage_metadata`
- `unreal_sequencer_metadata`
- `unreal_asset_registry`
- `unreal_project_settings`
- `unreal_level_metadata`

## Asset taxonomy and RAG coverage

Not every Unreal asset type is exported with full graph metadata. Use the production taxonomy to see what is indexed at each tier:

- **Guideline:** `RAG_Project_Guidelines/Unreal_Programming/22_Unreal_Asset_Taxonomy_For_Production_Work.md`
- **Machine-readable map:** `config/unreal_asset_taxonomy.json`
- **Runtime helper:** `scripts/asset_taxonomy.py` (`classify_ue_asset_class`, `taxonomy_text_lines`)

| RAG tier | Typical sources | Graph export today |
|----------|-----------------|-------------------|
| `graph_material` | `unreal_material_metadata` | Material, MI, MaterialFunction, MaterialLayer, MPC |
| `structured_metadata` | `unreal_structured_metadata` | DataTable, Curve, Niagara, AI, SoundCue, Input, PhysicalMaterial |
| `graph_blueprint` | `unreal_blueprint_metadata` | Blueprint classes |
| `graph_animation` | `unreal_animation_metadata` | Skeletal mesh, AnimBP, montage |
| `registry` | `unreal_asset_registry` | Path + class + taxonomy tags (e.g. Material Function, Material Layer, MPC) |
| `path_only` | `unreal_project_asset_path` | Path string only |

`unreal_asset_registry` rows include taxonomy lines (`taxonomy_item`, `rag_coverage`, `work_domain`) from `collect_editor_metadata.py`. Material Layer / Material Function graphs require a fresh manual Editor export followed by an `editor_metadata` refresh after the exporter changes.

The Direct index builder stores these summarized nodes for `unreal_rag_search`.
Direct `.uasset` graph mutation still belongs in Unreal Editor automation, but
the exports provide factual graph evidence before an Editor-side change.
