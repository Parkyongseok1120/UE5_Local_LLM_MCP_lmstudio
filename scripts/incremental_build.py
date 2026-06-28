#!/usr/bin/env python
"""Rebuild the RAG index only when raw inputs or manifest are stale."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from workspace_paths import find_workspace_root

RAW_INPUT_FILES = (
    "raw_guidelines.jsonl",
    "raw_game_design.jsonl",
    "raw_symbols.jsonl",
    "raw_module_graph.jsonl",
    "raw_project_profiles.jsonl",
    "raw_project_architecture.jsonl",
    "raw_blueprint_metadata.jsonl",
    "raw_material_metadata.jsonl",
    "raw_animation_metadata.jsonl",
    "raw_skeletal_mesh_metadata.jsonl",
    "raw_anim_blueprint_metadata.jsonl",
    "raw_anim_montage_metadata.jsonl",
    "raw_sequencer_metadata.jsonl",
    "raw_asset_registry.jsonl",
    "raw_project_settings.jsonl",
    "raw_level_metadata.jsonl",
    "raw_failure_memory.jsonl",
    "raw_build_logs.jsonl",
    "raw_docs.jsonl",
    "raw_source.jsonl",
    "raw_projects.jsonl",
)


def input_paths(data_dir: Path) -> list[Path]:
    return [data_dir / name for name in RAW_INPUT_FILES if (data_dir / name).exists()]


def manifest_stale(data_dir: Path, manifest_path: Path, sqlite_path: Path) -> tuple[bool, str]:
    if not sqlite_path.exists():
        return True, "index-missing"

    inputs = input_paths(data_dir)
    if not inputs:
        return False, "no-inputs"

    newest_input = max(inputs, key=lambda path: path.stat().st_mtime)
    index_mtime = sqlite_path.stat().st_mtime
    if newest_input.stat().st_mtime > index_mtime:
        return True, f"input-newer-than-index ({newest_input.name})"

    chunks_jsonl = data_dir / "chunks.jsonl"
    if chunks_jsonl.exists() and chunks_jsonl.stat().st_mtime > index_mtime:
        return True, "chunks-jsonl-newer-than-index"

    if not manifest_path.exists():
        return True, "manifest-missing"

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        return True, "manifest-invalid"

    recorded = {item["path"]: item for item in manifest.get("inputs", []) if isinstance(item, dict)}
    for path in inputs:
        resolved = str(path.resolve())
        info = recorded.get(resolved)
        if not info:
            return True, f"manifest-missing-input ({path.name})"
        if not path.exists():
            continue
        stat = path.stat()
        if int(info.get("sizeBytes") or 0) != stat.st_size:
            return True, f"input-size-changed ({path.name})"
        recorded_mtime = info.get("modifiedAt")
        if recorded_mtime:
            current = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()
            if current != recorded_mtime:
                return True, f"input-modified ({path.name})"

    workspace_root = manifest.get("workspaceRoot")
    current_root = str(find_workspace_root().resolve())
    if workspace_root and workspace_root != current_root:
        return True, "workspace-root-changed"

    return False, "up-to-date"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", type=Path, default=Path("data/unreal58"))
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    workspace = find_workspace_root()
    data_dir = (workspace / args.out_dir).resolve()
    manifest_path = data_dir / "build_manifest.json"
    sqlite_path = data_dir / "rag.sqlite"

    stale, reason = manifest_stale(data_dir, manifest_path, sqlite_path)
    if not args.force and not stale:
        print(f"skip: {reason}")
        return 0

    inputs = input_paths(data_dir)
    if not inputs:
        print("error: no raw input jsonl files found", file=sys.stderr)
        return 1

    cmd = [
        sys.executable,
        str(workspace / "scripts" / "build_rag_index.py"),
        "--out-dir",
        str(data_dir),
        "--workspace-root",
        str(workspace),
        "--input",
        *[str(path) for path in inputs],
    ]
    print(f"rebuild: {reason}")
    return subprocess.call(cmd, cwd=workspace)


if __name__ == "__main__":
    raise SystemExit(main())
