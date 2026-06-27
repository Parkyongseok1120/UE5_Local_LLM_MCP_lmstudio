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
    {"id": "bp:BP_Player", "type": "blueprint", "assetPath": "/Game/..."}
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
- Module graph (via PAB modules)

Use for duplicate subsystem detection and architecture review.
