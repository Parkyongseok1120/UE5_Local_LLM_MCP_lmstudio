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

MATERIAL_PROPERTY_OUTPUTS = (
    ("MP_BASE_COLOR", "BaseColor"),
    ("MP_EMISSIVE_COLOR", "EmissiveColor"),
    ("MP_OPACITY", "Opacity"),
    ("MP_OPACITY_MASK", "OpacityMask"),
    ("MP_NORMAL", "Normal"),
    ("MP_METALLIC", "Metallic"),
    ("MP_SPECULAR", "Specular"),
    ("MP_ROUGHNESS", "Roughness"),
    ("MP_AMBIENT_OCCLUSION", "AmbientOcclusion"),
    ("MP_WORLD_POSITION_OFFSET", "WorldPositionOffset"),
    ("MP_SUBSURFACE_COLOR", "SubsurfaceColor"),
    ("MP_REFRACTION", "Refraction"),
)

EXPRESSION_DETAIL_PROPERTIES = (
    ("parameter_name", "parameter_name"),
    ("ParameterName", "parameter_name"),
    ("default_value", "default_value"),
    ("DefaultValue", "default_value"),
    ("const_a", "const_a"),
    ("ConstA", "const_a"),
    ("const_b", "const_b"),
    ("ConstB", "const_b"),
    ("const_base", "const_base"),
    ("ConstBase", "const_base"),
    ("const_exponent", "const_exponent"),
    ("ConstExponent", "const_exponent"),
    ("r", "r"),
    ("R", "r"),
    ("g", "g"),
    ("G", "g"),
    ("b", "b"),
    ("B", "b"),
    ("a", "a"),
    ("A", "a"),
)

MATERIAL_EXPORT_CLASSES = frozenset(
    {
        "Material",
        "MaterialInstanceConstant",
        "MaterialInstance",
        "MaterialFunction",
        "MaterialFunctionMaterialLayer",
        "MaterialFunctionMaterialLayerBlend",
        "MaterialParameterCollection",
    }
)

MATERIAL_GRAPH_CLASSES = frozenset(
    {
        "Material",
        "MaterialInstanceConstant",
        "MaterialInstance",
        "MaterialFunction",
        "MaterialFunctionMaterialLayer",
        "MaterialFunctionMaterialLayerBlend",
    }
)

MATERIAL_FUNCTION_CLASSES = frozenset(
    {
        "MaterialFunction",
        "MaterialFunctionMaterialLayer",
        "MaterialFunctionMaterialLayerBlend",
    }
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


def _append_unique_object(items: list, value) -> None:
    if value is None:
        return
    try:
        if any(item is value for item in items):
            return
    except Exception:
        pass
    items.append(value)


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


def _material_graph_sources(material) -> list:
    sources = []
    _append_unique_object(sources, material)
    for prop in (
        "expression_collection",
        "ExpressionCollection",
        "material_expression_collection",
        "MaterialExpressionCollection",
    ):
        _append_unique_object(sources, _safe_prop(material, prop, None))
    for prop in (
        "editor_only_data",
        "EditorOnlyData",
        "material_editor_only_data",
        "MaterialEditorOnlyData",
    ):
        _append_unique_object(sources, _safe_prop(material, prop, None))

    for source in list(sources):
        for prop in (
            "expression_collection",
            "ExpressionCollection",
            "material_expression_collection",
            "MaterialExpressionCollection",
        ):
            _append_unique_object(sources, _safe_prop(source, prop, None))
    return sources


def _coerce_list(value) -> list:
    if value is None:
        return []
    if isinstance(value, (str, bytes)):
        return []
    try:
        return list(value)
    except TypeError:
        return []


def _material_editing_library(unreal_module):
    if unreal_module is None:
        return None
    return getattr(unreal_module, "MaterialEditingLibrary", None)


def _is_material_function_class(cls: str) -> bool:
    return cls in MATERIAL_FUNCTION_CLASSES or (
        bool(cls) and cls.startswith("MaterialFunction") and cls not in {"MaterialFunctionInterface"}
    )


def _append_expression(expressions: list, seen: set, expression) -> bool:
    if not _is_material_expression(expression):
        return False
    key = id(expression)
    if key in seen:
        return False
    seen.add(key)
    expressions.append(expression)
    return len(expressions) >= MAX_EXPRESSIONS


def _collect_material_expressions(material, unreal_module=None, asset_class: str = "") -> list:
    expressions = []
    seen = set()
    library = _material_editing_library(unreal_module)
    is_function = _is_material_function_class(asset_class)

    if library and is_function and hasattr(library, "get_material_function_expressions"):
        try:
            for expression in list(library.get_material_function_expressions(material)):
                if _append_expression(expressions, seen, expression):
                    return expressions
        except Exception:
            pass

    if library and not is_function and hasattr(library, "get_material_expressions"):
        try:
            for expression in list(library.get_material_expressions(material)):
                if _append_expression(expressions, seen, expression):
                    return expressions
        except Exception:
            pass

    for source in _material_graph_sources(material):
        for prop in (
            "expressions",
            "Expressions",
            "material_expressions",
            "MaterialExpressions",
        ):
            for expression in _coerce_list(_safe_prop(source, prop, None)):
                if _append_expression(expressions, seen, expression):
                    return expressions
    return expressions


def _collect_expression_input_wires(
    expression,
    material=None,
    unreal_module=None,
    material_function=None,
) -> dict:
    wires = {}
    library = _material_editing_library(unreal_module)
    if (
        material_function is not None
        and library
        and hasattr(library, "get_inputs_for_material_function_expression")
    ):
        try:
            input_names = []
            if hasattr(library, "get_material_expression_input_names"):
                input_names = [str(item) for item in library.get_material_expression_input_names(expression)]
            inputs = list(library.get_inputs_for_material_function_expression(material_function, expression))
            for index, source in enumerate(inputs):
                ref = _resolve_expression_reference(source)
                if not ref:
                    continue
                input_name = input_names[index] if index < len(input_names) and input_names[index] else f"Input{index}"
                wires[input_name] = ref
        except Exception:
            pass

    if material is not None and library and hasattr(library, "get_inputs_for_material_expression"):
        try:
            input_names = []
            if hasattr(library, "get_material_expression_input_names"):
                input_names = [str(item) for item in library.get_material_expression_input_names(expression)]
            inputs = list(library.get_inputs_for_material_expression(material, expression))
            for index, source in enumerate(inputs):
                ref = _resolve_expression_reference(source)
                if not ref:
                    continue
                input_name = input_names[index] if index < len(input_names) and input_names[index] else f"Input{index}"
                wires[input_name] = ref
        except Exception:
            pass

    for prop in COMMON_EXPRESSION_INPUTS:
        if prop in wires:
            continue
        ref = _resolve_expression_reference(_safe_prop(expression, prop, None))
        if ref:
            wires[prop] = ref
    return wires


def _collect_expression_details(expression) -> dict:
    details = {}
    seen = set()
    for prop, key in EXPRESSION_DETAIL_PROPERTIES:
        if key in seen:
            continue
        value = _safe_prop(expression, prop, None)
        if value in (None, ""):
            continue
        text = _value_to_text(value)
        if text:
            seen.add(key)
            details[key] = text
    return details


def _collect_function_outputs(expressions) -> list[dict]:
    outputs = []
    seen = set()
    for expression in expressions:
        cls_name = expression.__class__.__name__
        if "FunctionOutput" not in cls_name and "MaterialLayerOutput" not in cls_name:
            continue
        output_name = (
            _safe_prop(expression, "output_name", None)
            or _safe_prop(expression, "OutputName", None)
            or _expression_key(expression)
        )
        ref = None
        for prop in ("input", "Input", "A", "Coordinates", "Texture"):
            ref = _resolve_expression_reference(_safe_prop(expression, prop, None))
            if ref:
                break
        key = (str(output_name), str(ref or ""))
        if key in seen:
            continue
        seen.add(key)
        outputs.append(
            {
                "output": str(output_name),
                "expression": ref if isinstance(ref, str) else (str(ref) if ref else ""),
                "kind": "function_output",
                "node": _expression_key(expression),
            }
        )
    return outputs


def _collect_material_function_metadata(material) -> dict:
    row = {}
    for prop, key in (
        ("description", "description"),
        ("Description", "description"),
        ("user_exposed_caption", "user_exposed_caption"),
        ("UserExposedCaption", "user_exposed_caption"),
        ("expose_to_library", "expose_to_library"),
        ("bExposeToLibrary", "expose_to_library"),
    ):
        if key in row:
            continue
        value = _safe_prop(material, prop, None)
        if value not in (None, ""):
            row[key] = _value_to_text(value)
    return row


def _collect_material_root_outputs(
    material,
    unreal_module=None,
    asset_class: str = "",
    expressions=None,
) -> list[dict]:
    outputs = []
    seen = set()
    if _is_material_function_class(asset_class) and expressions:
        for item in _collect_function_outputs(expressions):
            if item.get("expression") or item.get("node"):
                outputs.append(item)
        if outputs:
            return outputs

    library = _material_editing_library(unreal_module)
    material_property = getattr(unreal_module, "MaterialProperty", None) if unreal_module else None
    if library and material_property and hasattr(library, "get_material_property_input_node"):
        for enum_name, output_name in MATERIAL_PROPERTY_OUTPUTS:
            prop = getattr(material_property, enum_name, None)
            if prop is None:
                continue
            try:
                ref = _resolve_expression_reference(library.get_material_property_input_node(material, prop))
            except Exception:
                ref = None
            if not ref:
                continue
            key = (output_name, str(ref))
            if key in seen:
                continue
            seen.add(key)
            outputs.append({"output": output_name, "expression": ref if isinstance(ref, str) else str(ref)})

    for source in _material_graph_sources(material):
        for snake, pascal in MATERIAL_OUTPUT_PROPERTIES:
            value = _safe_prop(source, snake, None)
            if value is None:
                value = _safe_prop(source, pascal, None)
            ref = _resolve_expression_reference(value)
            if not ref:
                continue
            key = (pascal, str(ref))
            if key in seen:
                continue
            seen.add(key)
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


def _collect_material_graph(
    material,
    unreal_module=None,
    asset_class: str = "",
) -> tuple[list[dict], list[dict], list[dict]]:
    is_function = _is_material_function_class(asset_class)
    expressions = _collect_material_expressions(material, unreal_module, asset_class)[:MAX_EXPRESSIONS]
    expression_rows = []
    graph_edges = []

    for expression in expressions:
        name = _expression_key(expression)
        input_wires = _collect_expression_input_wires(
            expression,
            material=None if is_function else material,
            unreal_module=unreal_module,
            material_function=material if is_function else None,
        )
        row = {
            "name": name,
            "class": expression.__class__.__name__,
            "desc": str(_safe_prop(expression, "desc", "") or ""),
            "input_wires": input_wires,
        }
        details = _collect_expression_details(expression)
        if details:
            row["details"] = details
        if input_wires:
            row["inputs"] = list(input_wires.keys())
        expression_rows.append(row)
        for input_name, source in input_wires.items():
            _append_graph_edge(graph_edges, source, name, input_name)

    root_outputs = _collect_material_root_outputs(
        material,
        unreal_module,
        asset_class=asset_class,
        expressions=expressions,
    )
    for item in root_outputs:
        if item.get("kind") == "function_output":
            target = str(item.get("node") or item.get("output") or "FunctionOutput")
            if item.get("expression"):
                _append_graph_edge(graph_edges, item.get("expression"), target, "Input")
            _append_graph_edge(graph_edges, target, "FunctionOutput", str(item.get("output") or ""))
            continue
        _append_graph_edge(graph_edges, item.get("expression"), "MaterialOutput", str(item.get("output") or ""))

    return expression_rows, graph_edges, root_outputs


def _graph_source_material(material, asset_class: str, unreal_module=None):
    expressions = _collect_material_expressions(material, unreal_module, asset_class)
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


def _collect_mpc_parameters(mpc) -> dict:
    row: dict = {}
    scalar_rows = []
    vector_rows = []
    for prop in ("scalar_parameters", "ScalarParameters"):
        for item in list(_safe_prop(mpc, prop, []) or [])[:80]:
            name = _safe_prop(item, "parameter_name", None) or _safe_prop(item, "ParameterName", None)
            default = _safe_prop(item, "default_value", None) or _safe_prop(item, "DefaultValue", None)
            if name:
                scalar_rows.append({"name": str(name), "default": _value_to_text(default)})
    for prop in ("vector_parameters", "VectorParameters"):
        for item in list(_safe_prop(mpc, prop, []) or [])[:80]:
            name = _safe_prop(item, "parameter_name", None) or _safe_prop(item, "ParameterName", None)
            default = _safe_prop(item, "default_value", None) or _safe_prop(item, "DefaultValue", None)
            if name:
                vector_rows.append({"name": str(name), "default": _value_to_text(default)})
    if scalar_rows:
        row["scalar_parameters"] = scalar_rows
    if vector_rows:
        row["vector_parameters"] = vector_rows
    return row


def _export_material_row(registry, asset, cls: str, path: str) -> dict:
    import unreal

    row = {
        "asset_path": path,
        "asset_type": cls,
        "name": path.rsplit("/", 1)[-1],
    }
    try:
        material = unreal.load_asset(path)
        if not material:
            return row

        if cls == "MaterialParameterCollection":
            row.update(_collect_mpc_parameters(material))
            dependencies = registry.get_dependencies(asset.package_name)
            if dependencies:
                row["dependencies"] = [str(dep) for dep in dependencies[:40]]
            return row

        if "MaterialInstance" in cls:
            parent = _safe_prop(material, "parent", None)
            if parent:
                row["parent_material"] = _safe_name(parent)
        blend_mode = _safe_prop(material, "blend_mode", None)
        shading_model = _safe_prop(material, "shading_model", None)
        if blend_mode:
            row["blend_mode"] = str(blend_mode)
        if shading_model:
            row["shading_model"] = str(shading_model)

        if _is_material_function_class(cls):
            row.update(_collect_material_function_metadata(material))

        if cls in MATERIAL_GRAPH_CLASSES:
            graph_material, graph_source = _graph_source_material(material, cls, unreal)
            if graph_source:
                row["graph_source"] = graph_source
            expressions, graph_edges, root_outputs = _collect_material_graph(graph_material, unreal, cls)
            if expressions:
                row["expressions"] = expressions
            if graph_edges:
                row["graph_edges"] = graph_edges
            if root_outputs:
                row["root_outputs"] = root_outputs
            if not expressions and not graph_edges:
                row["graph_export_note"] = "no_expressions_collected"

        if "MaterialInstance" in cls:
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
    return row


def export_material_metadata(content_path: str, out_path: str) -> None:
    import unreal

    registry = unreal.AssetRegistryHelpers.get_asset_registry()
    assets = registry.get_assets_by_path(content_path, recursive=True)
    rows = []
    for asset in assets:
        cls = _asset_class_name(asset)
        if cls not in MATERIAL_EXPORT_CLASSES:
            continue
        path = str(asset.package_name)
        rows.append(_export_material_row(registry, asset, cls, path))

    with open(out_path, "w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    unreal.log(f"Exported {len(rows)} material metadata rows to {out_path}")
