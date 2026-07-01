#!/usr/bin/env python
"""Ingest Editor-exported JSONL files from a directory into RAG raw inputs."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from workspace_paths import active_project_names, find_workspace_root, load_shared_config

EXPORT_PATTERNS: tuple[tuple[str, str], ...] = (
    ("blueprint*.jsonl", "blueprint"),
    ("bp*.jsonl", "blueprint"),
    ("material*.jsonl", "material"),
    ("texture*.jsonl", "texture"),
    ("mesh*.jsonl", "mesh"),
    ("meshes*.jsonl", "mesh"),
    ("world_look*.jsonl", "world_look"),
    ("animation*.jsonl", "animation"),
    ("structured*.jsonl", "structured"),
    ("fmod*.jsonl", "fmod"),
    ("skeletal*.jsonl", "skeletal_mesh"),
    ("anim_blueprint*.jsonl", "anim_blueprint"),
    ("anim_montage*.jsonl", "anim_montage"),
    ("montage*.jsonl", "anim_montage"),
    ("sequencer*.jsonl", "sequencer"),
    ("level_sequence*.jsonl", "sequencer"),
    ("asset_registry*.jsonl", "asset_registry"),
    ("project_settings*.jsonl", "project_settings"),
    ("level*.jsonl", "level"),
)


def resolve_project_name(explicit: str) -> str:
    if explicit.strip():
        return explicit.strip()
    names = active_project_names()
    if names:
        return names[0]
    config = load_shared_config()
    active = str(config.get("activeProject") or "").strip()
    if active:
        return Path(active).stem
    return "Project"


def discover_exports(export_dir: Path) -> list[tuple[Path, str]]:
    found: list[tuple[Path, str]] = []
    seen: set[str] = set()
    if not export_dir.is_dir():
        return found
    for pattern, kind in EXPORT_PATTERNS:
        for path in sorted(export_dir.glob(pattern)):
            if not path.is_file():
                continue
            key = f"{kind}:{path.resolve()}"
            if key in seen:
                continue
            seen.add(key)
            found.append((path.resolve(), kind))
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest Editor export JSONL files from a directory.")
    parser.add_argument("--export-dir", required=True, help="Directory containing Editor export JSONL files.")
    parser.add_argument("--out-dir", default="data/unreal58")
    parser.add_argument("--project-name", default="")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    workspace = find_workspace_root()
    export_dir = Path(args.export_dir).expanduser().resolve()
    out_dir = Path(args.out_dir)
    if not out_dir.is_absolute():
        out_dir = workspace / out_dir
    project_name = resolve_project_name(args.project_name)

    exports = discover_exports(export_dir)
    if not exports:
        print(f"[warn] no export JSONL files found under: {export_dir}")
        print("Expected names like blueprints.jsonl, materials.jsonl, animation.jsonl")
        return 0

    collector = workspace / "scripts" / "collect_editor_metadata.py"
    ingested = 0
    for path, kind in exports:
        spec = f"{path}:{kind}"
        print(f"[ingest] {spec}")
        if args.dry_run:
            ingested += 1
            continue
        cmd = [
            sys.executable,
            str(collector),
            "--project-name",
            project_name,
            "--out-dir",
            str(out_dir),
            "--export",
            spec,
        ]
        result = subprocess.run(cmd, cwd=str(workspace), check=False)
        if result.returncode != 0:
            print(f"[fail] ingest failed for {path}")
            return result.returncode
        ingested += 1

    print(f"done: ingested {ingested} export file(s) from {export_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
