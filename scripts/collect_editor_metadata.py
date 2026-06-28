#!/usr/bin/env python
"""Collect editor-exported metadata into RAG raw JSONL (Phase 16)."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


SOURCE_MAP = {
    "blueprint": "unreal_blueprint_metadata",
    "material": "unreal_material_metadata",
    "animation": "unreal_animation_metadata",
    "skeletal_mesh": "unreal_skeletal_mesh_metadata",
    "anim_blueprint": "unreal_anim_blueprint_metadata",
    "anim_montage": "unreal_anim_montage_metadata",
    "sequencer": "unreal_sequencer_metadata",
    "asset_registry": "unreal_asset_registry",
    "project_settings": "unreal_project_settings",
    "level": "unreal_level_metadata",
}


UASSET_SOURCES = {
    "unreal_blueprint_metadata",
    "unreal_material_metadata",
    "unreal_animation_metadata",
    "unreal_skeletal_mesh_metadata",
    "unreal_anim_blueprint_metadata",
    "unreal_anim_montage_metadata",
    "unreal_sequencer_metadata",
    "unreal_asset_registry",
    "unreal_level_metadata",
}


ANIMATION_ASSET_SOURCE_MAP = {
    "SkeletalMesh": "unreal_skeletal_mesh_metadata",
    "AnimBlueprint": "unreal_anim_blueprint_metadata",
    "AnimMontage": "unreal_anim_montage_metadata",
    "LevelSequence": "unreal_sequencer_metadata",
}


def parse_export_spec(spec: str) -> tuple[Path, str]:
    path_str, sep, kind = spec.rpartition(":")
    if not sep:
        raise ValueError(f"Invalid export spec, expected path:type: {spec}")
    if not path_str or not kind:
        raise ValueError(f"Invalid export spec, expected path:type: {spec}")
    return Path(path_str), kind


def source_for_row(source_key: str, row: dict[str, Any]) -> str:
    if source_key == "animation":
        asset_type = str(row.get("asset_type") or "")
        return ANIMATION_ASSET_SOURCE_MAP.get(asset_type, "unreal_animation_metadata")
    return SOURCE_MAP.get(source_key, source_key)


def row_to_chunk(source: str, row: dict[str, Any], project: str) -> dict[str, Any]:
    path = str(row.get("asset_path") or row.get("path") or row.get("map_path") or project)
    title = str(row.get("title") or row.get("generated_class") or row.get("key") or path)
    text_parts = [f"{source} metadata: {title}"]
    for key in (
        "asset_type",
        "parent_class",
        "generated_class",
        "skeleton",
        "skeletal_mesh",
        "physics_asset",
        "parent_material",
        "blend_mode",
        "shading_model",
        "sequence_length",
        "rate_scale",
        "frame_rate",
        "game_mode",
        "setting",
        "value",
    ):
        if row.get(key):
            text_parts.append(f"{key}: {row[key]}")
    for key in (
        "components",
        "variables",
        "functions",
        "interfaces",
        "scalar_parameters",
        "vector_parameters",
        "texture_parameters",
        "static_switch_parameters",
        "graphs",
        "nodes",
        "pins",
        "expressions",
        "materials",
        "notifies",
        "notify_tracks",
        "montage_sections",
        "slots",
        "bindings",
        "tracks",
        "dependencies",
    ):
        if row.get(key):
            text_parts.append(f"{key}: {row[key]}")
    text = "\n".join(text_parts)
    chunk_id = hashlib.sha1(f"{source}|{path}|{title}".encode()).hexdigest()
    return {
        "id": chunk_id,
        "source": source,
        "path": path,
        "title": title,
        "text": text,
        "metadata": {**row, "project": project, "extension": ".uasset" if source in UASSET_SOURCES else ".ini"},
    }


def ingest_export(export_path: Path, source_key: str, project: str, out_handle) -> int:
    count = 0
    for line in export_path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        row = json.loads(line)
        source = source_for_row(source_key, row)
        chunk = row_to_chunk(source, row, project)
        out_handle.write(json.dumps(chunk, ensure_ascii=False) + "\n")
        count += 1
    return count


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect editor metadata exports.")
    parser.add_argument("--export", action="append", default=[], help="path:type e.g. C:/x/bp.jsonl:blueprint")
    parser.add_argument("--project-name", required=True)
    parser.add_argument("--out-dir", default="data/unreal58")
    args = parser.parse_args()

    root = Path(__file__).resolve().parent.parent
    out_dir = root / args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    totals: dict[str, int] = {}
    for spec in args.export:
        try:
            path, kind = parse_export_spec(spec)
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
        if not path.is_file():
            print(f"SKIP missing: {path}")
            continue
        out_name = f"raw_{kind}_metadata.jsonl" if kind != "asset_registry" else "raw_asset_registry.jsonl"
        if kind == "project_settings":
            out_name = "raw_project_settings.jsonl"
        if kind == "level":
            out_name = "raw_level_metadata.jsonl"
        out_path = out_dir / out_name
        with out_path.open("a", encoding="utf-8") as handle:
            n = ingest_export(path, kind, args.project_name, handle)
        totals[kind] = totals.get(kind, 0) + n
        print(f"Ingested {n} rows -> {out_path}")
    print(json.dumps(totals, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
