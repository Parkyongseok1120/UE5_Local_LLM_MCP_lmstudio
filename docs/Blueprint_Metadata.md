# Blueprint metadata

1. Export from Unreal Editor: `tools/ue_export/export_blueprint_metadata.py`
2. Convert to RAG JSONL:

```powershell
.\rag.ps1 collect-blueprint-metadata -Question C:\export\blueprints.jsonl -ProjectName MyGame
```

3. Rebuild index to include `unreal_blueprint_metadata` source.

Or use the unified collector:

```powershell
.\rag.ps1 collect-editor-metadata -ProjectName MyGame -Question "C:\export\bp.jsonl:blueprint"
.\rag.ps1 build-incremental
```

See [Editor_Metadata_Export.md](Editor_Metadata_Export.md) for asset registry and project settings exports.

Limitations: metadata only — no Blueprint graph parsing in v1.

## Material metadata companion

Material and Material Instance work should ingest editor metadata too:

```powershell
.\rag.ps1 collect-material-metadata -Question C:\export\materials.jsonl -ProjectName MyGame
.\rag.ps1 build-incremental
```

This adds `unreal_material_metadata` chunks for parent material and dependency lookup.
