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
- pin default values/default objects when the Editor API exposes them
- function, variable, event, and delegate references when the node exposes them
- asset dependencies

## Material graph coverage

Material export records best-effort expression and parameter summaries:

- material/material instance class
- parent material
- blend mode and shading model when exposed by the Editor API
- material expressions
- scalar/vector/texture/static switch parameter names
- scalar/vector/texture/static switch parameter values when the Editor API exposes them
- asset dependencies

## Shader and screenshot analysis

Project text collection already includes `.usf` and `.ush` files. Use:

```powershell
.\rag.ps1 collect-projects -CopyProjectText
.\rag.ps1 build-incremental
.\rag.ps1 query -Mode shader -Question "GlobalShader usf ush RenderCore RHI plugin setup"
```

For material screenshots, first run the material metadata export when possible, then ask the model to compare the visible screenshot facts with `unreal_material_metadata`:

```powershell
.\rag.ps1 collect-material-metadata -Question C:\export\materials.jsonl -ProjectName MyGame
.\rag.ps1 build-incremental
.\rag.ps1 query -Mode material_analysis -Question "MI_Player material parameters textures static switch"
```

For Blueprint function/variable call analysis:

```powershell
.\rag.ps1 collect-blueprint-metadata -Question C:\export\blueprints.jsonl -ProjectName MyGame
.\rag.ps1 build-incremental
.\rag.ps1 query -Mode blueprint_analysis -Question "BP_Player variables function calls EventGraph pins"
```

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
