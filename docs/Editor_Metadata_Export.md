# Editor Metadata Export

Blueprint-heavy projects need Editor exports — regex on C++ alone is not enough.

> **Optional track:** Editor export is **not** required for claim 9.0. On VRAM-limited machines, skip Editor and ingest pre-exported JSONL. On high-spec machines, run exports in Editor Python when convenient.

## Export scripts (run in UE Editor Python — manual, optional)

| Script | Output type |
|--------|-------------|
| `tools/ue_export/export_blueprint_metadata.py` | Blueprint parent, generated class |
| `tools/ue_export/export_material_metadata.py` | Material/Material Instance parent and dependencies |
| `tools/ue_export/export_asset_registry.py` | Asset registry summary |
| `tools/ue_export/export_project_settings.py` | DefaultGame/Engine/Input.ini keys |
| `tools/ue_export/export_level_metadata.py` | Map assets |

Example:

```python
exec(open(r'...\export_blueprint_metadata.py').read())
export_blueprint_metadata('/Game', r'C:\export\bp.jsonl')

exec(open(r'...\export_material_metadata.py').read())
export_material_metadata('/Game', r'C:\export\materials.jsonl')
```

## Ingest

```powershell
.\rag.ps1 collect-editor-metadata -ProjectName MyGame -Question "C:\export\bp.jsonl:blueprint"
.\rag.ps1 collect-editor-metadata -ProjectName MyGame -Question "C:\export\materials.jsonl:material"
.\rag.ps1 build-incremental
```

## RAG source tags

- `unreal_blueprint_metadata`
- `unreal_material_metadata`
- `unreal_asset_registry`
- `unreal_project_settings`
- `unreal_level_metadata`

Graph builder and PAB consume summarized nodes. Full Blueprint graph parsing is **not** in scope.
