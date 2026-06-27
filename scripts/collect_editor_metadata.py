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
    "asset_registry": "unreal_asset_registry",
    "project_settings": "unreal_project_settings",
    "level": "unreal_level_metadata",
}


def row_to_chunk(source: str, row: dict[str, Any], project: str) -> dict[str, Any]:
    path = str(row.get("asset_path") or row.get("path") or row.get("map_path") or project)
    title = str(row.get("title") or row.get("generated_class") or row.get("key") or path)
    text_parts = [f"{source} metadata: {title}"]
    for key in ("asset_type", "parent_class", "generated_class", "game_mode", "setting", "value"):
        if row.get(key):
            text_parts.append(f"{key}: {row[key]}")
    if row.get("components"):
        text_parts.append(f"components: {row['components']}")
    if row.get("variables"):
        text_parts.append(f"variables: {row['variables']}")
    if row.get("functions"):
        text_parts.append(f"functions: {row['functions']}")
    text = "\n".join(text_parts)
    chunk_id = hashlib.sha1(f"{source}|{path}|{title}".encode()).hexdigest()
    return {
        "id": chunk_id,
        "source": source,
        "path": path,
        "title": title,
        "text": text,
        "metadata": {**row, "project": project, "extension": ".uasset" if "asset" in source else ".ini"},
    }


def ingest_export(export_path: Path, source_key: str, project: str, out_handle) -> int:
    source = SOURCE_MAP.get(source_key, source_key)
    count = 0
    for line in export_path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        row = json.loads(line)
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
        path_str, _, kind = spec.partition(":")
        path = Path(path_str)
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
