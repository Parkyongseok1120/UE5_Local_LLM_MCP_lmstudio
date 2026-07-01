# Run inside Unreal Editor Python.
# export_texture_metadata('/Game', r'C:\export\textures.jsonl')

from export_common import MAX_ITEMS, export_by_class_map, safe_prop, value_to_text

TEXTURE_EXPORT_CLASSES = frozenset(
    {
        "Texture2D",
        "TextureCube",
        "TextureRenderTarget",
        "TextureRenderTarget2D",
        "MediaTexture",
        "RuntimeVirtualTexture",
        "VirtualTextureBuilder",
    }
)


def _collect_texture(_unreal, asset_obj, cls: str) -> dict:
    row: dict = {}
    for key, props in (
        ("width", ("source_size_x", "SourceSizeX", "size_x", "SizeX")),
        ("height", ("source_size_y", "SourceSizeY", "size_y", "SizeY")),
        ("srgb", ("srgb", "SRGB")),
        ("compression", ("compression_settings", "CompressionSettings")),
        ("mip_gen_settings", ("mip_gen_settings", "MipGenSettings")),
        ("lod_group", ("lod_group", "LODGroup")),
    ):
        for prop in props:
            value = safe_prop(asset_obj, prop, None)
            if value is not None:
                row[key] = value_to_text(value)
                break
    virtual_streaming = safe_prop(asset_obj, "virtual_texture_streaming", None)
    if virtual_streaming is not None:
        row["virtual_texture_streaming"] = value_to_text(virtual_streaming)
    source_file = safe_prop(asset_obj, "source_file_path", None) or safe_prop(asset_obj, "SourceFilePath", None)
    if source_file:
        row["source_file"] = str(source_file)
    return row


COLLECTORS = {cls: _collect_texture for cls in TEXTURE_EXPORT_CLASSES}


def export_texture_metadata(content_path: str, out_path: str) -> None:
    export_by_class_map(
        content_path,
        out_path,
        export_classes=TEXTURE_EXPORT_CLASSES,
        collectors=COLLECTORS,
        log_label="texture",
    )
