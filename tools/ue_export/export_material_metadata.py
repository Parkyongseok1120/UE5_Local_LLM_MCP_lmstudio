# Run inside Unreal Editor (Python) or as Editor Utility.
# Exports Material and Material Instance metadata, including material expression
# nodes and best-effort input/output wire summaries, to JSONL for RAG indexing.
#
# Usage (Editor Python console):
#   exec(open(r'path/to/tools/ue_export/export_material_metadata.py', encoding='utf-8').read())
#   export_material_metadata('/Game', r'C:\export\materials.jsonl')

import json


MAX_EXPRESSIONS = 320
MAX_GRAPH_EDGES = 800

COMMON_EXPRESSION_INPUTS = (
    "A",
    "B",
    "C",
    "D",
    "Input",
    "Input1",
    "Input2",
    "Input3",
    "Coordinates",
    "Texture",
    "TextureObject",
    "WorldPosition",
    "ViewProperty",
    "Alpha",
    "Mask",
    "Distance",
    "Position",
    "Specular",
    "Roughness",
    "Metallic",
    "Normal",
    "Color",
    "Top",
    "Bottom",
    "Left",
    "Right",
    "True",
    "False",
    "Then",
    "Else",
    "Value",
    "XY",
    "XYZ",
    "RGB",
    "R",
    "G",
    "B",
)

MATERIAL_OUTPUT_PROPERTIES = (
    ("base_color", "BaseColor"),
    ("emissive_color", "EmissiveColor"),
    ("opacity", "Opacity"),
    ("opacity_mask", "OpacityMask"),
    ("normal", "Normal"),
    ("metallic", "Metallic"),
    ("specular", "Specular"),
    ("roughness", "Roughness"),
    ("ambient_occlusion", "AmbientOcclusion"),
    ("world_position_offset", "WorldPositionOffset"),
    ("subsurface_color", "SubsurfaceColor"),
    ("refraction", "Refraction"),
)


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


def _value_to_text(value) -> str:
    if value is None:
        return ""
    for attr in ("get_path_name", "get_name"):
        try:
            if hasattr(value, attr):
                return str(getattr(value, attr)())
        except Exception:
            pass
    return str(value)


def _is_material_expression(value) -> bool:
    try:
        return value.__class__.__name__.startswith("MaterialExpression")
    except Exception:
        return False


def _expression_key(expression) -> str:
    return _safe_name(expression)


def _resolve_expression_reference(value):
    if value is None:
        return None
    if _is_material_expression(value):
        return _expression_key(value)
    inner = _safe_prop(value, "expression", None) or _safe_prop(value, "Expression", None)
    if inner and _is_material_expression(inner):
        return _expression_key(inner)
    if hasattr(value, "__iter__"):
        try:
            refs = [_resolve_expression_reference(item) for item in list(value)[:8]]
        except TypeError:
            refs = []
        refs = [item for item in refs if item]
        if not refs:
            return None
        if len(refs) == 1:
            return refs[0]
        return refs
    return None


def _collect_expression_input_wires(expression) -> dict:
    wires = {}
    for prop in COMMON_EXPRESSION_INPUTS:
        ref = _resolve_expression_reference(_safe_prop(expression, prop, None))
        if ref:
            wires[prop] = ref
    return wires


def _collect_material_root_outputs(material) -> list[dict]:
    outputs = []
    for snake, pascal in MATERIAL_OUTPUT_PROPERTIES:
        value = _safe_prop(material, snake, None)
        if value is None:
            value = _safe_prop(material, pascal, None)
        ref = _resolve_expression_reference(value)
        if ref:
            outputs.append({"output": pascal, "expression": ref if isinstance(ref, str) else str(ref)})
    return outputs


def _append_graph_edge(graph_edges: list[dict], source, target: str, target_input: str) -> None:
    if len(graph_edges) >= MAX_GRAPH_EDGES:
        return
    if isinstance(source, list):
        for idx, item in enumerate(source):
            if not item:
                continue
            suffix = target_input if len(source) == 1 else f"{target_input}[{idx}]"
            graph_edges.append({"from": str(item), "to": target, "to_input": suffix})
        return
    if source:
        graph_edges.append({"from": str(source), "to": target, "to_input": target_input})


def _collect_material_graph(material) -> tuple[list[dict], list[dict], list[dict]]:
    expressions = list(_safe_prop(material, "expressions", []) or [])[:MAX_EXPRESSIONS]
    expression_rows = []
    graph_edges = []

    for expression in expressions:
        name = _expression_key(expression)
        input_wires = _collect_expression_input_wires(expression)
        row = {
            "name": name,
            "class": expression.__class__.__name__,
            "desc": str(_safe_prop(expression, "desc", "") or ""),
            "input_wires": input_wires,
        }
        if input_wires:
            row["inputs"] = list(input_wires.keys())
        expression_rows.append(row)
        for input_name, source in input_wires.items():
            _append_graph_edge(graph_edges, source, name, input_name)

    root_outputs = _collect_material_root_outputs(material)
    for item in root_outputs:
        _append_graph_edge(graph_edges, item.get("expression"), "MaterialOutput", str(item.get("output") or ""))

    return expression_rows, graph_edges, root_outputs


def _graph_source_material(material, asset_class: str):
    expressions = _safe_prop(material, "expressions", []) or []
    if expressions:
        return material, None
    if "MaterialInstance" not in asset_class:
        return material, None
    parent = _safe_prop(material, "parent", None)
    if not parent:
        return material, None
    return parent, _value_to_text(parent)


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


def _collect_parameter_values(unreal, material, names: list[str], kind: str) -> list[dict]:
    library = getattr(unreal, "MaterialEditingLibrary", None)
    if not library:
        return []
    function_name = {
        "scalar": "get_material_instance_scalar_parameter_value",
        "vector": "get_material_instance_vector_parameter_value",
        "texture": "get_material_instance_texture_parameter_value",
        "static_switch": "get_material_instance_static_switch_parameter_value",
    }.get(kind)
    if not function_name or not hasattr(library, function_name):
        return []

    rows = []
    for name in names[:80]:
        try:
            value = getattr(library, function_name)(material, name)
        except Exception:
            continue
        rows.append({"name": str(name), "value": _value_to_text(value)})
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

                graph_material, graph_source = _graph_source_material(material, cls)
                if graph_source:
                    row["graph_source"] = graph_source
                expressions, graph_edges, root_outputs = _collect_material_graph(graph_material)
                if expressions:
                    row["expressions"] = expressions
                if graph_edges:
                    row["graph_edges"] = graph_edges
                if root_outputs:
                    row["root_outputs"] = root_outputs

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
                scalar_values = _collect_parameter_values(unreal, material, scalar_params, "scalar")
                vector_values = _collect_parameter_values(unreal, material, vector_params, "vector")
                texture_values = _collect_parameter_values(unreal, material, texture_params, "texture")
                switch_values = _collect_parameter_values(unreal, material, switch_params, "static_switch")
                if scalar_values:
                    row["scalar_parameter_values"] = scalar_values
                if vector_values:
                    row["vector_parameter_values"] = vector_values
                if texture_values:
                    row["texture_parameter_values"] = texture_values
                if switch_values:
                    row["static_switch_parameter_values"] = switch_values
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
