# Run inside Unreal Editor (Python) or as Editor Utility.
# Exports Material and Material Instance metadata, including best-effort graph
# expression summaries, to JSONL for RAG indexing.
#
# Usage (Editor Python console):
#   exec(open(r'path/to/tools/ue_export/export_material_metadata.py').read())
#   export_material_metadata('/Game', r'C:\export\materials.jsonl')

import json


MAX_EXPRESSIONS = 320


def _asset_class_name(asset) -> str:
    if hasattr(asset, "asset_class_path"):
        return str(asset.asset_class_path.asset_name)
    return str(getattr(asset, "asset_class", "") or "")


def _safe_name(value) -> str:
    try:
        return value.get_name()
    except Exception:
        return str(value or "")


def _safe_prop(obj, prop: str, default=None):
    try:
        if hasattr(obj, "get_editor_property"):
            return obj.get_editor_property(prop)
        return getattr(obj, prop, default)
    except Exception:
        return default


def _collect_parameter_names(unreal, material, kind: str) -> list[str]:
    library = getattr(unreal, "MaterialEditingLibrary", None)
    if not library:
        return []
    function_name = {
        "scalar": "get_scalar_parameter_names",
        "vector": "get_vector_parameter_names",
        "texture": "get_texture_parameter_names",
        "static_switch": "get_static_switch_parameter_names",
    }.get(kind)
    if not function_name or not hasattr(library, function_name):
        return []
    try:
        return [str(value) for value in getattr(library, function_name)(material)]
    except Exception:
        return []


def _collect_material_expressions(material) -> list[dict]:
    expressions = _safe_prop(material, "expressions", []) or []
    rows = []
    for expression in list(expressions)[:MAX_EXPRESSIONS]:
        inputs = []
        for prop in ("inputs", "material_expression_editor_x", "material_expression_editor_y"):
            value = _safe_prop(expression, prop, None)
            if prop == "inputs" and value:
                inputs = [_safe_name(item) for item in list(value)[:32]]
        rows.append(
            {
                "name": _safe_name(expression),
                "class": expression.__class__.__name__,
                "desc": str(_safe_prop(expression, "desc", "") or ""),
                "inputs": [item for item in inputs if item],
            }
        )
    return rows


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
                    blend_mode = _safe_prop(material, "blend_mode", None)
                    shading_model = _safe_prop(material, "shading_model", None)
                    if blend_mode:
                        row["blend_mode"] = str(blend_mode)
                    if shading_model:
                        row["shading_model"] = str(shading_model)
                expressions = _collect_material_expressions(material)
                if expressions:
                    row["expressions"] = expressions
                scalar_params = _collect_parameter_names(unreal, material, "scalar")
                vector_params = _collect_parameter_names(unreal, material, "vector")
                texture_params = _collect_parameter_names(unreal, material, "texture")
                switch_params = _collect_parameter_names(unreal, material, "static_switch")
                if scalar_params:
                    row["scalar_parameters"] = scalar_params
                if vector_params:
                    row["vector_parameters"] = vector_params
                if texture_params:
                    row["texture_parameters"] = texture_params
                if switch_params:
                    row["static_switch_parameters"] = switch_params
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
