# Project Graph Format

Built by:

```powershell
.\rag.ps1 build-project-graph -ProjectFile C:\path\Game.uproject
```

Output:

- `data/unreal_projects/{Name}_project_graph.json`
- `data/unreal_projects/{Name}_project_graph.jsonl` (single-line ingest stub)

## JSON schema

```json
{
  "project": "GameName",
  "projectRoot": "C:/...",
  "generatedAt": "ISO8601",
  "nodes": [
    {"id": "class:UMyActor", "type": "class", "path": "Source/..."},
    {"id": "bp:BP_Player", "type": "blueprint", "assetPath": "/Game/..."},
    {"id": "material:/Game/Materials/MI_Player", "type": "material", "assetPath": "/Game/Materials/MI_Player"},
    {"id": "anim_blueprint:/Game/Characters/ABP_Player", "type": "anim_blueprint", "assetPath": "/Game/Characters/ABP_Player"},
    {"id": "sequencer:/Game/Cinematics/LS_Intro", "type": "sequencer", "assetPath": "/Game/Cinematics/LS_Intro"}
  ],
  "edges": [
    {"from": "class:UMyActor", "to": "class:AActor", "kind": "inherits"},
    {"from": "module:Game", "to": "module:Engine", "kind": "depends_on"}
  ],
  "summary": {"nodeCount": 0, "edgeCount": 0}
}
```

## MCP query

`unreal_project_graph_query` with optional `nodeType`, `nameContains`, `projectName`.

## Sources merged

- PAB (`project_architecture.json`)
- Blueprint metadata (`raw_blueprint_metadata.jsonl`)
- Material metadata (`raw_material_metadata.jsonl`)
- Animation metadata (`raw_animation_metadata.jsonl`)
- SkeletalMesh metadata (`raw_skeletal_mesh_metadata.jsonl`)
- AnimBlueprint metadata (`raw_anim_blueprint_metadata.jsonl`)
- AnimMontage metadata (`raw_anim_montage_metadata.jsonl`)
- Sequencer metadata (`raw_sequencer_metadata.jsonl`)
- Module graph (via PAB modules)

Use for duplicate subsystem detection and architecture review.
