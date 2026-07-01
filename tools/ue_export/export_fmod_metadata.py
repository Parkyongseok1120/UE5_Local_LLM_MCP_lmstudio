# Run inside Unreal Editor Python.
# export_fmod_metadata('/Game', r'C:\export\fmod.jsonl')

from export_common import export_by_class_map, safe_name, safe_prop, value_to_text

FMOD_EXPORT_CLASSES = frozenset(
    {
        "FMODEvent",
        "FMODBank",
        "FMODBankLookup",
        "FMODAsset",
    }
)


def _collect_fmod_event(_unreal, asset_obj, cls: str) -> dict:
    row: dict = {}
    for key in ("path", "Path", "event_name", "EventName", "asset_guid", "AssetGuid"):
        value = safe_prop(asset_obj, key, None)
        if value is not None:
            row["properties"] = {key: value_to_text(value)}
            break
    return row


def _collect_fmod_bank(_unreal, asset_obj, cls: str) -> dict:
    row: dict = {}
    for key in ("bank_name", "BankName", "path", "Path"):
        value = safe_prop(asset_obj, key, None)
        if value is not None:
            row["properties"] = {key: value_to_text(value)}
            break
    return row


def _collect_generic(_unreal, asset_obj, cls: str) -> dict:
    return {"parent_class": safe_name(asset_obj.get_class())}


COLLECTORS = {
    "FMODEvent": _collect_fmod_event,
    "FMODBank": _collect_fmod_bank,
    "FMODBankLookup": _collect_generic,
    "FMODAsset": _collect_generic,
}


def export_fmod_metadata(content_path: str, out_path: str) -> None:
    export_by_class_map(
        content_path,
        out_path,
        export_classes=FMOD_EXPORT_CLASSES,
        collectors=COLLECTORS,
        log_label="FMOD",
    )
