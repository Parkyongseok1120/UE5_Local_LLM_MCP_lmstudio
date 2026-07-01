# Run inside Unreal Editor (Python) or as Editor Utility.
# Exports Blueprint metadata, including best-effort graph/node/pin summaries and
# pin link targets, to JSONL for RAG indexing.
#
# Usage (Editor Python console):
#   exec(open(r'path/to/tools/ue_export/export_blueprint_metadata.py', encoding='utf-8').read())
#   export_blueprint_metadata('/Game', r'C:\export\blueprints.jsonl')

import json


MAX_GRAPHS = 24
MAX_NODES_PER_GRAPH = 160
MAX_PINS_PER_NODE = 24
MAX_LINKS_PER_PIN = 16
MAX_GRAPH_LINKS = 1200


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


def _node_title(node) -> str:
    try:
        return _safe_text(node.get_node_title())
    except Exception:
        return _safe_text(_safe_prop(node, "node_title", ""))


def _linked_pin_target(linked_pin) -> dict | None:
    if linked_pin is None:
        return None
    node = None
    for accessor in ("get_owning_node", "getOwningNode"):
        try:
            if hasattr(linked_pin, accessor):
                node = getattr(linked_pin, accessor)()
                if node:
                    break
        except Exception:
            continue
    if node is None:
        node = _safe_prop(linked_pin, "owning_node", None)
    if node is None:
        return None
    return {
        "node": _safe_name(node),
        "node_title": _node_title(node) or _safe_name(node),
        "pin": _safe_text(_safe_prop(linked_pin, "pin_name", "")),
    }


def _pin_summary(pin) -> dict:
    linked_to = _safe_prop(pin, "linked_to", []) or []
    pin_type = _safe_prop(pin, "pin_type", None)
    direction = _safe_prop(pin, "direction", "")
    links = []
    for linked in list(linked_to)[:MAX_LINKS_PER_PIN]:
        target = _linked_pin_target(linked)
        if target:
            links.append(target)
    row = {
        "name": _safe_text(_safe_prop(pin, "pin_name", "")),
        "direction": _safe_text(direction),
        "type": _safe_text(pin_type),
        "linked_to_count": len(linked_to) if hasattr(linked_to, "__len__") else len(links),
    }
    if links:
        row["links"] = links
    default_value = _safe_prop(pin, "default_value", None)
    default_object = _safe_prop(pin, "default_object", None)
    if default_value not in (None, ""):
        row["default_value"] = _safe_text(default_value)
    if default_object:
        row["default_object"] = _safe_name(default_object)
    return row


def _node_summary(node, graph_name: str, graph_links: list[dict]) -> dict:
    title = _node_title(node) or _safe_name(node)
    pins = _safe_prop(node, "pins", []) or []
    node_name = _safe_name(node)
    pin_rows = []
    for pin in list(pins)[:MAX_PINS_PER_NODE]:
        pin_row = _pin_summary(pin)
        pin_rows.append(pin_row)
        pin_name = pin_row.get("name") or ""
        for link in pin_row.get("links") or []:
            if len(graph_links) >= MAX_GRAPH_LINKS:
                break
            graph_links.append(
                {
                    "graph": graph_name,
                    "from_node": node_name,
                    "from_pin": pin_name,
                    "to_node": link.get("node") or "",
                    "to_pin": link.get("pin") or "",
                }
            )
    row = {
        "name": node_name,
        "class": node.__class__.__name__,
        "title": title,
        "pins": pin_rows,
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


def _graph_summary(graph, graph_links: list[dict]) -> dict:
    graph_name = _safe_name(graph)
    nodes = _safe_prop(graph, "nodes", []) or []
    node_rows = [
        _node_summary(node, graph_name, graph_links)
        for node in list(nodes)[:MAX_NODES_PER_GRAPH]
    ]
    return {
        "name": graph_name,
        "node_count": len(nodes) if hasattr(nodes, "__len__") else len(node_rows),
        "nodes": node_rows,
    }


def _collect_graphs(bp) -> tuple[list[dict], list[dict]]:
    graphs = []
    graph_links: list[dict] = []
    for prop in (
        "ubergraph_pages",
        "function_graphs",
        "macro_graphs",
        "delegate_signature_graphs",
    ):
        for graph in list(_safe_prop(bp, prop, []) or []):
            if len(graphs) >= MAX_GRAPHS:
                return graphs, graph_links
            graphs.append(_graph_summary(graph, graph_links))
    return graphs, graph_links


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
                graphs, graph_links = _collect_graphs(bp)
                if graphs:
                    row["graphs"] = graphs
                if graph_links:
                    row["graph_links"] = graph_links
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
