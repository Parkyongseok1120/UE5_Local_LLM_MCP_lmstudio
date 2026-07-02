#!/usr/bin/env python
"""Look up exported Material/Blueprint graph metadata by asset path or name."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Literal

from workspace_paths import load_shared_config, resolve_index_dir
from project_context import active_project_name as context_active_project_name
from project_row_filter import filter_rows_by_project
from asset_taxonomy import classify_ue_asset_class, graph_lookup_guidance

AssetKind = Literal["auto", "material", "blueprint", "animation"]
GraphDetail = Literal["compact", "medium", "large", "full"]

GRAPH_DETAIL_ORDER: tuple[GraphDetail, ...] = ("compact", "medium", "large", "full")

GRAPH_DETAIL_LIMITS: dict[str, dict[str, int]] = {
    "compact": {"expressions": 12, "edges": 20, "max_tool_chars": 10_000},
    "medium": {"expressions": 36, "edges": 64, "max_tool_chars": 18_000},
    "large": {"expressions": 96, "edges": 160, "max_tool_chars": 40_000},
    "full": {"expressions": 320, "edges": 800, "max_tool_chars": 80_000},
}

KIND_FILES: dict[str, str] = {
    "material": "raw_material_metadata.jsonl",
    "blueprint": "raw_blueprint_metadata.jsonl",
    "structured": "raw_structured_metadata.jsonl",
    "texture": "raw_texture_metadata.jsonl",
    "mesh": "raw_mesh_metadata.jsonl",
    "world_look": "raw_world_look_metadata.jsonl",
    "fmod": "raw_fmod_metadata.jsonl",
    "animation": "raw_animation_metadata.jsonl",
}

AUTO_SEARCH_KINDS = (
    "material",
    "blueprint",
    "structured",
    "texture",
    "mesh",
    "world_look",
    "fmod",
    "animation",
)

ASSET_PATH_RE = re.compile(r"/Game/[A-Za-z0-9_/]+", re.IGNORECASE)


def _resolve_index_dir(index_dir: str | Path | None) -> Path:
    if index_dir:
        workspace = Path(__file__).resolve().parent.parent
        idx = Path(index_dir)
        return idx if idx.is_absolute() else workspace / idx
    return resolve_index_dir()


def _active_project_name() -> str:
    name = context_active_project_name()
    if name:
        return name
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
    if q == name:
        return True
    if path.endswith("/" + q):
        return True
    if q in {seg for seg in path.split("/") if seg}:
        return True
    title_name = title.rsplit("/", 1)[-1]
    return q == title_name


def _filter_project(rows: list[dict[str, Any]], project_name: str | None) -> list[dict[str, Any]]:
    return filter_rows_by_project(rows, project_name)


def _prioritize_material_expressions(expressions: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    if len(expressions) <= limit:
        return expressions

    priority_markers = (
        "Parameter",
        "FunctionOutput",
        "MaterialLayerOutput",
        "FunctionCall",
        "TextureSample",
        "SetMaterialAttributes",
    )

    def rank(expression: dict[str, Any]) -> tuple[int, str]:
        cls = str(expression.get("class") or "")
        for index, marker in enumerate(priority_markers):
            if marker in cls:
                return index, str(expression.get("name") or "")
        return len(priority_markers), str(expression.get("name") or "")

    return sorted(expressions, key=rank)[:limit]


def resolve_graph_detail(
    *,
    detail: str | None = None,
    compact: bool | None = None,
    include_full_graph: bool = False,
) -> GraphDetail:
    if include_full_graph:
        return "full"
    if detail:
        normalized = str(detail).strip().lower()
        if normalized in GRAPH_DETAIL_LIMITS:
            return normalized  # type: ignore[return-value]
    if compact is False:
        return "medium"
    return "compact"


def graph_detail_limits(detail: str) -> dict[str, int]:
    return dict(GRAPH_DETAIL_LIMITS.get(detail) or GRAPH_DETAIL_LIMITS["compact"])


def _next_graph_detail(detail: str) -> GraphDetail | None:
    try:
        index = GRAPH_DETAIL_ORDER.index(detail)  # type: ignore[arg-type]
    except ValueError:
        return None
    if index + 1 >= len(GRAPH_DETAIL_ORDER):
        return None
    return GRAPH_DETAIL_ORDER[index + 1]


def _summarize_material(row: dict[str, Any], *, detail: GraphDetail = "compact") -> dict[str, Any]:
    meta = _row_metadata(row)
    limits = graph_detail_limits(detail)
    expr_limit = int(limits["expressions"])
    edge_limit = int(limits["edges"])
    all_expressions = list(meta.get("expressions") or [])
    all_edges = list(meta.get("graph_edges") or [])
    if detail in {"compact", "medium"}:
        expression_rows = _prioritize_material_expressions(all_expressions, expr_limit)
    else:
        expression_rows = all_expressions[:expr_limit]
    param_limit = 12 if detail == "compact" else (24 if detail == "medium" else None)
    root_limit = 6 if detail == "compact" else (12 if detail == "medium" else None)
    return {
        "assetPath": _asset_path(row) or None,
        "assetType": meta.get("asset_type"),
        "name": _asset_name(row) or None,
        "parentMaterial": meta.get("parent_material"),
        "graphSource": meta.get("graph_source"),
        "detailLevel": detail,
        "expressionCount": len(all_expressions),
        "graphEdgeCount": len(all_edges),
        "expressionsReturned": len(expression_rows),
        "graphEdgesReturned": min(edge_limit, len(all_edges)),
        "rootOutputs": (meta.get("root_outputs") or [])[:root_limit],
        "expressions": expression_rows,
        "graphEdges": all_edges[:edge_limit],
        "scalarParameters": (meta.get("scalar_parameters") or [])[:param_limit],
        "vectorParameters": (meta.get("vector_parameters") or [])[:param_limit],
        "textureParameters": (meta.get("texture_parameters") or [])[:param_limit],
        "staticSwitchParameters": (meta.get("static_switch_parameters") or [])[:param_limit],
        "description": meta.get("description"),
        "user_exposed_caption": meta.get("user_exposed_caption"),
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


def _summarize_structured(row: dict[str, Any]) -> dict[str, Any]:
    meta = _row_metadata(row)
    return {
        "assetPath": _asset_path(row) or None,
        "assetType": meta.get("asset_type"),
        "name": _asset_name(row) or None,
        "rowStruct": meta.get("row_struct"),
        "columns": meta.get("columns") or [],
        "rowNames": (meta.get("row_names") or [])[:40],
        "blackboardKeys": meta.get("blackboard_keys") or [],
        "emitters": meta.get("emitters") or [],
        "userParameters": meta.get("user_parameters") or [],
        "inputMappings": meta.get("input_mappings") or [],
        "dependencies": meta.get("dependencies") or [],
    }


def _summarize_texture(row: dict[str, Any]) -> dict[str, Any]:
    meta = _row_metadata(row)
    return {
        "assetPath": _asset_path(row) or None,
        "assetType": meta.get("asset_type"),
        "name": _asset_name(row) or None,
        "width": meta.get("width"),
        "height": meta.get("height"),
        "srgb": meta.get("srgb"),
        "compression": meta.get("compression"),
    }


def _summarize_mesh(row: dict[str, Any]) -> dict[str, Any]:
    meta = _row_metadata(row)
    return {
        "assetPath": _asset_path(row) or None,
        "assetType": meta.get("asset_type"),
        "name": _asset_name(row) or None,
        "materialSlots": meta.get("material_slots") or [],
        "lodCount": meta.get("lod_count"),
        "naniteEnabled": meta.get("nanite_enabled"),
        "collisionProfile": meta.get("collision_profile"),
    }


def _summarize_animation(row: dict[str, Any]) -> dict[str, Any]:
    meta = _row_metadata(row)
    return {
        "assetPath": _asset_path(row) or None,
        "assetType": meta.get("asset_type"),
        "name": _asset_name(row) or None,
        "skeleton": meta.get("skeleton"),
        "poses": (meta.get("poses") or [])[:40],
        "blendSamples": (meta.get("blend_samples") or [])[:40],
        "bones": (meta.get("bones") or [])[:40],
        "sockets": (meta.get("sockets") or [])[:40],
    }


def _summarize_generic(row: dict[str, Any]) -> dict[str, Any]:
    meta = _row_metadata(row)
    return {
        "assetPath": _asset_path(row) or None,
        "assetType": meta.get("asset_type"),
        "name": _asset_name(row) or None,
        "properties": meta.get("properties") or meta.get("post_process_settings"),
        "dependencies": meta.get("dependencies") or [],
    }


def _annotate_graph_coverage(summary: dict[str, Any], *, detail: GraphDetail = "compact") -> dict[str, Any]:
    asset_type = str(summary.get("assetType") or "")
    expression_count = int(summary.get("expressionCount") or 0)
    edge_count = int(summary.get("graphEdgeCount") or 0)
    expressions_returned = int(summary.get("expressionsReturned") or len(summary.get("expressions") or []))
    edges_returned = int(summary.get("graphEdgesReturned") or len(summary.get("graphEdges") or []))
    has_graph = expression_count > 0 or edge_count > 0
    summary["graphExported"] = has_graph
    summary["detailLevel"] = detail
    if not has_graph:
        summary["stopRetryingLookup"] = True
        summary["coverageNote"] = (
            "Indexed asset exists but graph/parameters were not exported. "
            "Do not call unreal_asset_graph_lookup again for the same path in this session."
        )
        actions = graph_lookup_guidance(asset_class=asset_type, asset_path=str(summary.get("assetPath") or ""))
        if asset_type in {"MaterialFunctionMaterialLayer", "MaterialFunctionMaterialLayerBlend"}:
            actions.insert(
                0,
                "Run export-editor-metadata if this layer should have a graph, then retry lookup once.",
            )
        summary["nextActions"] = actions[:6]
        taxonomy = classify_ue_asset_class(asset_type)
        if taxonomy:
            summary["taxonomy"] = {
                "item": taxonomy.get("item_name"),
                "ragCoverage": taxonomy.get("rag_coverage"),
                "workDomain": taxonomy.get("work_domain"),
            }
        return summary

    graph_sampled = expressions_returned < expression_count or edges_returned < edge_count
    if graph_sampled:
        summary["graphSampled"] = True
        next_detail = _next_graph_detail(detail)
        if next_detail:
            summary["nextDetailLevel"] = next_detail
            summary["coverageNote"] = (
                f"Graph exported ({expression_count} expressions, {edge_count} edges). "
                f"Current detailLevel={detail} returned {expressions_returned} expressions and "
                f"{edges_returned} edges. Do not repeat the same detailLevel. "
                f"If more node detail is required, call unreal_asset_graph_lookup once with "
                f"graphDetail={next_detail} for the same asset."
            )
            summary["nextActions"] = [
                f"Prefer answering from this {detail} sample plus parameters unless a specific node is missing.",
                f"Escalate at most one level: graphDetail={next_detail}.",
                "For one wire claim, use unreal_material_claim_validate instead of another lookup.",
            ]
        else:
            summary["stopRetryingLookup"] = True
            summary["coverageNote"] = (
                f"Graph exported at maximum detailLevel={detail} "
                f"({expressions_returned}/{expression_count} expressions returned)."
            )
    else:
        summary["stopRetryingLookup"] = True
        summary["coverageNote"] = (
            f"Full graph returned at detailLevel={detail} ({expression_count} expressions)."
        )
    return summary


def _summarize_row(kind: str, row: dict[str, Any], *, detail: GraphDetail = "compact") -> dict[str, Any]:
    if kind == "material":
        summary = _summarize_material(row, detail=detail)
        return _annotate_graph_coverage(summary, detail=detail)
    if kind == "blueprint":
        return _summarize_blueprint(row)
    if kind == "structured":
        return _summarize_structured(row)
    if kind == "texture":
        return _summarize_texture(row)
    if kind == "mesh":
        return _summarize_mesh(row)
    if kind == "animation":
        return _summarize_animation(row)
    return _summarize_generic(row)


def _detect_kind(query: str, explicit: AssetKind) -> AssetKind:
    if explicit != "auto":
        return explicit
    if query.startswith("/Game/"):
        return "auto"
    q = query.lower()
    if q.startswith(("t_", "tex_")):
        return "texture"  # type: ignore[return-value]
    if q.startswith(("sm_", "sk_", "gc_")):
        return "mesh"  # type: ignore[return-value]
    if q.startswith(("pp_", "sky_", "fog_")):
        return "world_look"  # type: ignore[return-value]
    if q.startswith(("pose_", "bs_", "cr_", "ikr_", "ikrt_")):
        return "animation"  # type: ignore[return-value]
    if q.startswith(("dt_", "da_", "pda_", "npc_", "ns_", "bt_", "bb_", "eqs_", "sc_", "ia_", "imc_")):
        return "structured"  # type: ignore[return-value]
    if q.startswith(("m_", "mi_", "mf_", "ml_", "mlb_", "mpc_", "material")):
        return "material"
    if q.startswith(("bp_", "wbp_", "abp_")) or "blueprint" in q:
        return "blueprint"
    return "auto"


def lookup_asset_graph(
    asset_path: str,
    *,
    asset_kind: AssetKind = "auto",
    index_dir: str | Path | None = None,
    project_name: str | None = None,
    include_full_graph: bool = False,
    compact: bool | None = None,
    detail: str | None = None,
) -> dict[str, Any]:
    resolved_detail = resolve_graph_detail(
        detail=detail,
        compact=compact,
        include_full_graph=include_full_graph,
    )
    idx = _resolve_index_dir(index_dir)
    query = str(asset_path or "").strip()
    if not query:
        return {"ok": False, "error": "Missing assetPath query."}

    kind = _detect_kind(query, asset_kind)
    kinds_to_search: list[str]
    if kind == "auto":
        kinds_to_search = list(AUTO_SEARCH_KINDS)
    else:
        kinds_to_search = [kind]

    matches: list[dict[str, Any]] = []
    searched: list[str] = []
    for search_kind in kinds_to_search:
        filename = KIND_FILES.get(search_kind)
        if not filename:
            continue
        raw_path = idx / filename
        searched.append(str(raw_path))
        if not raw_path.is_file():
            continue
        rows = _filter_project(_load_jsonl(raw_path), project_name)
        for row in rows:
            if _matches_query(row, query):
                matches.append({"kind": search_kind, "row": row})

    if not matches:
        asset_class = ""
        registry_path = idx / "raw_asset_registry.jsonl"
        if registry_path.is_file():
            for row in _load_jsonl(registry_path):
                if _matches_query(row, query):
                    meta = _row_metadata(row)
                    asset_class = str(meta.get("asset_type") or row.get("asset_type") or "")
                    break
        next_actions = [
            "Call unreal_editor_metadata_status to check export freshness.",
            "If stale or missing, run Editor export then unreal_sync_editor_metadata.",
            "Retry unreal_asset_graph_lookup with full /Game/... path.",
        ]
        next_actions.extend(graph_lookup_guidance(asset_class=asset_class, asset_path=query))
        return {
            "ok": False,
            "query": query,
            "assetKind": kind,
            "indexDir": str(idx),
            "projectName": project_name or _active_project_name(),
            "searchedFiles": searched,
            "matches": [],
            "assetClass": asset_class or None,
            "taxonomy": classify_ue_asset_class(asset_class) if asset_class else None,
            "nextActions": next_actions,
        }

    summaries = []
    for item in matches[:8]:
        row = item["row"]
        summary = _summarize_row(item["kind"], row, detail=resolved_detail)
        summary["kind"] = item["kind"]
        if include_full_graph or resolved_detail == "full":
            summary["rawMetadata"] = _row_metadata(row)
        summaries.append(summary)

    primary = summaries[0]
    return {
        "ok": True,
        "query": query,
        "assetKind": primary["kind"],
        "matchCount": len(summaries),
        "detailLevel": resolved_detail,
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
    kinds = list(AUTO_SEARCH_KINDS) if kind == "auto" else [kind]

    hits: list[dict[str, Any]] = []
    for search_kind in kinds:
        filename = KIND_FILES.get(search_kind)
        if not filename:
            continue
        raw_path = idx / filename
        if not raw_path.is_file():
            continue
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
        "projectName": project_name or _active_project_name(),
        "count": len(hits),
        "results": hits,
    }


def analyze_asset_folder(
    folder_hint: str,
    *,
    asset_kind: AssetKind = "auto",
    index_dir: str | Path | None = None,
    project_name: str | None = None,
    limit: int = 24,
    graph_detail: GraphDetail | str = "compact",
) -> dict[str, Any]:
    from asset_hint_resolver import resolve_asset_folder_hint
    from project_context import resolve_active_project_context

    ctx = resolve_active_project_context()
    if not ctx.get("ok"):
        return {
            "ok": False,
            "error": ctx.get("error"),
            "suggestedToolCalls": ctx.get("suggestedToolCalls") or [],
        }

    hint_payload = resolve_asset_folder_hint(folder_hint, ctx)
    active_name = project_name or str(ctx["projectName"])
    token = str(hint_payload.get("searchToken") or folder_hint).strip()
    segment = str(hint_payload.get("folderSegment") or token).strip()

    search_result = search_asset_graphs(
        segment,
        asset_kind=asset_kind,
        index_dir=index_dir,
        project_name=active_name,
        limit=max(limit, 12),
    )

    matches: list[dict[str, Any]] = []
    resolved_detail = resolve_graph_detail(detail=str(graph_detail))
    for hit in search_result.get("results") or []:
        asset_path = str(hit.get("assetPath") or "")
        if not asset_path:
            continue
        path_lower = asset_path.lower()
        if segment.lower() not in path_lower and segment.lower() not in str(hit.get("name") or "").lower():
            continue
        lookup = lookup_asset_graph(
            asset_path,
            asset_kind=str(hit.get("kind") or asset_kind),  # type: ignore[arg-type]
            index_dir=index_dir,
            project_name=active_name,
            detail=resolved_detail,
        )
        if lookup.get("ok"):
            matches.append(lookup.get("primary") or lookup)

    suggested = [
        {"tool": "unreal_get_active_project", "args": {}},
        {
            "tool": "unreal_asset_graph_lookup",
            "args": {
                "search": segment,
                "assetKind": asset_kind,
                "projectName": active_name,
                "graphDetail": resolved_detail,
            },
        },
    ]

    return {
        "ok": bool(matches),
        "folderHint": folder_hint,
        "projectName": active_name,
        "searchToken": token,
        "folderSegment": segment,
        "hint": hint_payload,
        "matchCount": len(matches),
        "matches": matches[:limit],
        "indexDir": search_result.get("indexDir"),
        "suggestedToolCalls": suggested,
        "nextActions": [] if matches else [
            "Call unreal_editor_metadata_status and unreal_sync_editor_metadata if exports are stale.",
            f"Retry unreal_asset_graph_lookup with search={segment!r} and projectName={active_name!r}.",
        ],
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
