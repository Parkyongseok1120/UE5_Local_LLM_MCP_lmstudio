# Run inside Unreal Editor Python
# export_asset_registry('/Game', r'C:\export\asset_registry.jsonl')

import json


def export_asset_registry(content_path: str, out_path: str) -> None:
    import unreal

    registry = unreal.AssetRegistryHelpers.get_asset_registry()
    assets = registry.get_assets_by_path(content_path, recursive=True)
    rows = []
    for asset in assets:
        path = str(asset.package_name)
        cls = str(asset.asset_class_path.asset_name) if hasattr(asset, "asset_class_path") else ""
        tags = {}
        try:
            tag_list = registry.get_asset_by_object_path(asset.object_path).get_tag_value("GeneratedClass")
            if tag_list:
                tags["generated_class"] = str(tag_list)
        except Exception:
            pass
        rows.append(
            {
                "asset_path": path,
                "asset_type": cls,
                "tags": tags,
            }
        )
    with open(out_path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    unreal.log(f"Exported {len(rows)} asset registry rows to {out_path}")
