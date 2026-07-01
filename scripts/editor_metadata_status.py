#!/usr/bin/env python
"""Report freshness and availability of Editor-exported metadata files."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from workspace_paths import editor_export_dir, load_shared_config

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

KIND_ASSET_TYPES = {
    "blueprint": {"Blueprint", "WidgetBlueprint", "AnimBlueprint"},
    "material": {"Material", "MaterialInstance", "MaterialInstanceConstant"},
    "animation": {"SkeletalMesh", "AnimBlueprint", "AnimSequence", "AnimMontage", "AnimNotify", "AnimNotifyState", "LevelSequence"},
    "skeletal_mesh": {"SkeletalMesh"},
    "anim_blueprint": {"AnimBlueprint"},
    "anim_montage": {"AnimMontage"},
    "sequencer": {"LevelSequence"},
    "level": {"World", "Level"},
}

AGGREGATE_ANIMATION_KINDS = {"skeletal_mesh", "anim_blueprint", "anim_montage", "sequencer"}


def _line_count(path: Path) -> int:
    try:
        return sum(1 for line in path.read_text(encoding="utf-8-sig", errors="replace").splitlines() if line.strip())
    except OSError:
        return 0


def _load_jsonl_metadata(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.is_file():
        return rows
    for line in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        meta = row.get("metadata") if isinstance(row, dict) else None
        rows.append(meta if isinstance(meta, dict) else row)
    return rows


def _latest_project_asset_mtime(project_root: Path, asset_paths: set[str] | None = None) -> float | None:
    content = project_root / "Content"
    if not content.is_dir():
        return None
    latest: float | None = None
    for path in content.rglob("*.uasset"):
        if asset_paths is not None:
            try:
                rel = path.relative_to(content).with_suffix("")
            except ValueError:
                continue
            game_path = "/Game/" + str(rel).replace("\\", "/")
            if game_path not in asset_paths:
                continue
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        latest = mtime if latest is None else max(latest, mtime)
    return latest


def _asset_paths_for_kind(index_dir: Path, kind: str) -> set[str] | None:
    wanted = KIND_ASSET_TYPES.get(kind)
    if not wanted:
        return None
    paths: set[str] = set()
    for meta in _load_jsonl_metadata(index_dir / "raw_asset_registry.jsonl"):
        asset_type = str(meta.get("asset_type") or "")
        asset_path = str(meta.get("asset_path") or "")
        if asset_type in wanted and asset_path.startswith("/Game/"):
            paths.add(asset_path)
    return paths


def _aggregate_animation_rows(index_dir: Path, asset_type: str) -> int:
    count = 0
    for meta in _load_jsonl_metadata(index_dir / "raw_animation_metadata.jsonl"):
        if str(meta.get("asset_type") or "") == asset_type:
            count += 1
    return count


def _metadata_file_info(index_dir: Path, kind: str) -> tuple[Path, bool, int]:
    path = index_dir / METADATA_FILES[kind]
    if path.is_file():
        return path, True, _line_count(path)
    aggregate_type = {
        "skeletal_mesh": "SkeletalMesh",
        "anim_blueprint": "AnimBlueprint",
        "anim_montage": "AnimMontage",
        "sequencer": "LevelSequence",
    }.get(kind)
    if aggregate_type:
        aggregate = index_dir / "raw_animation_metadata.jsonl"
        count = _aggregate_animation_rows(index_dir, aggregate_type)
        if count:
            return aggregate, True, count
    return path, False, 0


def _latest_config_mtime(project_root: Path) -> float | None:
    config_dir = project_root / "Config"
    if not config_dir.is_dir():
        return None
    latest: float | None = None
    for path in config_dir.rglob("*.ini"):
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
    latest_config_mtime = _latest_config_mtime(root) if root and root.exists() else None
    now = __import__("time").time()
    files: dict[str, Any] = {}
    missing: list[str] = []
    stale: list[str] = []
    for kind, filename in METADATA_FILES.items():
        path, exists, row_count = _metadata_file_info(idx, kind)
        row: dict[str, Any] = {"path": str(path), "exists": exists, "rowCount": row_count}
        relevant_mtime = latest_asset_mtime
        if kind == "project_settings":
            relevant_mtime = latest_config_mtime
        elif kind not in {"asset_registry", "project_settings"}:
            asset_paths = _asset_paths_for_kind(idx, kind)
            kind_mtime = _latest_project_asset_mtime(root, asset_paths) if root and root.exists() and asset_paths else None
            if kind_mtime is not None:
                relevant_mtime = kind_mtime
        if exists:
            stat = path.stat()
            age_hours = (now - stat.st_mtime) / 3600.0
            row.update(
                {
                    "sizeBytes": stat.st_size,
                    "mtime": stat.st_mtime,
                    "ageHours": round(age_hours, 2),
                    "rowCount": _line_count(path),
                    "olderThanRelevantSource": bool(relevant_mtime and stat.st_mtime < relevant_mtime),
                }
            )
            if kind in AGGREGATE_ANIMATION_KINDS:
                row["rowCount"] = row_count
                row["aggregateSource"] = "raw_animation_metadata.jsonl"
            if age_hours > stale_after_hours or row.get("olderThanRelevantSource"):
                stale.append(kind)
        else:
            missing.append(kind)
        files[kind] = row

    needs_export = bool(missing or stale)
    export_dir = editor_export_dir()
    export_dir_info: dict[str, Any] = {"configured": bool(export_dir), "path": str(export_dir or "")}
    if export_dir:
        try:
            export_files = sorted(export_dir.glob("*.jsonl"))
            export_dir_info["fileCount"] = len(export_files)
            export_dir_info["newestMtime"] = max((p.stat().st_mtime for p in export_files), default=None)
        except OSError:
            export_dir_info["fileCount"] = 0
            export_dir_info["newestMtime"] = None

    recommended = []
    if needs_export:
        recommended = [
            "Call unreal_sync_editor_metadata with autoExport=true (or refresh=true).",
            "Or run .\\rag.ps1 export-editor-metadata locally.",
            "If Editor is already open, register LM Studio menu once so the export watcher is active.",
        ]
        if export_dir:
            recommended.insert(
                1,
                f"Export dir: {export_dir}. Run installer\\Export-EditorMetadata.ps1 -PrintCommandsOnly for copy/paste commands.",
            )

    return {
        "ok": not needs_export,
        "activeProject": active_project,
        "projectRoot": str(root) if root else "",
        "indexDir": str(idx),
        "latestProjectUAssetMtime": latest_asset_mtime,
        "missingKinds": missing,
        "staleKinds": stale,
        "needsEditorExport": needs_export,
        "exportDir": export_dir_info,
        "recommendedActions": recommended,
        "recommendedCommands": [
            "unreal_sync_editor_metadata (autoExport=true)",
            ".\\rag.ps1 export-editor-metadata",
            "unreal_asset_graph_lookup",
        ] if needs_export else ["unreal_asset_graph_lookup"],
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
