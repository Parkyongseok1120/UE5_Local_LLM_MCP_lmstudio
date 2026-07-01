# Run inside Unreal Editor (Python) or as Editor Utility.
# Exports structured metadata for gameplay/data/VFX/AI/audio/input/UI assets.

from export_common import (
    MAX_ITEMS,
    export_by_class_map,
    safe_name,
    safe_prop,
    value_to_text,
    coerce_list,
)

MAX_ROW_NAMES = 80

STRUCTURED_EXPORT_CLASSES = frozenset(
    {
        "DataTable",
        "DataAsset",
        "PrimaryDataAsset",
        "CurveFloat",
        "CurveVector",
        "CurveLinearColor",
        "CurveTable",
        "StringTable",
        "DataRegistry",
        "UserDefinedEnum",
        "NiagaraSystem",
        "NiagaraEmitter",
        "NiagaraParameterCollection",
        "NiagaraScript",
        "ParticleSystem",
        "BehaviorTree",
        "BlackboardData",
        "EnvQuery",
        "SmartObjectDefinition",
        "NavModifierVolume",
        "SoundCue",
        "SoundWave",
        "MetaSoundSource",
        "MetaSoundPatch",
        "SoundClass",
        "SoundMix",
        "SoundAttenuation",
        "InputAction",
        "InputMappingContext",
        "InputModifier",
        "InputTrigger",
        "PhysicalMaterial",
        "GameplayAbility",
        "GameplayEffect",
        "GameplayCueNotify_Static",
        "Font",
        "FontFace",
        "MediaPlayer",
        "FileMediaSource",
        "ImgMediaSource",
    }
)


def _collect_data_table(unreal, asset_obj, cls: str) -> dict:
    row: dict = {}
    row_struct = safe_prop(asset_obj, "row_struct", None) or safe_prop(asset_obj, "RowStruct", None)
    if row_struct:
        row["row_struct"] = safe_name(row_struct)
        columns = []
        for field in coerce_list(safe_prop(row_struct, "fields", None) or safe_prop(row_struct, "Fields", None))[:MAX_ITEMS]:
            field_name = safe_prop(field, "name", None) or safe_prop(field, "Name", None)
            if field_name:
                columns.append(str(field_name))
        if columns:
            row["columns"] = columns
    library = getattr(unreal, "DataTableFunctionLibrary", None)
    if library and hasattr(library, "get_data_table_row_names"):
        try:
            row_names = [str(name) for name in library.get_data_table_row_names(asset_obj)[:MAX_ROW_NAMES]]
            if row_names:
                row["row_names"] = row_names
        except Exception:
            pass
    return row


def _collect_curve(asset_obj, cls: str) -> dict:
    row: dict = {}
    keys = []
    for prop in ("float_curve", "FloatCurve", "vector_curve", "VectorCurve", "color_curve", "ColorCurve"):
        curve = safe_prop(asset_obj, prop, None)
        if not curve:
            continue
        for key_prop in ("keys", "Keys"):
            for key in coerce_list(safe_prop(curve, key_prop, None))[:MAX_ITEMS]:
                time = safe_prop(key, "time", None) or safe_prop(key, "Time", None)
                if time is not None:
                    keys.append(str(time))
        break
    if keys:
        row["curve_keys"] = keys[:MAX_ITEMS]
    return row


def _collect_curve_table(asset_obj) -> dict:
    row: dict = {}
    rows = []
    for prop in ("row_map", "RowMap"):
        for key in coerce_list(safe_prop(asset_obj, prop, None))[:MAX_ROW_NAMES]:
            rows.append(value_to_text(key))
    if rows:
        row["row_names"] = rows
    return row


def _collect_string_table(asset_obj) -> dict:
    row: dict = {}
    namespace = safe_prop(asset_obj, "namespace", None) or safe_prop(asset_obj, "Namespace", None)
    if namespace:
        row["namespace"] = str(namespace)
    keys = []
    for prop in ("keys", "Keys", "string_table", "StringTable"):
        container = safe_prop(asset_obj, prop, None)
        if container is None:
            continue
        for key in coerce_list(safe_prop(container, "keys", None) or safe_prop(container, "Keys", None))[:MAX_ITEMS]:
            keys.append(value_to_text(key))
    if keys:
        row["row_names"] = [key for key in keys if key][:MAX_ROW_NAMES]
    return row


def _collect_data_registry(asset_obj) -> dict:
    row: dict = {}
    for key in ("registry_type", "RegistryType", "item_struct", "ItemStruct"):
        value = safe_prop(asset_obj, key, None)
        if value is not None:
            row["row_struct"] = value_to_text(value)
            break
    return row


def _collect_user_defined_enum(asset_obj) -> dict:
    row: dict = {}
    values = []
    for prop in ("display_name_map", "DisplayNameMap", "names", "Names"):
        container = safe_prop(asset_obj, prop, None)
        if container is None:
            continue
        for item in coerce_list(container)[:MAX_ITEMS]:
            values.append(value_to_text(item))
    if values:
        row["row_names"] = values
    return row


def _collect_niagara_system(asset_obj) -> dict:
    row: dict = {}
    emitters = []
    for prop in ("system_emitters", "SystemEmitters"):
        for item in coerce_list(safe_prop(asset_obj, prop, None))[:MAX_ITEMS]:
            emitter = safe_prop(item, "emitter", None) or safe_prop(item, "Emitter", None) or item
            name = safe_name(emitter)
            if name:
                emitters.append(name)
    if emitters:
        row["emitters"] = emitters
    user_params = []
    for prop in ("user_parameters", "UserParameters"):
        for item in coerce_list(safe_prop(asset_obj, prop, None))[:MAX_ITEMS]:
            name = safe_prop(item, "name", None) or safe_prop(item, "Name", None)
            if name:
                user_params.append(str(name))
    if user_params:
        row["user_parameters"] = user_params
    return row


def _collect_niagara_emitter(asset_obj) -> dict:
    row: dict = {}
    modules = []
    for graph_prop in ("update_script", "UpdateScript", "spawn_script", "SpawnScript"):
        if safe_prop(asset_obj, graph_prop, None):
            modules.append(graph_prop)
    if modules:
        row["behavior_nodes"] = modules
    return row


def _collect_niagara_parameter_collection(asset_obj) -> dict:
    row: dict = {}
    params = []
    for prop in ("parameters", "Parameters"):
        for item in coerce_list(safe_prop(asset_obj, prop, None))[:MAX_ITEMS]:
            name = safe_prop(item, "name", None) or safe_prop(item, "Name", None)
            if name:
                params.append(str(name))
    if params:
        row["user_parameters"] = params
    return row


def _collect_niagara_script(asset_obj) -> dict:
    row: dict = {}
    usage = safe_prop(asset_obj, "usage", None) or safe_prop(asset_obj, "Usage", None)
    if usage is not None:
        row["properties"] = {"usage": value_to_text(usage)}
    return row


def _collect_particle_system(asset_obj) -> dict:
    row: dict = {}
    emitters = []
    for emitter in coerce_list(safe_prop(asset_obj, "emitters", None) or safe_prop(asset_obj, "Emitters", None))[:MAX_ITEMS]:
        emitters.append(safe_name(emitter))
    if emitters:
        row["emitters"] = emitters
    return row


def _collect_blackboard(asset_obj) -> dict:
    row: dict = {}
    keys = []
    for prop in ("keys", "Keys"):
        for item in coerce_list(safe_prop(asset_obj, prop, None))[:MAX_ITEMS]:
            entry_name = safe_prop(item, "entry_name", None) or safe_prop(item, "EntryName", None)
            key_type = safe_prop(item, "key_type", None) or safe_prop(item, "KeyType", None)
            if entry_name:
                keys.append({"name": str(entry_name), "type": value_to_text(key_type)})
    if keys:
        row["blackboard_keys"] = keys
    return row


def _collect_behavior_tree(asset_obj) -> dict:
    row: dict = {}
    blackboard = safe_prop(asset_obj, "blackboard_asset", None) or safe_prop(asset_obj, "BlackboardAsset", None)
    if blackboard:
        row["parent_class"] = safe_name(blackboard)
    root = safe_prop(asset_obj, "root_node", None) or safe_prop(asset_obj, "RootNode", None)
    if root:
        row["behavior_nodes"] = [{"name": safe_name(root), "class": root.__class__.__name__}]
    return row


def _collect_env_query(asset_obj) -> dict:
    row: dict = {}
    queries = []
    for prop in ("queries", "Queries"):
        for item in coerce_list(safe_prop(asset_obj, prop, None))[:MAX_ITEMS]:
            option_name = safe_prop(item, "option_name", None) or safe_prop(item, "OptionName", None)
            if option_name:
                queries.append(str(option_name))
    if queries:
        row["behavior_nodes"] = queries
    return row


def _collect_smart_object(asset_obj) -> dict:
    row: dict = {}
    slots = []
    for prop in ("definition_slots", "DefinitionSlots", "slots", "Slots"):
        for item in coerce_list(safe_prop(asset_obj, prop, None))[:MAX_ITEMS]:
            slots.append(safe_name(item) or value_to_text(item))
    if slots:
        row["properties"] = {"slots": slots}
    return row


def _collect_nav_modifier(asset_obj) -> dict:
    row: dict = {}
    area = safe_prop(asset_obj, "area_class", None) or safe_prop(asset_obj, "AreaClass", None)
    if area:
        row["properties"] = {"area_class": safe_name(area)}
    return row


def _collect_sound_cue(asset_obj) -> dict:
    row: dict = {}
    children = []
    for prop in ("all_nodes", "AllNodes", "first_node", "FirstNode"):
        value = safe_prop(asset_obj, prop, None)
        if value is None:
            continue
        nodes = coerce_list(value) if prop.lower().startswith("all") else [value]
        for node in nodes[:MAX_ITEMS]:
            children.append({"name": safe_name(node), "class": node.__class__.__name__})
    if children:
        row["sound_nodes"] = children
    return row


def _collect_sound_wave(asset_obj) -> dict:
    row: dict = {}
    for key, props in (
        ("duration", ("duration", "Duration", "total_samples", "TotalSamples")),
        ("compression", ("compression_quality", "CompressionQuality")),
    ):
        for prop in props:
            value = safe_prop(asset_obj, prop, None)
            if value is not None:
                row[key] = value_to_text(value)
                break
    return row


def _collect_metasound(asset_obj) -> dict:
    row: dict = {}
    graph = safe_prop(asset_obj, "root_graph", None) or safe_prop(asset_obj, "RootGraph", None)
    if graph:
        row["graphs"] = [safe_name(graph)]
    return row


def _collect_sound_class(asset_obj) -> dict:
    row: dict = {}
    parent = safe_prop(asset_obj, "parent", None) or safe_prop(asset_obj, "Parent", None)
    if parent:
        row["parent_class"] = safe_name(parent)
    return row


def _collect_sound_mix(asset_obj) -> dict:
    row: dict = {}
    entries = []
    for prop in ("sound_class_mixes", "SoundClassMixes"):
        for item in coerce_list(safe_prop(asset_obj, prop, None))[:MAX_ITEMS]:
            sound_class = safe_prop(item, "sound_class", None) or safe_prop(item, "SoundClass", None)
            if sound_class:
                entries.append(safe_name(sound_class))
    if entries:
        row["properties"] = {"sound_classes": entries}
    return row


def _collect_sound_attenuation(asset_obj) -> dict:
    row: dict = {}
    for key in ("falloff_distance", "FalloffDistance", "distance_algorithm", "DistanceAlgorithm"):
        value = safe_prop(asset_obj, key, None)
        if value is not None:
            row["properties"] = {key: value_to_text(value)}
            break
    return row


def _collect_input_action(asset_obj) -> dict:
    row: dict = {}
    value_type = safe_prop(asset_obj, "value_type", None) or safe_prop(asset_obj, "ValueType", None)
    if value_type is not None:
        row["value_type"] = str(value_type)
    triggers = []
    for prop in ("triggers", "Triggers"):
        for item in coerce_list(safe_prop(asset_obj, prop, None))[:MAX_ITEMS]:
            triggers.append(item.__class__.__name__)
    if triggers:
        row["properties"] = {"triggers": triggers}
    return row


def _collect_input_mapping_context(asset_obj) -> dict:
    row: dict = {}
    mappings = []
    for prop in ("mappings", "Mappings"):
        for item in coerce_list(safe_prop(asset_obj, prop, None))[:MAX_ITEMS]:
            action = safe_prop(item, "action", None) or safe_prop(item, "Action", None)
            key = safe_prop(item, "key", None) or safe_prop(item, "Key", None)
            mappings.append({"action": safe_name(action) or value_to_text(action), "key": value_to_text(key)})
    if mappings:
        row["input_mappings"] = mappings
    return row


def _collect_input_modifier(asset_obj) -> dict:
    return {"parent_class": asset_obj.__class__.__name__}


def _collect_physical_material(asset_obj) -> dict:
    row: dict = {}
    for key, props in (
        ("friction", ("friction", "Friction")),
        ("restitution", ("restitution", "Restitution")),
        ("surface_type", ("surface_type", "SurfaceType")),
    ):
        for prop in props:
            value = safe_prop(asset_obj, prop, None)
            if value is not None:
                row[key] = value_to_text(value)
                break
    return row


def _collect_gameplay_asset(asset_obj) -> dict:
    row: dict = {}
    parent = safe_prop(asset_obj, "parent_class", None) or safe_prop(asset_obj, "ParentClass", None)
    if parent:
        row["parent_class"] = safe_name(parent)
    tags = []
    for prop in ("ability_tags", "AbilityTags", "asset_tags", "AssetTags", "gameplay_cue_tags", "GameplayCueTags"):
        container = safe_prop(asset_obj, prop, None)
        if container is None:
            continue
        for tag in coerce_list(safe_prop(container, "gameplay_tags", None) or safe_prop(container, "GameplayTags", None))[:MAX_ITEMS]:
            tags.append(value_to_text(tag))
    if tags:
        row["tags"] = tags[:MAX_ITEMS]
    return row


def _collect_data_asset(asset_obj) -> dict:
    return {"parent_class": safe_name(asset_obj.get_class())}


def _collect_font(asset_obj) -> dict:
    row: dict = {}
    for key in ("font_name", "FontName", "legacy_font_name", "LegacyFontName"):
        value = safe_prop(asset_obj, key, None)
        if value is not None:
            row["properties"] = {key: value_to_text(value)}
            break
    return row


def _collect_media_player(asset_obj) -> dict:
    row: dict = {}
    source = safe_prop(asset_obj, "source", None) or safe_prop(asset_obj, "Source", None)
    if source:
        row["parent_class"] = safe_name(source)
    return row


def _collect_media_source(asset_obj) -> dict:
    row: dict = {}
    for key in ("file_path", "FilePath", "url", "Url"):
        value = safe_prop(asset_obj, key, None)
        if value:
            row["properties"] = {key: str(value)}
            break
    return row


COLLECTORS = {
    "DataTable": _collect_data_table,
    "DataAsset": lambda u, o, c: _collect_data_asset(o),
    "PrimaryDataAsset": lambda u, o, c: _collect_data_asset(o),
    "CurveFloat": lambda u, o, c: _collect_curve(o, c),
    "CurveVector": lambda u, o, c: _collect_curve(o, c),
    "CurveLinearColor": lambda u, o, c: _collect_curve(o, c),
    "CurveTable": lambda u, o, c: _collect_curve_table(o),
    "StringTable": lambda u, o, c: _collect_string_table(o),
    "DataRegistry": lambda u, o, c: _collect_data_registry(o),
    "UserDefinedEnum": lambda u, o, c: _collect_user_defined_enum(o),
    "NiagaraSystem": lambda u, o, c: _collect_niagara_system(o),
    "NiagaraEmitter": lambda u, o, c: _collect_niagara_emitter(o),
    "NiagaraParameterCollection": lambda u, o, c: _collect_niagara_parameter_collection(o),
    "NiagaraScript": lambda u, o, c: _collect_niagara_script(o),
    "ParticleSystem": lambda u, o, c: _collect_particle_system(o),
    "BehaviorTree": lambda u, o, c: _collect_behavior_tree(o),
    "BlackboardData": lambda u, o, c: _collect_blackboard(o),
    "EnvQuery": lambda u, o, c: _collect_env_query(o),
    "SmartObjectDefinition": lambda u, o, c: _collect_smart_object(o),
    "NavModifierVolume": lambda u, o, c: _collect_nav_modifier(o),
    "SoundCue": lambda u, o, c: _collect_sound_cue(o),
    "SoundWave": lambda u, o, c: _collect_sound_wave(o),
    "MetaSoundSource": lambda u, o, c: _collect_metasound(o),
    "MetaSoundPatch": lambda u, o, c: _collect_metasound(o),
    "SoundClass": lambda u, o, c: _collect_sound_class(o),
    "SoundMix": lambda u, o, c: _collect_sound_mix(o),
    "SoundAttenuation": lambda u, o, c: _collect_sound_attenuation(o),
    "InputAction": lambda u, o, c: _collect_input_action(o),
    "InputMappingContext": lambda u, o, c: _collect_input_mapping_context(o),
    "InputModifier": lambda u, o, c: _collect_input_modifier(o),
    "InputTrigger": lambda u, o, c: _collect_input_modifier(o),
    "PhysicalMaterial": lambda u, o, c: _collect_physical_material(o),
    "GameplayAbility": lambda u, o, c: _collect_gameplay_asset(o),
    "GameplayEffect": lambda u, o, c: _collect_gameplay_asset(o),
    "GameplayCueNotify_Static": lambda u, o, c: _collect_gameplay_asset(o),
    "Font": lambda u, o, c: _collect_font(o),
    "FontFace": lambda u, o, c: _collect_font(o),
    "MediaPlayer": lambda u, o, c: _collect_media_player(o),
    "FileMediaSource": lambda u, o, c: _collect_media_source(o),
    "ImgMediaSource": lambda u, o, c: _collect_media_source(o),
}


def export_structured_asset_metadata(content_path: str, out_path: str) -> None:
    export_by_class_map(
        content_path,
        out_path,
        export_classes=STRUCTURED_EXPORT_CLASSES,
        collectors=COLLECTORS,
        log_label="structured",
    )
