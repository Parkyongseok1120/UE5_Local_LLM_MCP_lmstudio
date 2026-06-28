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

```powershell
.\rag.ps1 collect-editor-metadata -ProjectName MyGame -Question "C:\export\bp.jsonl:blueprint"
.\rag.ps1 collect-editor-metadata -ProjectName MyGame -Question "C:\export\materials.jsonl:material"
.\rag.ps1 collect-editor-metadata -ProjectName MyGame -Question "C:\export\animation.jsonl:animation"
.\rag.ps1 build-incremental
```

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
