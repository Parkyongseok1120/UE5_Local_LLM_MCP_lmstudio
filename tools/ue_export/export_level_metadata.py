# Run inside Unreal Editor Python
# export_level_metadata('/Game/Maps', r'C:\export\levels.jsonl')

import json

from export_common import asset_class_name


def export_level_metadata(maps_path: str, out_path: str) -> None:
    import unreal

    registry = unreal.AssetRegistryHelpers.get_asset_registry()
    assets = registry.get_assets_by_path(maps_path, recursive=True)
    rows = []
    for asset in assets:
        cls = asset_class_name(asset)
        if "World" not in cls and "Map" not in cls:
            continue
        path = str(asset.package_name)
        rows.append(
            {
                "map_path": path,
                "asset_type": cls,
                "title": path.rsplit("/", 1)[-1],
            }
        )
    with open(out_path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    unreal.log(f"Exported {len(rows)} level metadata rows to {out_path}")
