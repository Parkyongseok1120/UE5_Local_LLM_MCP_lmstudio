# Run inside Unreal Editor (Python) or as Editor Utility.
# Exports animation, skeletal, sequencer, rig, and pose metadata to JSONL.

from export_common import MAX_ITEMS, collect_dependencies, safe_name, safe_prop, value_to_text, coerce_list, write_jsonl_rows

ANIMATION_EXPORT_CLASSES = frozenset(
    {
        "SkeletalMesh",
        "AnimBlueprint",
        "AnimSequence",
        "AnimMontage",
        "AnimNotify",
        "AnimNotifyState",
        "LevelSequence",
        "PoseAsset",
        "BlendSpace",
        "BlendSpace1D",
        "AimOffsetBlendSpace",
        "Skeleton",
        "PhysicsAsset",
        "ControlRigBlueprint",
        "IKRigDefinition",
        "IKRetargeter",
    }
)


def _collect_skeletal_mesh(asset_obj) -> dict:
    skeleton = safe_prop(asset_obj, "skeleton", None)
    physics_asset = safe_prop(asset_obj, "physics_asset", None)
    materials = []
    for slot in coerce_list(safe_prop(asset_obj, "materials", None))[:MAX_ITEMS]:
        material = safe_prop(slot, "material_interface", None)
        slot_name = safe_prop(slot, "material_slot_name", "")
        materials.append({"slot": str(slot_name or ""), "material": safe_name(material)})
    row: dict = {}
    if skeleton:
        row["skeleton"] = safe_name(skeleton)
    if physics_asset:
        row["physics_asset"] = safe_name(physics_asset)
    if materials:
        row["materials"] = materials
    return row


def _collect_anim_blueprint(asset_obj) -> dict:
    row: dict = {}
    generated_class = safe_prop(asset_obj, "generated_class", None)
    parent_class = safe_prop(asset_obj, "parent_class", None)
    target_skeleton = safe_prop(asset_obj, "target_skeleton", None)
    if generated_class:
        row["generated_class"] = safe_name(generated_class)
    if parent_class:
        row["parent_class"] = safe_name(parent_class)
    if target_skeleton:
        row["skeleton"] = safe_name(target_skeleton)
    graphs = []
    for prop in ("ubergraph_pages", "function_graphs", "anim_graphs"):
        for graph in coerce_list(safe_prop(asset_obj, prop, None))[:MAX_ITEMS]:
            graphs.append(safe_name(graph))
    if graphs:
        row["graphs"] = graphs[:MAX_ITEMS]
    return row


def _collect_notifies(asset_obj) -> list[dict]:
    notifies = []
    for notify in coerce_list(safe_prop(asset_obj, "notifies", None))[:MAX_ITEMS]:
        notify_obj = safe_prop(notify, "notify", None) or safe_prop(notify, "notify_state_class", None)
        notifies.append(
            {
                "name": safe_name(notify_obj) or str(safe_prop(notify, "notify_name", "") or ""),
                "time": str(safe_prop(notify, "time", "") or ""),
                "class": notify_obj.__class__.__name__ if notify_obj else "",
            }
        )
    return [item for item in notifies if item["name"] or item["time"]]


def _collect_animation_asset(asset_obj) -> dict:
    row: dict = {}
    skeleton = safe_prop(asset_obj, "skeleton", None)
    sequence_length = safe_prop(asset_obj, "sequence_length", None)
    rate_scale = safe_prop(asset_obj, "rate_scale", None)
    notifies = _collect_notifies(asset_obj)
    if skeleton:
        row["skeleton"] = safe_name(skeleton)
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
    for section in coerce_list(safe_prop(asset_obj, "composite_sections", None))[:MAX_ITEMS]:
        sections.append(
            {
                "name": str(safe_prop(section, "section_name", "") or ""),
                "start_time": str(safe_prop(section, "start_time", "") or ""),
            }
        )
    slots = []
    for slot in coerce_list(safe_prop(asset_obj, "slot_anim_tracks", None))[:MAX_ITEMS]:
        slots.append(str(safe_prop(slot, "slot_name", "") or ""))
    if sections:
        row["montage_sections"] = sections
    if slots:
        row["slots"] = [slot for slot in slots if slot]
    return row


def _collect_level_sequence(asset_obj) -> dict:
    row: dict = {}
    bindings = []
    try:
        for binding in list(asset_obj.get_bindings())[:MAX_ITEMS]:
            binding_row = {"name": safe_name(binding), "tracks": []}
            try:
                binding_row["tracks"] = [safe_name(track) for track in binding.get_tracks()[:MAX_ITEMS]]
            except Exception:
                pass
            bindings.append(binding_row)
    except Exception:
        pass
    if bindings:
        row["bindings"] = bindings
    return row


def _collect_pose_asset(asset_obj) -> dict:
    row: dict = {}
    skeleton = safe_prop(asset_obj, "skeleton", None) or safe_prop(asset_obj, "Skeleton", None)
    if skeleton:
        row["skeleton"] = safe_name(skeleton)
    poses = []
    for pose_name in coerce_list(safe_prop(asset_obj, "pose_names", None) or safe_prop(asset_obj, "PoseNames", None))[:MAX_ITEMS]:
        poses.append(str(pose_name))
    if poses:
        row["poses"] = poses
    return row


def _collect_blend_space(asset_obj) -> dict:
    row: dict = {}
    skeleton = safe_prop(asset_obj, "skeleton", None) or safe_prop(asset_obj, "Skeleton", None)
    if skeleton:
        row["skeleton"] = safe_name(skeleton)
    samples = []
    for sample in coerce_list(safe_prop(asset_obj, "sample_data", None) or safe_prop(asset_obj, "SampleData", None))[:MAX_ITEMS]:
        anim = safe_prop(sample, "animation", None) or safe_prop(sample, "Animation", None)
        if anim:
            samples.append(safe_name(anim))
    if samples:
        row["blend_samples"] = samples
    for axis in ("horizontal_axis", "HorizontalAxis", "vertical_axis", "VerticalAxis"):
        value = safe_prop(asset_obj, axis, None)
        if value is not None:
            row.setdefault("properties", {})[axis] = value_to_text(value)
    return row


def _collect_skeleton(asset_obj) -> dict:
    row: dict = {}
    bones = []
    for bone in coerce_list(safe_prop(asset_obj, "bone_tree", None) or safe_prop(asset_obj, "BoneTree", None))[:MAX_ITEMS]:
        name = safe_prop(bone, "name", None) or safe_name(bone)
        if name:
            bones.append(str(name))
    if bones:
        row["bones"] = bones
    sockets = []
    for socket in coerce_list(safe_prop(asset_obj, "sockets", None) or safe_prop(asset_obj, "Sockets", None))[:MAX_ITEMS]:
        sockets.append(safe_name(socket))
    if sockets:
        row["sockets"] = sockets
    return row


def _collect_physics_asset(asset_obj) -> dict:
    row: dict = {}
    bodies = []
    for body in coerce_list(safe_prop(asset_obj, "skeletal_body_setups", None) or safe_prop(asset_obj, "SkeletalBodySetups", None))[
        :MAX_ITEMS
    ]:
        bodies.append(safe_name(body))
    if bodies:
        row["physics_bodies"] = bodies
    constraints = []
    for constraint in coerce_list(safe_prop(asset_obj, "constraint_setup", None) or safe_prop(asset_obj, "ConstraintSetup", None))[
        :MAX_ITEMS
    ]:
        constraints.append(safe_name(constraint))
    if constraints:
        row["constraints"] = constraints
    return row


def _collect_control_rig(asset_obj) -> dict:
    row: dict = {}
    graph = safe_prop(asset_obj, "control_rig_graph", None) or safe_prop(asset_obj, "ControlRigGraph", None)
    if graph:
        row["graphs"] = [safe_name(graph)]
    return row


def _collect_ik_rig(asset_obj) -> dict:
    row: dict = {}
    chains = []
    for chain in coerce_list(safe_prop(asset_obj, "goal_solvers", None) or safe_prop(asset_obj, "GoalSolvers", None))[:MAX_ITEMS]:
        chains.append(safe_name(chain))
    if chains:
        row["properties"] = {"goal_solvers": chains}
    return row


def _collect_ik_retargeter(asset_obj) -> dict:
    row: dict = {}
    source = safe_prop(asset_obj, "source_ik_rig", None) or safe_prop(asset_obj, "SourceIKRig", None)
    target = safe_prop(asset_obj, "target_ik_rig", None) or safe_prop(asset_obj, "TargetIKRig", None)
    if source:
        row["parent_class"] = safe_name(source)
    if target:
        row.setdefault("properties", {})["target_ik_rig"] = safe_name(target)
    return row


COLLECTORS = {
    "SkeletalMesh": lambda _u, o, _c: _collect_skeletal_mesh(o),
    "AnimBlueprint": lambda _u, o, _c: _collect_anim_blueprint(o),
    "AnimMontage": lambda _u, o, _c: _collect_montage(o),
    "AnimSequence": lambda _u, o, _c: _collect_animation_asset(o),
    "LevelSequence": lambda _u, o, _c: _collect_level_sequence(o),
    "PoseAsset": lambda _u, o, _c: _collect_pose_asset(o),
    "BlendSpace": lambda _u, o, _c: _collect_blend_space(o),
    "BlendSpace1D": lambda _u, o, _c: _collect_blend_space(o),
    "AimOffsetBlendSpace": lambda _u, o, _c: _collect_blend_space(o),
    "Skeleton": lambda _u, o, _c: _collect_skeleton(o),
    "PhysicsAsset": lambda _u, o, _c: _collect_physics_asset(o),
    "ControlRigBlueprint": lambda _u, o, _c: _collect_control_rig(o),
    "IKRigDefinition": lambda _u, o, _c: _collect_ik_rig(o),
    "IKRetargeter": lambda _u, o, _c: _collect_ik_retargeter(o),
}


def export_animation_metadata(content_path: str, out_path: str) -> None:
    import unreal

    registry = unreal.AssetRegistryHelpers.get_asset_registry()
    assets = registry.get_assets_by_path(content_path, recursive=True)
    rows = []
    for asset in assets:
        cls = str(asset.asset_class_path.asset_name) if hasattr(asset, "asset_class_path") else ""
        if cls not in ANIMATION_EXPORT_CLASSES:
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
                collector = COLLECTORS.get(cls)
                if collector:
                    row.update(collector(unreal, asset_obj, cls))
            dependencies = collect_dependencies(registry, asset.package_name, limit=80)
            if dependencies:
                row["dependencies"] = dependencies
        except Exception:
            pass
        rows.append(row)

    write_jsonl_rows(out_path, rows)
    unreal.log(f"Exported {len(rows)} animation metadata rows to {out_path}")
