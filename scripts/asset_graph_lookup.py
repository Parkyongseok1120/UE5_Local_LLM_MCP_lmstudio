#!/usr/bin/env python
"""Look up exported Material/Blueprint graph metadata by asset path or name."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Literal

from workspace_paths import load_shared_config, resolve_index_dir

AssetKind = Literal["auto", "material", "blueprint", "animation"]

KIND_FILES: dict[str, str] = {
    "material": "raw_material_metadata.jsonl",
    "blueprint": "raw_blueprint_metadata.jsonl",
}

ASSET_PATH_RE = re.compile(r"/Game/[A-Za-z0-9_/]+", re.IGNORECASE)


def _resolve_index_dir(index_dir: str | Path | None) -> Path:
    if index_dir:
        workspace = Path(__file__).resolve().parent.parent
        idx = Path(index_dir)
        return idx if idx.is_absolute() else workspace / idx
    return resolve_index_dir()


def _active_project_name() -> str:
    active = str(load_shared_config().get("activeProject") or "")
    if not active:
        return ""
    path = Path(active)
    return path.stem if path.suffix.lower() == ".uproject" else path.name


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.is_file():
        return rows
    for line in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def _row_metadata(row: dict[str, Any]) -> dict[str, Any]:
    meta = row.get("metadata")
    return meta if isinstance(meta, dict) else row


def _asset_path(row: dict[str, Any]) -> str:
    meta = _row_metadata(row)
    return str(meta.get("asset_path") or row.get("path") or "")


def _asset_name(row: dict[str, Any]) -> str:
    path = _asset_path(row)
    return path.rsplit("/", 1)[-1] if path else str(_row_metadata(row).get("name") or row.get("title") or "")


def _normalize_query(text: str) -> str:
    value = str(text or "").strip().replace("\\", "/")
    if not value:
        return value
    if value.startswith("/Game"):
        return value
    if value.startswith("Game/"):
        return "/" + value
    return value


def _matches_query(row: dict[str, Any], query: str) -> bool:
    q = _normalize_query(query).lower()
    if not q:
        return False
    meta = _row_metadata(row)
    path = _asset_path(row).lower()
    name = _asset_name(row).lower()
    title = str(row.get("title") or meta.get("generated_class") or "").lower()
    if q.startswith("/game/"):
        return path == q or path.endswith("/" + q.rsplit("/", 1)[-1])
    return q in path or q == name or q in title


def _filter_project(rows: list[dict[str, Any]], project_name: str | None) -> list[dict[str, Any]]:
    active = project_name or _active_project_name()
    if not active:
        return rows
    filtered = []
    for row in rows:
        meta = _row_metadata(row)
        project = str(meta.get("project") or row.get("project") or "")
        if project in {"", active}:
            filtered.append(row)
    return filtered


def _summarize_material(row: dict[str, Any]) -> dict[str, Any]:
    meta = _row_metadata(row)
    return {
        "assetPath": _asset_path(row) or None,
        "assetType": meta.get("asset_type"),
        "name": _asset_name(row) or None,
        "parentMaterial": meta.get("parent_material"),
        "graphSource": meta.get("graph_source"),
        "expressionCount": len(meta.get("expressions") or []),
        "graphEdgeCount": len(meta.get("graph_edges") or []),
        "rootOutputs": meta.get("root_outputs") or [],
        "expressions": (meta.get("expressions") or [])[:40],
        "graphEdges": (meta.get("graph_edges") or [])[:80],
        "scalarParameters": meta.get("scalar_parameters") or [],
        "vectorParameters": meta.get("vector_parameters") or [],
        "textureParameters": meta.get("texture_parameters") or [],
        "staticSwitchParameters": meta.get("static_switch_parameters") or [],
    }


def _summarize_blueprint(row: dict[str, Any]) -> dict[str, Any]:
    meta = _row_metadata(row)
    graphs = meta.get("graphs") or []
    return {
        "assetPath": _asset_path(row) or None,
        "assetType": meta.get("asset_type"),
        "name": _asset_name(row) or None,
        "generatedClass": meta.get("generated_class"),
        "parentClass": meta.get("parent_class"),
        "graphCount": len(graphs),
        "graphs": [
            {
                "name": graph.get("name"),
                "nodeCount": graph.get("node_count"),
                "nodes": (graph.get("nodes") or [])[:24],
            }
            for graph in graphs[:8]
            if isinstance(graph, dict)
        ],
        "graphLinks": (meta.get("graph_links") or [])[:80],
        "variables": meta.get("variables") or [],
        "functions": meta.get("functions") or [],
    }


def _detect_kind(query: str, explicit: AssetKind) -> AssetKind:
    if explicit != "auto":
        return explicit
    if query.startswith("/Game/"):
        return "auto"
    if query.lower().startswith(("m_", "mi_", "material")):
        return "material"
    if query.lower().startswith(("bp_", "wbp_", "abp_")) or "blueprint" in query.lower():
        return "blueprint"
    return "auto"


def lookup_asset_graph(
    asset_path: str,
    *,
    asset_kind: AssetKind = "auto",
    index_dir: str | Path | None = None,
    project_name: str | None = None,
    include_full_graph: bool = False,
) -> dict[str, Any]:
    idx = _resolve_index_dir(index_dir)
    query = str(asset_path or "").strip()
    if not query:
        return {"ok": False, "error": "Missing assetPath query."}

    kind = _detect_kind(query, asset_kind)
    kinds_to_search: list[str]
    if kind == "auto":
        kinds_to_search = ["material", "blueprint"]
    else:
        kinds_to_search = [kind]

    matches: list[dict[str, Any]] = []
    searched: list[str] = []
    for search_kind in kinds_to_search:
        raw_path = idx / KIND_FILES[search_kind]
        searched.append(str(raw_path))
        rows = _filter_project(_load_jsonl(raw_path), project_name)
        for row in rows:
            if _matches_query(row, query):
                matches.append({"kind": search_kind, "row": row})

    if not matches:
        return {
            "ok": False,
            "query": query,
            "assetKind": kind,
            "indexDir": str(idx),
            "projectName": project_name or _active_project_name(),
            "searchedFiles": searched,
            "matches": [],
            "nextActions": [
                "Call unreal_editor_metadata_status to check export freshness.",
                "If stale or missing, run Editor export then unreal_sync_editor_metadata.",
                "Retry unreal_asset_graph_lookup with full /Game/... path.",
            ],
        }

    summaries = []
    for item in matches[:8]:
        row = item["row"]
        if item["kind"] == "material":
            summary = _summarize_material(row)
        else:
            summary = _summarize_blueprint(row)
        summary["kind"] = item["kind"]
        if include_full_graph:
            summary["rawMetadata"] = _row_metadata(row)
        summaries.append(summary)

    primary = summaries[0]
    return {
        "ok": True,
        "query": query,
        "assetKind": primary["kind"],
        "matchCount": len(summaries),
        "indexDir": str(idx),
        "projectName": project_name or _active_project_name(),
        "primary": primary,
        "matches": summaries,
    }


def search_asset_graphs(
    query: str,
    *,
    asset_kind: AssetKind = "auto",
    index_dir: str | Path | None = None,
    project_name: str | None = None,
    limit: int = 12,
) -> dict[str, Any]:
    idx = _resolve_index_dir(index_dir)
    q = str(query or "").strip().lower()
    kind = _detect_kind(query, asset_kind)
    kinds = ["material", "blueprint"] if kind == "auto" else [kind]

    hits: list[dict[str, Any]] = []
    for search_kind in kinds:
        raw_path = idx / KIND_FILES[search_kind]
        rows = _filter_project(_load_jsonl(raw_path), project_name)
        for row in rows:
            path = _asset_path(row).lower()
            name = _asset_name(row).lower()
            haystack = f"{path} {name}"
            if q and q not in haystack:
                continue
            hits.append(
                {
                    "kind": search_kind,
                    "assetPath": _asset_path(row),
                    "name": _asset_name(row),
                    "graphEdgeCount": len(_row_metadata(row).get("graph_edges") or []),
                    "graphLinkCount": len(_row_metadata(row).get("graph_links") or []),
                }
            )
            if len(hits) >= limit:
                break
        if len(hits) >= limit:
            break

    return {
        "ok": bool(hits),
        "query": query,
        "assetKind": kind,
        "indexDir": str(idx),
        "count": len(hits),
        "results": hits,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Look up exported asset graph metadata.")
    parser.add_argument("--asset-path", default="", help="Asset path or short name, e.g. /Game/Materials/M_Core or M_Core")
    parser.add_argument("--search", default="", help="Substring search across indexed material/blueprint assets")
    parser.add_argument("--asset-kind", default="auto", choices=["auto", "material", "blueprint"])
    parser.add_argument("--index-dir", default="")
    parser.add_argument("--project-name", default="")
    parser.add_argument("--full", action="store_true")
    parser.add_argument("--limit", type=int, default=12)
    args = parser.parse_args()

    index_dir = args.index_dir or None
    project_name = args.project_name or None
    if args.search:
        payload = search_asset_graphs(
            args.search,
            asset_kind=args.asset_kind,  # type: ignore[arg-type]
            index_dir=index_dir,
            project_name=project_name,
            limit=args.limit,
        )
    else:
        payload = lookup_asset_graph(
            args.asset_path,
            asset_kind=args.asset_kind,  # type: ignore[arg-type]
            index_dir=index_dir,
            project_name=project_name,
            include_full_graph=args.full,
        )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
