# Blueprint, Material, Animation metadata

1. Export from Unreal Editor:
   - `tools/ue_export/export_blueprint_metadata.py`
   - `tools/ue_export/export_material_metadata.py`
   - `tools/ue_export/export_animation_metadata.py`
2. Convert to RAG JSONL:

```powershell
.\rag.ps1 collect-blueprint-metadata -Question C:\export\blueprints.jsonl -ProjectName MyGame
.\rag.ps1 collect-material-metadata -Question C:\export\materials.jsonl -ProjectName MyGame
.\rag.ps1 collect-animation-metadata -Question C:\export\animation.jsonl -ProjectName MyGame
```

3. Rebuild the index:

```powershell
.\rag.ps1 build-incremental
```

Or use the unified collector:

```powershell
.\rag.ps1 collect-editor-metadata -ProjectName MyGame -Question "C:\export\bp.jsonl:blueprint"
.\rag.ps1 collect-editor-metadata -ProjectName MyGame -Question "C:\export\materials.jsonl:material"
.\rag.ps1 collect-editor-metadata -ProjectName MyGame -Question "C:\export\animation.jsonl:animation"
.\rag.ps1 build-incremental
```

See [Editor_Metadata_Export.md](Editor_Metadata_Export.md) for asset registry and project settings exports.
See [Asset_Automation_Roadmap.md](Asset_Automation_Roadmap.md) for the staged path from metadata to Editor-side `.uasset` mutation.

## Blueprint graph coverage

Blueprint export records best-effort graph, node, and pin summaries:

- parent/generated class
- variables, functions, implemented interfaces
- Ubergraph/function/macro/delegate graphs
- node class/title/name and pin direction/type/link count
- asset dependencies

## Material graph coverage

Material export records best-effort expression and parameter summaries:

- material/material instance class
- parent material
- blend mode and shading model when exposed by the Editor API
- material expressions
- scalar/vector/texture/static switch parameter names
- asset dependencies

## Animation and Sequencer coverage

Animation export records mixed asset metadata and the ingest step splits it into specific RAG sources:

- `unreal_skeletal_mesh_metadata`
- `unreal_anim_blueprint_metadata`
- `unreal_anim_montage_metadata`
- `unreal_animation_metadata`
- `unreal_sequencer_metadata`

The exporter covers SkeletalMesh skeleton/material/physics asset references, AnimBlueprint class/skeleton/graph names, AnimSequence and AnimMontage notifies/sections/slots, and LevelSequence bindings/tracks when those APIs are available.

## Implementation boundary

These exports make BP, Material, SkeletalMesh, AnimBP, Notify, Montage, and Sequencer relationships visible to RAG. Actual node rewiring or `.uasset` mutation must still be executed inside Unreal Editor through Editor Python, Editor Utility, or a dedicated plugin command; the repository-side index gives the agent the map it needs before making those Editor-side changes.
