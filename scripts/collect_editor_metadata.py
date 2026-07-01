#!/usr/bin/env python
"""Collect editor-exported metadata into RAG raw JSONL (Phase 16)."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from material_graph_format import append_material_graph_text_parts
from blueprint_graph_format import append_blueprint_graph_text_parts


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
        "scalar_parameter_values",
        "vector_parameter_values",
        "texture_parameter_values",
        "static_switch_parameter_values",
        "graphs",
        "nodes",
        "pins",
        "materials",
        "notifies",
        "montage_sections",
        "slots",
        "bindings",
        "tracks",
        "dependencies",
        "graph_source",
    ):
        if row.get(key):
            text_parts.append(f"{key}: {row[key]}")
    append_material_graph_text_parts(row, text_parts)
    append_blueprint_graph_text_parts(row, text_parts)
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


def _chunk_asset_key(chunk: dict[str, Any]) -> str:
    meta = chunk.get("metadata")
    if isinstance(meta, dict):
        path = str(meta.get("asset_path") or chunk.get("path") or "")
        if path:
            return path.lower()
    return str(chunk.get("id") or chunk.get("path") or chunk.get("title") or "")


def _load_existing_chunks(out_path: Path) -> dict[str, dict[str, Any]]:
    existing: dict[str, dict[str, Any]] = {}
    if not out_path.is_file():
        return existing
    for line in out_path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            chunk = json.loads(line)
        except json.JSONDecodeError:
            continue
        key = _chunk_asset_key(chunk)
        if key:
            existing[key] = chunk
    return existing


def ingest_export(export_path: Path, source_key: str, project: str) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    for line in export_path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        row = json.loads(line)
        source = source_for_row(source_key, row)
        chunks.append(row_to_chunk(source, row, project))
    return chunks


def merge_export_into_raw(
    export_path: Path,
    source_key: str,
    project: str,
    out_path: Path,
    *,
    replace_project: bool = True,
) -> tuple[int, int]:
    incoming = ingest_export(export_path, source_key, project)
    existing = _load_existing_chunks(out_path) if replace_project else {}
    if replace_project:
        project_keys = {
            key
            for key, chunk in existing.items()
            if str((chunk.get("metadata") or {}).get("project") or chunk.get("project") or "") in {"", project}
        }
        kept = {
            key: chunk
            for key, chunk in existing.items()
            if key not in project_keys
        }
    else:
        kept = dict(existing)
        project_keys = set()

    replaced = 0
    for chunk in incoming:
        key = _chunk_asset_key(chunk)
        if key in project_keys:
            replaced += 1
        kept[key or str(chunk.get("id"))] = chunk

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as handle:
        for chunk in kept.values():
            handle.write(json.dumps(chunk, ensure_ascii=False) + "\n")
    return len(incoming), replaced


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect editor metadata exports.")
    parser.add_argument("--export", action="append", default=[], help="path:type e.g. C:/x/bp.jsonl:blueprint")
    parser.add_argument("--project-name", required=True)
    parser.add_argument("--out-dir", default="data/unreal58")
    parser.add_argument(
        "--replace-project",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Replace rows for the same project/asset_path instead of appending duplicates.",
    )
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
        ingested, replaced = merge_export_into_raw(
            path,
            kind,
            args.project_name,
            out_path,
            replace_project=args.replace_project,
        )
        totals[kind] = totals.get(kind, 0) + ingested
        print(f"Ingested {ingested} rows ({replaced} replaced) -> {out_path}")
    print(json.dumps(totals, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
