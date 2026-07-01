#!/usr/bin/env python
"""Report freshness and availability of Editor-exported metadata files."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from workspace_paths import load_shared_config

METADATA_FILES = {
    "blueprint": "raw_blueprint_metadata.jsonl",
    "material": "raw_material_metadata.jsonl",
    "animation": "raw_animation_metadata.jsonl",
    "skeletal_mesh": "raw_skeletal_mesh_metadata.jsonl",
    "anim_blueprint": "raw_anim_blueprint_metadata.jsonl",
    "anim_montage": "raw_anim_montage_metadata.jsonl",
    "sequencer": "raw_sequencer_metadata.jsonl",
    "asset_registry": "raw_asset_registry.jsonl",
    "project_settings": "raw_project_settings.jsonl",
    "level": "raw_level_metadata.jsonl",
}


def _line_count(path: Path) -> int:
    try:
        return sum(1 for line in path.read_text(encoding="utf-8-sig", errors="replace").splitlines() if line.strip())
    except OSError:
        return 0


def _latest_project_asset_mtime(project_root: Path) -> float | None:
    content = project_root / "Content"
    if not content.is_dir():
        return None
    latest: float | None = None
    for path in content.rglob("*.uasset"):
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        latest = mtime if latest is None else max(latest, mtime)
    return latest


def editor_metadata_status(
    index_dir: str | Path | None = None,
    project_root: str | Path | None = None,
    stale_after_hours: float = 24.0,
) -> dict[str, Any]:
    workspace = Path(__file__).resolve().parent.parent
    idx = Path(index_dir) if index_dir else workspace / "data" / "unreal58"
    if not idx.is_absolute():
        idx = workspace / idx

    active_project = ""
    if project_root:
        root = Path(project_root).resolve()
    else:
        config = load_shared_config()
        active_project = str(config.get("activeProject") or "")
        root = Path(active_project).resolve() if active_project else Path()
    if root and root.suffix.lower() == ".uproject":
        root = root.parent

    latest_asset_mtime = _latest_project_asset_mtime(root) if root and root.exists() else None
    now = __import__("time").time()
    files: dict[str, Any] = {}
    missing: list[str] = []
    stale: list[str] = []
    for kind, filename in METADATA_FILES.items():
        path = idx / filename
        exists = path.is_file()
        row: dict[str, Any] = {"path": str(path), "exists": exists, "rowCount": 0}
        if exists:
            stat = path.stat()
            age_hours = (now - stat.st_mtime) / 3600.0
            row.update(
                {
                    "sizeBytes": stat.st_size,
                    "mtime": stat.st_mtime,
                    "ageHours": round(age_hours, 2),
                    "rowCount": _line_count(path),
                    "olderThanLatestUAsset": bool(latest_asset_mtime and stat.st_mtime < latest_asset_mtime),
                }
            )
            if age_hours > stale_after_hours or row.get("olderThanLatestUAsset"):
                stale.append(kind)
        else:
            missing.append(kind)
        files[kind] = row

    needs_export = bool(missing or stale)
    return {
        "ok": not needs_export,
        "activeProject": active_project,
        "projectRoot": str(root) if root else "",
        "indexDir": str(idx),
        "latestProjectUAssetMtime": latest_asset_mtime,
        "missingKinds": missing,
        "staleKinds": stale,
        "needsEditorExport": needs_export,
        "recommendedCommands": [
            ".\\rag.ps1 collect-blueprint-metadata -Question <export.jsonl>",
            ".\\rag.ps1 collect-material-metadata -Question <export.jsonl>",
            ".\\rag.ps1 build",
        ] if needs_export else [],
        "files": files,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Report Editor metadata freshness.")
    parser.add_argument("--index-dir", default="data/unreal58")
    parser.add_argument("--project-root", default="")
    parser.add_argument("--stale-after-hours", type=float, default=24.0)
    args = parser.parse_args()
    payload = editor_metadata_status(args.index_dir, args.project_root or None, args.stale_after_hours)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())