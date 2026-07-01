# Shared helpers for Unreal Editor metadata exporters.

from __future__ import annotations

import json
from typing import Any, Callable

MAX_ITEMS = 120
MAX_DEPS = 40


def asset_class_name(asset) -> str:
    if hasattr(asset, "asset_class_path"):
        return str(asset.asset_class_path.asset_name)
    return str(getattr(asset, "asset_class", "") or "")


def safe_name(value) -> str:
    try:
        return value.get_name()
    except Exception:
        return str(value or "")


def safe_prop(obj, prop: str, default=None):
    try:
        if hasattr(obj, "get_editor_property"):
            return obj.get_editor_property(prop)
        return getattr(obj, prop, default)
    except Exception:
        return default


def value_to_text(value) -> str:
    if value is None:
        return ""
    for attr in ("get_path_name", "get_name"):
        try:
            if hasattr(value, attr):
                return str(getattr(value, attr)())
        except Exception:
            pass
    return str(value)


def coerce_list(value) -> list:
    if value is None:
        return []
    if isinstance(value, (str, bytes)):
        return []
    try:
        return list(value)
    except TypeError:
        return []


def collect_dependencies(registry, package_name, limit: int = MAX_DEPS) -> list[str]:
    try:
        return [str(dep) for dep in registry.get_dependencies(package_name)[:limit]]
    except Exception:
        return []


def write_jsonl_rows(out_path: str, rows: list[dict[str, Any]]) -> None:
    with open(out_path, "w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def export_by_class_map(
    content_path: str,
    out_path: str,
    *,
    export_classes: set[str] | frozenset[str],
    collectors: dict[str, Callable[[Any, Any, str], dict[str, Any]]],
    log_label: str,
) -> int:
    """Scan content_path once; load only assets whose class is in export_classes."""
    import unreal

    registry = unreal.AssetRegistryHelpers.get_asset_registry()
    assets = registry.get_assets_by_path(content_path, recursive=True)
    class_set = set(export_classes)
    rows: list[dict[str, Any]] = []
    for asset in assets:
        cls = asset_class_name(asset)
        if cls not in class_set:
            continue
        path = str(asset.package_name)
        row: dict[str, Any] = {
            "asset_path": path,
            "asset_type": cls,
            "name": path.rsplit("/", 1)[-1],
        }
        collector = collectors.get(cls)
        if not collector:
            rows.append(row)
            continue
        try:
            asset_obj = unreal.load_asset(path)
            if asset_obj:
                row.update(collector(unreal, asset_obj, cls))
            deps = collect_dependencies(registry, asset.package_name)
            if deps:
                row["dependencies"] = deps
        except Exception:
            pass
        rows.append(row)

    write_jsonl_rows(out_path, rows)
    unreal.log(f"Exported {len(rows)} {log_label} metadata rows to {out_path}")
    return len(rows)
