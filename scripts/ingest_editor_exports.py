#!/usr/bin/env python
"""Ingest Editor-exported JSONL files from a directory into RAG raw inputs."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from workspace_paths import active_project_names, find_workspace_root, load_shared_config, resolve_index_dir

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


def discover_exports(
    export_dir: Path,
    *,
    project_file: Path | None = None,
    require_manifest: bool = False,
) -> list[tuple[Path, str]]:
    if require_manifest or (export_dir / "export_manifest.json").is_file():
        if project_file is None:
            if require_manifest:
                raise ValueError("Exact project_file is required for a completed export manifest")
        else:
            from editor_export_contract import completed_export_files

            return completed_export_files(export_dir, project_file)[1]
    found: list[tuple[Path, str]] = []
    seen: set[Path] = set()
    if not export_dir.is_dir():
        return found
    for pattern, kind in EXPORT_PATTERNS:
        for path in sorted(export_dir.glob(pattern)):
            if not path.is_file():
                continue
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            found.append((resolved, kind))
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest Editor export JSONL files from a directory.")
    parser.add_argument("--export-dir", required=True, help="Directory containing Editor export JSONL files.")
    parser.add_argument("--out-dir", default="", help="Output directory (default: configured RAG data directory).")
    parser.add_argument("--project-name", default="")
    parser.add_argument("--project-root", default="")
    parser.add_argument("--project-file", default="", help="Exact .uproject bound by export_manifest.json.")
    parser.add_argument("--require-manifest", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    workspace = find_workspace_root()
    export_dir = Path(args.export_dir).expanduser().resolve()
    out_dir = Path(args.out_dir) if args.out_dir else resolve_index_dir(workspace)
    if args.out_dir and not out_dir.is_absolute():
        out_dir = workspace / out_dir
    project_name = resolve_project_name(args.project_name)

    project_file = Path(args.project_file).expanduser().resolve() if args.project_file else None
    manifest = None
    try:
        if args.require_manifest:
            from editor_export_contract import completed_export_files

            if project_file is None:
                raise ValueError("--project-file is required with --require-manifest")
            manifest, exports = completed_export_files(export_dir, project_file)
        else:
            exports = discover_exports(export_dir, project_file=project_file)
    except (RuntimeError, ValueError) as exc:
        print(f"[fail] {exc}")
        return 2
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
            "--project-root",
            str(args.project_root or ""),
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

    if manifest is not None and not args.dry_run:
        from editor_capture_state import record_completed_capture

        record_completed_capture(out_dir, manifest)

    print(f"done: ingested {ingested} export file(s) from {export_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
