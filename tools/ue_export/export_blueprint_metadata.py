# Run inside Unreal Editor (Python) or as Editor Utility.
# Exports minimal Blueprint metadata to JSONL for RAG indexing.
#
# Usage (Editor Python console):
#   exec(open(r'path/to/tools/ue_export/export_blueprint_metadata.py').read())
#   export_blueprint_metadata('/Game', r'C:\export\blueprints.jsonl')

import json


def export_blueprint_metadata(content_path: str, out_path: str) -> None:
    import unreal

    asset_registry = unreal.AssetRegistryHelpers.get_asset_registry()
    assets = asset_registry.get_assets_by_path(content_path, recursive=True)
    rows = []
    for asset in assets:
        cls = str(asset.asset_class_path.asset_name) if hasattr(asset, "asset_class_path") else ""
        if "Blueprint" not in cls and "Widget" not in cls:
            continue
        path = str(asset.package_name)
        row = {
            "asset_path": path,
            "asset_type": cls,
            "generated_class": path.rsplit("/", 1)[-1],
        }
        try:
            bp = unreal.load_asset(path)
            if bp:
                gen_class = bp.get_class().get_name()
                row["generated_class"] = gen_class
                parent = bp.get_class().get_super_class().get_name() if hasattr(bp.get_class(), "get_super_class") else ""
                if parent:
                    row["parent_class"] = parent
        except Exception:
            pass
        rows.append(row)
    with open(out_path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    unreal.log(f"Exported {len(rows)} blueprint metadata rows to {out_path}")
