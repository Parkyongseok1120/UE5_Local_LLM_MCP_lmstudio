# Run inside Unreal Editor (Python) or as Editor Utility.
# Exports SkeletalMesh, AnimBlueprint, animation, montage, notify, and
# LevelSequence metadata to JSONL for RAG indexing.
#
# Usage (Editor Python console):
#   exec(open(r'path/to/tools/ue_export/export_animation_metadata.py').read())
#   export_animation_metadata('/Game', r'C:\export\animation.jsonl')

import json


MAX_ITEMS = 160


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


def _collect_dependencies(registry, package_name) -> list[str]:
    try:
        return [str(dep) for dep in registry.get_dependencies(package_name)[:80]]
    except Exception:
        return []


def _collect_skeletal_mesh(asset_obj) -> dict:
    skeleton = _safe_prop(asset_obj, "skeleton", None)
    physics_asset = _safe_prop(asset_obj, "physics_asset", None)
    materials = []
    for slot in list(_safe_prop(asset_obj, "materials", []) or [])[:MAX_ITEMS]:
        material = _safe_prop(slot, "material_interface", None)
        slot_name = _safe_prop(slot, "material_slot_name", "")
        materials.append(
            {
                "slot": str(slot_name or ""),
                "material": _safe_name(material),
            }
        )
    row = {}
    if skeleton:
        row["skeleton"] = _safe_name(skeleton)
    if physics_asset:
        row["physics_asset"] = _safe_name(physics_asset)
    if materials:
        row["materials"] = materials
    return row


def _collect_anim_blueprint(asset_obj) -> dict:
    row = {}
    generated_class = _safe_prop(asset_obj, "generated_class", None)
    parent_class = _safe_prop(asset_obj, "parent_class", None)
    target_skeleton = _safe_prop(asset_obj, "target_skeleton", None)
    if generated_class:
        row["generated_class"] = _safe_name(generated_class)
    if parent_class:
        row["parent_class"] = _safe_name(parent_class)
    if target_skeleton:
        row["skeleton"] = _safe_name(target_skeleton)
    graphs = []
    for prop in ("ubergraph_pages", "function_graphs", "anim_graphs"):
        for graph in list(_safe_prop(asset_obj, prop, []) or [])[:MAX_ITEMS]:
            graphs.append(_safe_name(graph))
    if graphs:
        row["graphs"] = graphs[:MAX_ITEMS]
    return row


def _collect_notifies(asset_obj) -> list[dict]:
    notifies = []
    for notify in list(_safe_prop(asset_obj, "notifies", []) or [])[:MAX_ITEMS]:
        notify_obj = _safe_prop(notify, "notify", None) or _safe_prop(notify, "notify_state_class", None)
        notifies.append(
            {
                "name": _safe_name(notify_obj) or str(_safe_prop(notify, "notify_name", "") or ""),
                "time": str(_safe_prop(notify, "time", "") or ""),
                "class": notify_obj.__class__.__name__ if notify_obj else "",
            }
        )
    return [item for item in notifies if item["name"] or item["time"]]


def _collect_animation_asset(asset_obj) -> dict:
    row = {}
    skeleton = _safe_prop(asset_obj, "skeleton", None)
    sequence_length = _safe_prop(asset_obj, "sequence_length", None)
    rate_scale = _safe_prop(asset_obj, "rate_scale", None)
    notifies = _collect_notifies(asset_obj)
    if skeleton:
        row["skeleton"] = _safe_name(skeleton)
    if sequence_length is not None:
        row["sequence_length"] = str(sequence_length)
    if rate_scale is not None:
        row["rate_scale"] = str(rate_scale)
    if notifies:
        row["notifies"] = notifies
    return row


def _collect_montage(asset_obj) -> dict:
    row = _collect_animation_asset(asset_obj)
    sections = []
    for section in list(_safe_prop(asset_obj, "composite_sections", []) or [])[:MAX_ITEMS]:
        sections.append(
            {
                "name": str(_safe_prop(section, "section_name", "") or ""),
                "start_time": str(_safe_prop(section, "start_time", "") or ""),
            }
        )
    slots = []
    for slot in list(_safe_prop(asset_obj, "slot_anim_tracks", []) or [])[:MAX_ITEMS]:
        slots.append(str(_safe_prop(slot, "slot_name", "") or ""))
    if sections:
        row["montage_sections"] = sections
    if slots:
        row["slots"] = [slot for slot in slots if slot]
    return row


def _collect_level_sequence(asset_obj) -> dict:
    row = {}
    bindings = []
    try:
        for binding in list(asset_obj.get_bindings())[:MAX_ITEMS]:
            binding_row = {"name": _safe_name(binding), "tracks": []}
            try:
                binding_row["tracks"] = [_safe_name(track) for track in binding.get_tracks()[:MAX_ITEMS]]
            except Exception:
                pass
            bindings.append(binding_row)
    except Exception:
        pass
    if bindings:
        row["bindings"] = bindings
    return row


def export_animation_metadata(content_path: str, out_path: str) -> None:
    import unreal

    registry = unreal.AssetRegistryHelpers.get_asset_registry()
    assets = registry.get_assets_by_path(content_path, recursive=True)
    supported = {
        "SkeletalMesh",
        "AnimBlueprint",
        "AnimSequence",
        "AnimMontage",
        "AnimNotify",
        "AnimNotifyState",
        "LevelSequence",
    }
    rows = []
    for asset in assets:
        cls = _asset_class_name(asset)
        if cls not in supported:
            continue
        path = str(asset.package_name)
        row = {
            "asset_path": path,
            "asset_type": cls,
            "name": path.rsplit("/", 1)[-1],
        }
        try:
            asset_obj = unreal.load_asset(path)
            if asset_obj:
                if cls == "SkeletalMesh":
                    row.update(_collect_skeletal_mesh(asset_obj))
                elif cls == "AnimBlueprint":
                    row.update(_collect_anim_blueprint(asset_obj))
                elif cls == "AnimMontage":
                    row.update(_collect_montage(asset_obj))
                elif cls == "AnimSequence":
                    row.update(_collect_animation_asset(asset_obj))
                elif cls == "LevelSequence":
                    row.update(_collect_level_sequence(asset_obj))
            dependencies = _collect_dependencies(registry, asset.package_name)
            if dependencies:
                row["dependencies"] = dependencies
        except Exception:
            pass
        rows.append(row)

    with open(out_path, "w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    unreal.log(f"Exported {len(rows)} animation metadata rows to {out_path}")
