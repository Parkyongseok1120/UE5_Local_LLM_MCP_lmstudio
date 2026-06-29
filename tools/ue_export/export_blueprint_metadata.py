# Run inside Unreal Editor (Python) or as Editor Utility.
# Exports Blueprint metadata, including best-effort graph/node/pin summaries,
# to JSONL for RAG indexing.
#
# Usage (Editor Python console):
#   exec(open(r'path/to/tools/ue_export/export_blueprint_metadata.py').read())
#   export_blueprint_metadata('/Game', r'C:\export\blueprints.jsonl')

import json


MAX_GRAPHS = 24
MAX_NODES_PER_GRAPH = 160
MAX_PINS_PER_NODE = 24


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


def _safe_text(value) -> str:
    try:
        return str(value)
    except Exception:
        return ""


def _member_reference_summary(value) -> dict:
    if not value:
        return {}
    row = {}
    for prop in ("member_name", "member_parent", "member_guid"):
        prop_value = _safe_prop(value, prop, None)
        if prop_value:
            row[prop] = _safe_text(prop_value)
    return row


def _pin_summary(pin) -> dict:
    linked_to = _safe_prop(pin, "linked_to", []) or []
    pin_type = _safe_prop(pin, "pin_type", None)
    direction = _safe_prop(pin, "direction", "")
    row = {
        "name": _safe_text(_safe_prop(pin, "pin_name", "")),
        "direction": _safe_text(direction),
        "type": _safe_text(pin_type),
        "linked_to_count": len(linked_to) if hasattr(linked_to, "__len__") else 0,
    }
    default_value = _safe_prop(pin, "default_value", None)
    default_object = _safe_prop(pin, "default_object", None)
    if default_value not in (None, ""):
        row["default_value"] = _safe_text(default_value)
    if default_object:
        row["default_object"] = _safe_name(default_object)
    return row


def _node_summary(node) -> dict:
    title = ""
    try:
        title = _safe_text(node.get_node_title())
    except Exception:
        title = _safe_text(_safe_prop(node, "node_title", ""))
    pins = _safe_prop(node, "pins", []) or []
    row = {
        "name": _safe_name(node),
        "class": node.__class__.__name__,
        "title": title or _safe_name(node),
        "pins": [_pin_summary(pin) for pin in list(pins)[:MAX_PINS_PER_NODE]],
    }
    for prop in ("function_reference", "variable_reference"):
        summary = _member_reference_summary(_safe_prop(node, prop, None))
        if summary:
            row[prop] = summary
    for prop in ("event_reference", "delegate_reference", "custom_function_name"):
        value = _safe_prop(node, prop, None)
        if value:
            row[prop] = _safe_text(value)
    return row


def _graph_summary(graph) -> dict:
    nodes = _safe_prop(graph, "nodes", []) or []
    node_rows = [_node_summary(node) for node in list(nodes)[:MAX_NODES_PER_GRAPH]]
    return {
        "name": _safe_name(graph),
        "node_count": len(nodes) if hasattr(nodes, "__len__") else len(node_rows),
        "nodes": node_rows,
    }


def _collect_graphs(bp) -> list[dict]:
    graphs = []
    for prop in (
        "ubergraph_pages",
        "function_graphs",
        "macro_graphs",
        "delegate_signature_graphs",
    ):
        for graph in list(_safe_prop(bp, prop, []) or []):
            if len(graphs) >= MAX_GRAPHS:
                return graphs
            graphs.append(_graph_summary(graph))
    return graphs


def _collect_names(bp, prop: str) -> list[str]:
    values = _safe_prop(bp, prop, []) or []
    names = []
    for value in list(values)[:80]:
        names.append(_safe_name(value))
    return [name for name in names if name]


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
                variables = _collect_names(bp, "new_variables")
                if variables:
                    row["variables"] = variables
                functions = _collect_names(bp, "function_graphs")
                if functions:
                    row["functions"] = functions
                interfaces = _collect_names(bp, "implemented_interfaces")
                if interfaces:
                    row["interfaces"] = interfaces
                graphs = _collect_graphs(bp)
                if graphs:
                    row["graphs"] = graphs
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
