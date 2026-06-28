# Run inside Unreal Editor (Python) or as Editor Utility.
# Exports minimal Blueprint metadata to JSONL for RAG indexing.
#
# Usage (Editor Python console):
#   exec(open(r'path/to/tools/ue_export/export_blueprint_metadata.py').read())
#   export_blueprint_metadata('/Game', r'C:\export\blueprints.jsonl')

import json


def _safe_name(value) -> str:
    try:
        return value.get_name()
    except Exception:
        return str(value or "")


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
                gen_class = None
                parent = None
                if hasattr(bp, "generated_class"):
                    gen_class = bp.generated_class
                elif hasattr(bp, "get_editor_property"):
                    gen_class = bp.get_editor_property("generated_class")
                if gen_class:
                    row["generated_class"] = _safe_name(gen_class)
                    if hasattr(gen_class, "get_super_class"):
                        parent = gen_class.get_super_class()
                if not parent and hasattr(bp, "parent_class"):
                    parent = bp.parent_class
                if not parent and hasattr(bp, "get_editor_property"):
                    try:
                        parent = bp.get_editor_property("parent_class")
                    except Exception:
                        parent = None
                if parent:
                    row["parent_class"] = _safe_name(parent)
                dependencies = asset_registry.get_dependencies(asset.package_name)
                if dependencies:
                    row["dependencies"] = [str(dep) for dep in dependencies[:40]]
        except Exception:
            pass
        rows.append(row)
    with open(out_path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    unreal.log(f"Exported {len(rows)} blueprint metadata rows to {out_path}")
