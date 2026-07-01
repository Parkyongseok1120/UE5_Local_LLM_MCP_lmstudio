# Run inside Unreal Editor Python.
# export_mesh_metadata('/Game', r'C:\export\meshes.jsonl')

from export_common import MAX_ITEMS, export_by_class_map, safe_name, safe_prop, value_to_text, coerce_list

MESH_EXPORT_CLASSES = frozenset(
    {
        "StaticMesh",
        "GeometryCollection",
        "FoliageType_InstancedStaticMesh",
    }
)


def _collect_static_mesh(_unreal, asset_obj, cls: str) -> dict:
    row: dict = {}
    slots = []
    for slot in coerce_list(safe_prop(asset_obj, "static_materials", None) or safe_prop(asset_obj, "StaticMaterials", None))[
        :MAX_ITEMS
    ]:
        material = safe_prop(slot, "material_interface", None) or safe_prop(slot, "MaterialInterface", None)
        slot_name = safe_prop(slot, "material_slot_name", None) or safe_prop(slot, "MaterialSlotName", None)
        slots.append({"slot": str(slot_name or ""), "material": safe_name(material)})
    if slots:
        row["material_slots"] = slots
    for key, props in (
        ("lod_count", ("lod_group", "LODGroup")),
        ("nanite_enabled", ("nanite_settings", "NaniteSettings")),
        ("collision_profile", ("body_setup", "BodySetup")),
    ):
        value = None
        for prop in props:
            value = safe_prop(asset_obj, prop, None)
            if value is not None:
                break
        if value is not None:
            if key == "nanite_enabled":
                enabled = safe_prop(value, "enabled", None) if value else None
                row[key] = value_to_text(enabled if enabled is not None else value)
            elif key == "collision_profile" and value:
                row[key] = safe_name(value)
            else:
                row[key] = value_to_text(value)
    bounds = safe_prop(asset_obj, "extended_bounds", None) or safe_prop(asset_obj, "ExtendedBounds", None)
    if bounds:
        row["bounds"] = value_to_text(bounds)
    return row


def _collect_geometry_collection(_unreal, asset_obj, cls: str) -> dict:
    row: dict = {}
    clusters = coerce_list(safe_prop(asset_obj, "geometry_source", None) or safe_prop(asset_obj, "GeometrySource", None))
    if clusters:
        row["properties"] = {"geometry_source_count": len(clusters)}
    return row


def _collect_foliage_type(_unreal, asset_obj, cls: str) -> dict:
    row: dict = {}
    mesh = safe_prop(asset_obj, "mesh", None) or safe_prop(asset_obj, "Mesh", None)
    if mesh:
        row["parent_class"] = safe_name(mesh)
    return row


COLLECTORS = {
    "StaticMesh": _collect_static_mesh,
    "GeometryCollection": _collect_geometry_collection,
    "FoliageType_InstancedStaticMesh": _collect_foliage_type,
}


def export_mesh_metadata(content_path: str, out_path: str) -> None:
    export_by_class_map(
        content_path,
        out_path,
        export_classes=MESH_EXPORT_CLASSES,
        collectors=COLLECTORS,
        log_label="mesh",
    )
