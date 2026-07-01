# Run inside Unreal Editor Python.
# export_world_look_metadata('/Game', r'C:\export\world_look.jsonl')

from export_common import export_by_class_map, safe_prop, value_to_text

WORLD_LOOK_EXPORT_CLASSES = frozenset(
    {
        "PostProcessVolume",
        "SkyAtmosphere",
        "ExponentialHeightFog",
        "VolumetricCloud",
        "DataLayerAsset",
    }
)


def _collect_post_process(_unreal, asset_obj, cls: str) -> dict:
    row: dict = {}
    settings = safe_prop(asset_obj, "settings", None) or safe_prop(asset_obj, "Settings", None)
    if settings:
        summary = {}
        for key in (
            "bloom_intensity",
            "auto_exposure_bias",
            "color_saturation",
            "color_contrast",
            "color_gamma",
            "motion_blur_amount",
            "vignette_intensity",
        ):
            value = safe_prop(settings, key, None)
            if value is not None:
                summary[key] = value_to_text(value)
        if summary:
            row["post_process_settings"] = summary
    blendables = safe_prop(asset_obj, "blendables", None) or safe_prop(asset_obj, "Blendables", None)
    if blendables:
        row["properties"] = {"blendable_count": len(list(blendables))}
    return row


def _collect_sky_atmosphere(_unreal, asset_obj, cls: str) -> dict:
    row: dict = {}
    for key in ("rayleigh_scattering", "mie_scattering", "mie_absorption", "ground_albedo"):
        value = safe_prop(asset_obj, key, None)
        if value is not None:
            row.setdefault("properties", {})[key] = value_to_text(value)
    return row


def _collect_height_fog(_unreal, asset_obj, cls: str) -> dict:
    row: dict = {}
    for key in ("fog_density", "fog_height_falloff", "fog_inscattering_color", "start_distance"):
        value = safe_prop(asset_obj, key, None)
        if value is not None:
            row.setdefault("properties", {})[key] = value_to_text(value)
    return row


def _collect_volumetric_cloud(_unreal, asset_obj, cls: str) -> dict:
    row: dict = {}
    for key in ("layer_bottom_altitude", "layer_height", "tracer_start_max_distance"):
        value = safe_prop(asset_obj, key, None)
        if value is not None:
            row.setdefault("properties", {})[key] = value_to_text(value)
    return row


def _collect_data_layer(_unreal, asset_obj, cls: str) -> dict:
    row: dict = {}
    for key in ("data_layer_label", "DataLayerLabel", "initial_runtime_state", "InitialRuntimeState"):
        value = safe_prop(asset_obj, key, None)
        if value is not None:
            row["properties"] = {key: value_to_text(value)}
            break
    return row


COLLECTORS = {
    "PostProcessVolume": _collect_post_process,
    "SkyAtmosphere": _collect_sky_atmosphere,
    "ExponentialHeightFog": _collect_height_fog,
    "VolumetricCloud": _collect_volumetric_cloud,
    "DataLayerAsset": _collect_data_layer,
}


def export_world_look_metadata(content_path: str, out_path: str) -> None:
    export_by_class_map(
        content_path,
        out_path,
        export_classes=WORLD_LOOK_EXPORT_CLASSES,
        collectors=COLLECTORS,
        log_label="world look",
    )
