# Run inside Unreal Editor Python.
# Exports all supported metadata JSONL files in one call.
#
# Usage:
#   exec(open(r'path/to/tools/ue_export/run_all_exports.py', encoding='utf-8').read())
#   run_all_metadata_exports(r'C:\UnrealExports', content_path='/Game')
#   run_all_metadata_exports(r'C:\UnrealExports', content_path='/Game/06_Environment/BossStage')

import json
import os


DEFAULT_EXPORTS = (
    ("export_blueprint_metadata.py", "export_blueprint_metadata", "blueprints.jsonl"),
    ("export_material_metadata.py", "export_material_metadata", "materials.jsonl"),
    ("export_texture_metadata.py", "export_texture_metadata", "textures.jsonl"),
    ("export_mesh_metadata.py", "export_mesh_metadata", "meshes.jsonl"),
    ("export_world_look_metadata.py", "export_world_look_metadata", "world_look.jsonl"),
    ("export_structured_asset_metadata.py", "export_structured_asset_metadata", "structured.jsonl"),
    ("export_animation_metadata.py", "export_animation_metadata", "animation.jsonl"),
    ("export_fmod_metadata.py", "export_fmod_metadata", "fmod.jsonl"),
    ("export_asset_registry.py", "export_asset_registry", "asset_registry.jsonl"),
    ("export_project_settings.py", "export_project_settings", "project_settings.jsonl"),
    ("export_level_metadata.py", "export_level_metadata", "level.jsonl"),
)

try:
    _TOOLS_DIR
except NameError:
    _TOOLS_DIR = ""


def _tools_dir(explicit: str = "") -> str:
    if explicit:
        return explicit
    if _TOOLS_DIR:
        return _TOOLS_DIR
    raise RuntimeError(
        "tools_dir is required when run_all_exports.py is exec()'d in Unreal Editor Python"
    )


def _load_module(script_name: str, tools_dir: str = ""):
    root = _tools_dir(tools_dir)
    if not root:
        raise RuntimeError("tools_dir is required when run_all_exports.py is exec()'d without __file__")
    script_path = os.path.join(root, script_name)
    namespace = {}
    with open(script_path, encoding="utf-8") as handle:
        exec(handle.read(), namespace)
    return namespace


def run_all_metadata_exports(
    export_dir: str,
    content_path: str = "/Game",
    maps_path: str = "",
    include: tuple[str, ...] = (),
    exclude: tuple[str, ...] = (),
    tools_dir: str = "",
) -> dict:
    global _TOOLS_DIR
    if tools_dir:
        _TOOLS_DIR = tools_dir
    os.makedirs(export_dir, exist_ok=True)
    maps_root = maps_path or content_path
    results = []
    for script_name, function_name, output_name in DEFAULT_EXPORTS:
        stem = output_name.replace(".jsonl", "")
        if include and stem not in include and script_name not in include:
            continue
        if exclude and (stem in exclude or script_name in exclude):
            continue
        out_path = os.path.join(export_dir, output_name)
        module = _load_module(script_name, tools_dir)
        export_fn = module[function_name]
        if script_name == "export_level_metadata.py":
            export_fn(maps_root, out_path)
        elif script_name == "export_project_settings.py":
            export_fn(out_path)
        else:
            export_fn(content_path, out_path)
        size_bytes = os.path.getsize(out_path) if os.path.isfile(out_path) else 0
        results.append(
            {
                "script": script_name,
                "output": out_path,
                "sizeBytes": size_bytes,
            }
        )
    manifest_path = os.path.join(export_dir, "export_manifest.json")
    manifest = {
        "contentPath": content_path,
        "mapsPath": maps_root,
        "exports": results,
    }
    with open(manifest_path, "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, ensure_ascii=False, indent=2)
    print(f"Exported {len(results)} metadata file(s) to {export_dir}")
    for item in results:
        print(f"- {item['output']} ({item['sizeBytes']} bytes)")
    return manifest


def export_materials_only(export_dir: str, content_path: str = "/Game", tools_dir: str = "") -> dict:
    return run_all_metadata_exports(
        export_dir,
        content_path=content_path,
        include=("materials",),
        tools_dir=tools_dir,
    )


def export_blueprints_only(export_dir: str, content_path: str = "/Game", tools_dir: str = "") -> dict:
    return run_all_metadata_exports(
        export_dir,
        content_path=content_path,
        include=("blueprints",),
        tools_dir=tools_dir,
    )
