# Run inside Unreal Editor (Python) or as Editor Utility.
# Exports Material and Material Instance metadata to JSONL for RAG indexing.
#
# Usage (Editor Python console):
#   exec(open(r'path/to/tools/ue_export/export_material_metadata.py').read())
#   export_material_metadata('/Game', r'C:\export\materials.jsonl')

import json


def _asset_class_name(asset) -> str:
    if hasattr(asset, "asset_class_path"):
        return str(asset.asset_class_path.asset_name)
    return str(getattr(asset, "asset_class", "") or "")


def _safe_name(value) -> str:
    try:
        return value.get_name()
    except Exception:
        return str(value or "")


def export_material_metadata(content_path: str, out_path: str) -> None:
    import unreal

    registry = unreal.AssetRegistryHelpers.get_asset_registry()
    assets = registry.get_assets_by_path(content_path, recursive=True)
    rows = []
    for asset in assets:
        cls = _asset_class_name(asset)
        if cls not in {"Material", "MaterialInstanceConstant", "MaterialInstance"}:
            continue

        path = str(asset.package_name)
        row = {
            "asset_path": path,
            "asset_type": cls,
            "name": path.rsplit("/", 1)[-1],
        }
        try:
            material = unreal.load_asset(path)
            if material:
                if hasattr(material, "get_editor_property"):
                    parent = material.get_editor_property("parent") if "MaterialInstance" in cls else None
                    if parent:
                        row["parent_material"] = _safe_name(parent)
                dependencies = registry.get_dependencies(asset.package_name)
                if dependencies:
                    row["dependencies"] = [str(dep) for dep in dependencies[:40]]
        except Exception:
            pass
        rows.append(row)

    with open(out_path, "w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    unreal.log(f"Exported {len(rows)} material metadata rows to {out_path}")
