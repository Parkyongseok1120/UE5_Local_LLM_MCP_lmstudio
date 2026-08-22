# Run inside Unreal Editor Python.
# Exports all supported metadata JSONL files in one call.
#
# Usage:
#   exec(open(r'path/to/tools/ue_export/run_all_exports.py', encoding='utf-8').read())
#   run_all_metadata_exports(r'C:\UnrealExports', content_path='/Game')
#   run_all_metadata_exports(r'C:\UnrealExports', content_path='/Game/Environment/ExampleArea')

import json
import hashlib
import os
import time
import uuid


DEFAULT_EXPORTS = (
    ("export_blueprint_metadata.py", "export_blueprint_metadata", "blueprints.jsonl", "blueprint"),
    ("export_material_metadata.py", "export_material_metadata", "materials.jsonl", "material"),
    ("export_texture_metadata.py", "export_texture_metadata", "textures.jsonl", "texture"),
    ("export_mesh_metadata.py", "export_mesh_metadata", "meshes.jsonl", "mesh"),
    ("export_world_look_metadata.py", "export_world_look_metadata", "world_look.jsonl", "world_look"),
    ("export_structured_asset_metadata.py", "export_structured_asset_metadata", "structured.jsonl", "structured"),
    ("export_animation_metadata.py", "export_animation_metadata", "animation.jsonl", "animation"),
    ("export_fmod_metadata.py", "export_fmod_metadata", "fmod.jsonl", "fmod"),
    ("export_asset_registry.py", "export_asset_registry", "asset_registry.jsonl", "asset_registry"),
    ("export_project_settings.py", "export_project_settings", "project_settings.jsonl", "project_settings"),
    ("export_level_metadata.py", "export_level_metadata", "level.jsonl", "level"),
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
    import sys

    if root not in sys.path:
        sys.path.insert(0, root)
    script_path = os.path.join(root, script_name)
    namespace = {}
    with open(script_path, encoding="utf-8") as handle:
        exec(handle.read(), namespace)
    return namespace


def _path_identity(value: str) -> str:
    return os.path.normcase(os.path.realpath(os.path.abspath(value)))


def _actual_project_file(requested_project_file: str) -> str:
    import unreal

    actual = os.path.realpath(os.path.abspath(str(unreal.Paths.get_project_file_path())))
    if not requested_project_file:
        raise RuntimeError("Exact requested_project_file is required for an Editor export")
    if _path_identity(actual) != _path_identity(requested_project_file):
        raise RuntimeError(
            "The running Unreal Editor project does not match the requested .uproject"
        )
    return actual


def _file_facts(path: str) -> dict:
    digest = hashlib.sha256()
    row_count = 0
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    with open(path, encoding="utf-8-sig") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                raise RuntimeError(f"Editor export row must be an object: {path}")
            row_count += 1
    return {
        "sizeBytes": os.path.getsize(path),
        "sha256": digest.hexdigest(),
        "rowCount": row_count,
    }


def run_all_metadata_exports(
    export_dir: str,
    content_path: str = "/Game",
    maps_path: str = "",
    include: tuple[str, ...] = (),
    exclude: tuple[str, ...] = (),
    tools_dir: str = "",
    requested_project_file: str = "",
    run_id: str = "",
    scope: str = "all",
) -> dict:
    global _TOOLS_DIR
    if tools_dir:
        _TOOLS_DIR = tools_dir
    os.makedirs(export_dir, exist_ok=True)
    manifest_path = os.path.join(export_dir, "export_manifest.json")
    if os.path.isfile(manifest_path):
        os.remove(manifest_path)
    project_file = _actual_project_file(requested_project_file)
    maps_root = maps_path or content_path
    results = []
    for script_name, function_name, output_name, kind in DEFAULT_EXPORTS:
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
        if not os.path.isfile(out_path):
            raise RuntimeError(f"Editor exporter did not create its declared output: {out_path}")
        results.append(
            {
                "script": script_name,
                "output": out_path,
                "file": output_name,
                "kind": kind,
                **_file_facts(out_path),
            }
        )
    manifest = {
        "schemaVersion": 1,
        "complete": True,
        "runId": run_id or uuid.uuid4().hex,
        "capturedAt": time.time(),
        "projectFile": project_file,
        "projectRoot": os.path.dirname(project_file),
        "scope": scope,
        "contentPath": content_path,
        "mapsPath": maps_root,
        "exports": results,
    }
    manifest_temp = manifest_path + ".tmp"
    with open(manifest_temp, "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, ensure_ascii=False, indent=2)
    os.replace(manifest_temp, manifest_path)
    print(f"Exported {len(results)} metadata file(s) to {export_dir}")
    for item in results:
        print(f"- {item['output']} ({item['sizeBytes']} bytes)")
    return manifest


def export_materials_only(
    export_dir: str,
    content_path: str = "/Game",
    tools_dir: str = "",
    requested_project_file: str = "",
    run_id: str = "",
) -> dict:
    return run_all_metadata_exports(
        export_dir,
        content_path=content_path,
        include=("materials",),
        tools_dir=tools_dir,
        requested_project_file=requested_project_file,
        run_id=run_id,
        scope="materials",
    )


def export_blueprints_only(
    export_dir: str,
    content_path: str = "/Game",
    tools_dir: str = "",
    requested_project_file: str = "",
    run_id: str = "",
) -> dict:
    return run_all_metadata_exports(
        export_dir,
        content_path=content_path,
        include=("blueprints",),
        tools_dir=tools_dir,
        requested_project_file=requested_project_file,
        run_id=run_id,
        scope="blueprints",
    )
