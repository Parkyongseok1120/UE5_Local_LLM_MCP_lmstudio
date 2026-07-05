#!/usr/bin/env python
"""Shared search helpers for the local Unreal RAG index."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import hashlib
import json
import re
import sqlite3


TERM_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|[0-9]+|[\uac00-\ud7a3]+")

_conn_cache: dict[str, sqlite3.Connection] = {}

SIDECAR_RAG_MODES = {
    "compile_fix",
    "module_fix",
    "reflection_fix",
    "refactor_r0",
    "refactor_r1",
    "refactor_r2",
    "refactor_r3",
    "refactor_r4",
}
MODULE_QUERY_MARKERS = (
    "c1083",
    "cannot open include",
    "missing include",
    "missing module",
    "module dependency",
    "build.cs",
)


def get_index_connection(index: Path) -> sqlite3.Connection:
    key = str(index.resolve())
    conn = _conn_cache.get(key)
    if conn is None:
        conn = sqlite3.connect(index)
        conn.row_factory = sqlite3.Row
        conn.execute("pragma journal_mode=WAL")
        conn.execute("pragma cache_size=-64000")
        _conn_cache[key] = conn
    return conn

def table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    try:
        rows = conn.execute(f"pragma table_info({table})").fetchall()
        return {str(row[1]) for row in rows}
    except sqlite3.Error:
        return set()


def _header_basenames_from_query(query: str) -> list[str]:
    """Extract Unreal-style header basenames from a query (e.g. GameplayTagContainer.h)."""
    found = re.findall(r"[A-Za-z_][A-Za-z0-9_]*\.h(?:pp)?", query, flags=re.IGNORECASE)
    return list(dict.fromkeys(b.lower() for b in found))


def _sidecar_row(
    *,
    sidecar_type: str,
    title: str,
    items: list[dict[str, Any]],
    mode: str,
    locator: str = "",
) -> dict[str, Any]:
    payload = {"sidecarType": sidecar_type, "items": items}
    digest = hashlib.sha1(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()[:12]
    return {
        "chunk_id": f"sidecar:{sidecar_type}:{digest}",
        "source": "rag_sidecar",
        "title": title,
        "locator": locator or sidecar_type,
        "chunk_index": 0,
        "text": json.dumps(payload, ensure_ascii=False, indent=2),
        "symbol_name": "",
        "symbol_kind": sidecar_type,
        "module_name": "",
        "score": -9000.0,
        "sidecarType": sidecar_type,
        "items": items,
        "resolved_mode": mode,
    }


def _symbol_candidates_from_query(query: str) -> list[str]:
    found: list[str] = []
    patterns = (
        r"\b[AUFSI][A-Z][A-Za-z0-9_]{2,}\b",
        r"\b[A-Z][A-Za-z0-9_]+(?:Component|Subsystem|Character|Actor|GameMode|Widget|Interface)\b",
    )
    for pattern in patterns:
        for match in re.finditer(pattern, query or ""):
            symbol = match.group(0)
            if symbol not in found and not symbol.endswith(".h"):
                found.append(symbol)
    return found[:12]


def fetch_symbol_graph_sidecar(query: str, mode: str, limit: int = 5) -> list[dict[str, Any]]:
    if mode not in SIDECAR_RAG_MODES:
        return []
    try:
        from symbol_graph import load_symbol_graph, lookup_symbol
    except Exception:
        return []
    graph = load_symbol_graph()
    if not graph.get("symbols"):
        return []
    items: list[dict[str, Any]] = []
    seen: set[tuple[str, str, int]] = set()
    for symbol in _symbol_candidates_from_query(query):
        for row in lookup_symbol(symbol, graph, limit=2):
            key = (
                str(row.get("symbol_name") or ""),
                str(row.get("file_path") or ""),
                int(row.get("line_start") or 0),
            )
            if key in seen:
                continue
            seen.add(key)
            items.append(
                {
                    "symbol": row.get("symbol_name", ""),
                    "kind": row.get("symbol_kind", ""),
                    "file": row.get("file_path", ""),
                    "lineStart": row.get("line_start", 0),
                    "lineEnd": row.get("line_end", row.get("line_start", 0)),
                    "module": row.get("module_name", ""),
                    "ownerBuildCs": row.get("owner_build_cs", ""),
                }
            )
            if len(items) >= limit:
                return [_sidecar_row(sidecar_type="symbol_graph", title="Symbol graph sidecar", items=items, mode=mode)]
    return [_sidecar_row(sidecar_type="symbol_graph", title="Symbol graph sidecar", items=items, mode=mode)] if items else []


def query_looks_module_fix(query: str, mode: str) -> bool:
    raw = query.lower()
    return mode == "module_fix" or any(marker in raw for marker in MODULE_QUERY_MARKERS)


def fetch_module_resolver_sidecar(query: str, mode: str, limit: int = 5) -> list[dict[str, Any]]:
    if not query_looks_module_fix(query, mode):
        return []
    try:
        from module_resolver import resolve_modules_from_error, resolve_modules_from_text
    except Exception:
        return []
    modules: list[str] = []
    for module in [*resolve_modules_from_error(query), *resolve_modules_from_text(query)]:
        if module not in modules:
            modules.append(module)
    if not modules:
        return []
    items = [
        {
            "module": module,
            "hint": "Read owner Build.cs and include-owner evidence before deciding whether to patch dependency or source include.",
        }
        for module in modules[:limit]
    ]
    return [
        _sidecar_row(
            sidecar_type="module_resolver",
            title="Module resolver sidecar",
            items=items,
            mode=mode,
            locator="Build.cs/module hint",
        )
    ]


def fetch_error_route_sidecar(query: str, mode: str, limit: int = 3) -> list[dict[str, Any]]:
    if mode not in {"compile_fix", "module_fix", "reflection_fix"}:
        return []
    try:
        from error_taxonomy import route_error_action
    except Exception:
        return []
    route = route_error_action(query)
    if not route:
        return []
    items = [
        {
            "errorSubkind": route.get("errorSubkind", ""),
            "broadMode": route.get("broadMode", ""),
            "preferredRagModes": list(route.get("preferredRagModes") or [])[:limit],
            "requiredReads": list(route.get("requiredReads") or [])[:limit],
            "allowedPatchTargets": list(route.get("allowedPatchTargets") or [])[:limit],
            "forbiddenActions": list(route.get("forbiddenActions") or [])[:limit],
            "notes": list(route.get("notes") or [])[:limit],
            "softSteering": list(route.get("softSteering") or [])[:limit + 2],
            "buildCsFirstWarning": route.get("buildCsFirstWarning", ""),
            "routePriorityApplied": route.get("routePriorityApplied", ""),
        }
    ]
    return [
        _sidecar_row(
            sidecar_type="error_route",
            title="Error route sidecar",
            items=items,
            mode=mode,
            locator=str(route.get("broadMode") or mode),
        )
    ]


ARCHITECTURE_SIDECAR_MODES = {
    "review",
    "planning",
    "design",
    "agent_edit",
    "refactor_r0",
    "refactor_r1",
    "refactor_r2",
    "refactor_r3",
    "refactor_r4",
}
ARCHITECTURE_QUERY_MARKERS = (
    "architecture",
    "refactor",
    "ownership",
    "component",
    "subsystem",
    "blueprint",
    "uproperty",
    "ufunction",
    "responsibility",
    "boundary",
)
ARCHITECTURE_SIDECAR_STOPWORDS = {
    "review",
    "architecture",
    "ownership",
    "refactor",
    "please",
    "about",
    "with",
    "from",
    "this",
    "that",
    "the",
    "and",
}


def default_architecture_map_path(root: Path | None = None) -> Path:
    base = root or Path(__file__).resolve().parent.parent
    return base / "data" / "architecture" / "architecture_map.json"


def architecture_sidecar_locator(path: Path) -> str:
    root = Path(__file__).resolve().parent.parent
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return "architecture_map"


def query_wants_architecture_sidecar(query: str, mode: str) -> bool:
    if mode not in ARCHITECTURE_SIDECAR_MODES:
        return False
    raw = str(query or "").lower()
    return any(marker in raw for marker in ARCHITECTURE_QUERY_MARKERS)


def architecture_sidecar_match_score(row: dict[str, Any], query_lower: str, query_terms: list[str]) -> int:
    name = str(row.get("name") or "").lower()
    stripped_name = name[1:] if name[:1] in {"a", "f", "i", "s", "t", "u"} else name
    category = str(row.get("category") or "").lower()
    risk_flags = " ".join(str(item) for item in row.get("riskFlags") or []).lower()
    hints = " ".join(str(item) for item in row.get("responsibilityHints") or []).lower()
    haystack = " ".join([name, stripped_name, category, risk_flags, hints])
    score = 0
    if name and name in query_lower:
        score += 100
    if stripped_name and stripped_name in query_lower:
        score += 80
    if category and category in query_lower:
        score += 25
    for term in query_terms:
        if term in haystack:
            score += 8
    return score


def architecture_sidecar_item(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": row.get("name", ""),
        "category": row.get("category", ""),
        "module": row.get("module", ""),
        "header": row.get("header", ""),
        "cpp": row.get("cpp", ""),
        "responsibilityHints": list(row.get("responsibilityHints") or [])[:3],
        "riskFlags": list(row.get("riskFlags") or [])[:6],
    }


def fetch_architecture_sidecar(query: str, mode: str, limit: int = 5) -> list[dict[str, Any]]:
    if not query_wants_architecture_sidecar(query, mode):
        return []
    path = default_architecture_map_path()
    if not path.is_file():
        return []
    try:
        arch = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return []

    query_lower = str(query or "").lower()
    query_terms = [
        term.lower()
        for term in tokenize(query_lower)
        if term.lower() not in ARCHITECTURE_SIDECAR_STOPWORDS and len(term) > 2
    ]
    scored: list[tuple[int, dict[str, Any]]] = []
    for row in arch.get("types") or []:
        score = architecture_sidecar_match_score(row, query_lower, query_terms)
        if score > 0:
            scored.append((score, row))
    items = [architecture_sidecar_item(row) for _score, row in sorted(scored, key=lambda item: item[0], reverse=True)[:limit]]

    if not items:
        fallback_limit = min(limit, 3)
        for row in arch.get("types") or []:
            if row.get("riskFlags") or row.get("responsibilityHints"):
                item = architecture_sidecar_item(row)
                item["responsibilityHints"] = item["responsibilityHints"][:2]
                item["riskFlags"] = item["riskFlags"][:4]
                items.append(item)
            if len(items) >= fallback_limit:
                break
    if not items:
        return []
    return [
        _sidecar_row(
            sidecar_type="architecture_map",
            title="Architecture map sidecar",
            items=items,
            mode=mode,
            locator=architecture_sidecar_locator(path),
        )
    ]


def preferred_modes_from_error_route(query: str, mode: str) -> list[str]:
    if mode not in {"compile_fix", "module_fix", "reflection_fix"}:
        return []
    try:
        from error_taxonomy import route_error_action
    except Exception:
        return []
    route = route_error_action(query)
    return [str(item) for item in route.get("preferredRagModes") or [] if str(item) in VALID_MODES]


def fetch_module_graph_sidecar(
    conn: sqlite3.Connection,
    query: str,
    limit: int = 12,
) -> list[dict[str, Any]]:
    """Query include_owners / module_edges side tables (Phase 1 — not in FTS chunks)."""
    tables = {
        str(row[0])
        for row in conn.execute("select name from sqlite_master where type='table'")
    }
    if "include_owners" not in tables:
        return []

    rows: list[sqlite3.Row] = []
    seen: set[str] = set()

    def add_row(row: sqlite3.Row) -> None:
        oid = str(row["owner_id"])
        if oid in seen:
            return
        seen.add(oid)
        rows.append(row)

    for basename in _header_basenames_from_query(query):
        for row in conn.execute(
            """
            select owner_id, include_path, symbol_name, module_name, title, text
            from include_owners
            where lower(symbol_name) = ?
               or lower(include_path) like ?
            limit ?
            """,
            (basename, f"%/{basename}", limit),
        ):
            add_row(row)

    terms = [t for t in tokenize(query) if len(t) > 2]
    if not terms:
        terms = [query.strip()]

    for term in terms[:8]:
        pattern = f"%{term.lower()}%"
        for row in conn.execute(
            """
            select owner_id, include_path, symbol_name, module_name, title, text
            from include_owners
            where lower(include_path) like ?
               or lower(symbol_name) like ?
               or lower(module_name) like ?
               or lower(title) like ?
            limit ?
            """,
            (pattern, pattern, pattern, pattern, limit),
        ):
            add_row(row)

    results: list[dict[str, Any]] = []
    for row in rows:
        results.append(
            {
                "chunk_id": f"sidecar:include_owner:{row['owner_id']}",
                "source": "module_graph",
                "title": str(row["title"] or ""),
                "locator": str(row["include_path"] or ""),
                "chunk_index": 0,
                "text": str(row["text"] or ""),
                "symbol_name": str(row["symbol_name"] or ""),
                "symbol_kind": "include_owner",
                "module_name": str(row["module_name"] or ""),
                "score": 0.0,
            }
        )
    return results[:limit]


GUIDELINE_SIDECAR_TITLE_PATTERNS: dict[str, list[str]] = {
    "module_fix": [
        "%unreal error fix playbook%",
        "%unreal compile error triage%",
    ],
    "compile_fix": [
        "%unreal error fix playbook%",
        "%unreal compile error triage%",
    ],
    "reflection_fix": [
        "%unreal error fix playbook%",
        "%unreal compile error triage%",
    ],
    "blueprint_analysis": [
        "%shader, material, and blueprint analysis workflow%",
    ],
}

TAXONOMY_QUERY_HINTS = (
    "taxonomy",
    "work domain",
    "에셋 종류",
    "rag coverage",
    "asset type",
    "material layer",
    "material function",
)


def query_wants_taxonomy_sidecar(query: str, mode: str) -> bool:
    if mode == "api_lookup":
        return True
    raw = query.lower()
    return any(hint in raw for hint in TAXONOMY_QUERY_HINTS)


def fetch_guideline_sidecar(
    conn: sqlite3.Connection,
    mode: str,
    query: str,
    limit: int = 8,
) -> list[dict[str, Any]]:
    """Pull high-value guideline/profile chunks that FTS often misses behind symbol noise."""
    available_columns = table_columns(conn, "chunks")
    if not available_columns:
        return []

    meta_columns = (
        "project",
        "relative_path",
        "extension",
        "layer",
        "doc_type",
        "genre",
        "symbol_name",
        "symbol_kind",
        "module_name",
        "error_code",
        "error_file",
        "path_only",
    )
    select_columns = [
        "chunks.chunk_id",
        "chunks.source",
        "chunks.title",
        "chunks.locator",
        "chunks.chunk_index",
        "chunks.text",
        "-8000.0 as score",
    ]
    for column in meta_columns:
        if column in available_columns:
            select_columns.append(f"chunks.{column}")
        else:
            select_columns.append(f"'' as {column}")

    requests: list[tuple[str, str]] = []
    for pattern in GUIDELINE_SIDECAR_TITLE_PATTERNS.get(mode, []):
        requests.append(("project_guideline", pattern))

    query_lower = query.lower()
    if mode == "implementation" and "project profile" in query_lower:
        requests.append(("project_profile", "%project profile%"))
        for term in tokenize(query):
            if "_" in term and term.lower() not in {"build.cs", "target.cs"}:
                requests.append(("project_profile", f"%{term.lower()}%project profile%"))
                break

    if query_wants_taxonomy_sidecar(query, mode):
        requests.append(("project_guideline", "%unreal asset taxonomy for production work%"))

    seen: set[str] = set()
    results: list[dict[str, Any]] = []
    for source, pattern in requests:
        rows = conn.execute(
            f"""
            select
                {", ".join(select_columns)}
            from chunks
            where source = ?
              and lower(title) like ?
            limit ?
            """,
            (source, pattern.lower(), limit),
        ).fetchall()
        for row in rows:
            chunk_id = str(row["chunk_id"])
            if chunk_id in seen:
                continue
            seen.add(chunk_id)
            results.append(dict(row))
    return results[:limit]


def merge_ranked_sidecar(
    ranked: list[dict[str, Any]],
    sidecar: list[dict[str, Any]],
    query_terms: list[str],
    mode: str,
    *,
    extra_boost: float = 0.0,
) -> list[dict[str, Any]]:
    if not sidecar:
        return ranked
    sidecar_ranked = [rerank_row(dict(row), query_terms, mode) for row in sidecar]
    for row in sidecar_ranked:
        row["rank_score"] = float(row.get("rank_score") or 0.0) - extra_boost
    merged_map: dict[str, dict[str, Any]] = {str(r["chunk_id"]): r for r in ranked}
    for row in sidecar_ranked:
        merged_map[str(row["chunk_id"])] = row
    return sorted(
        merged_map.values(),
        key=lambda row: (float(row.get("rank_score") or 0.0), float(row.get("score") or 0.0)),
    )


VALID_MODES = {
    "auto",
    "planning",
    "design",
    "implementation",
    "review",
    "agent_edit",
    "codegen",
    "shader",
    "material_analysis",
    "material_porting",
    "blueprint_analysis",
    "blueprint_verification",
    "compile_fix",
    "runtime_debug",
    "api_lookup",
    "module_fix",
    "reflection_fix",
    "prototype_component",
    "prototype_subsystem",
    "refactor_r0",
    "refactor_r1",
    "refactor_r2",
    "refactor_r3",
    "refactor_r4",
}
META_COLUMNS = (
    "project",
    "relative_path",
    "extension",
    "layer",
    "doc_type",
    "genre",
    "symbol_name",
    "symbol_kind",
    "module_name",
    "error_code",
    "error_file",
    "path_only",
)

MODE_SOURCE_BIAS = {
    "planning": {
        "game_design_doc": -12.0,
        "project_guideline": -10.0,
        "unreal_project_text": -2.0,
        "epic_docs": -1.0,
        "unreal_source": 5.0,
        "unreal_symbol": 3.0,
        "build_log": 3.0,
        "unreal_project_asset_path": 4.0,
    },
    "design": {
        "game_design_doc": -8.0,
        "project_guideline": -7.0,
        "unreal_project_text": -3.0,
        "unreal_symbol": -2.0,
        "unreal_source": -1.0,
        "epic_docs": -1.0,
        "build_log": 1.0,
        "unreal_project_asset_path": 2.0,
    },
    "implementation": {
        "unreal_project_text": -5.0,
        "unreal_symbol": -5.0,
        "unreal_source": -4.0,
        "project_profile": -5.0,
        "game_design_doc": -2.0,
        "epic_docs": -2.0,
        "project_guideline": -1.0,
        "build_log": 1.0,
        "unreal_project_asset_path": 2.0,
    },
    "agent_edit": {
        "project_guideline": -9.0,
        "project_profile": -8.0,
        "unreal_project_text": -7.0,
        "unreal_symbol": -6.0,
        "module_graph": -5.0,
        "build_log": -4.0,
        "unreal_source": -3.0,
        "epic_docs": -1.0,
        "unreal_blueprint_metadata": -6.0,
        "unreal_material_metadata": -6.0,
        "unreal_animation_metadata": -6.0,
        "unreal_skeletal_mesh_metadata": -6.0,
        "unreal_anim_blueprint_metadata": -6.0,
        "unreal_anim_montage_metadata": -6.0,
        "unreal_sequencer_metadata": -6.0,
        "unreal_project_asset_path": 2.0,
    },
    "codegen": {
        "unreal_symbol": -9.0,
        "unreal_project_text": -6.0,
        "unreal_source": -4.0,
        "module_graph": -4.0,
        "project_profile": -4.0,
        "project_guideline": -2.0,
        "epic_docs": -2.0,
        "game_design_doc": -1.0,
        "build_log": 2.0,
        "unreal_project_asset_path": 3.0,
    },
    "shader": {
        "project_guideline": -10.0,
        "unreal_project_text": -9.0,
        "unreal_source": -7.0,
        "unreal_symbol": -5.0,
        "module_graph": -5.0,
        "project_profile": -4.0,
        "epic_docs": -2.0,
        "build_log": -2.0,
    },
    "material_analysis": {
        "unreal_material_metadata": -12.0,
        "project_guideline": -9.0,
        "unreal_project_text": -6.0,
        "unreal_project_asset_path": -4.0,
        "unreal_symbol": -2.0,
        "epic_docs": -1.0,
    },
    "material_porting": {
        "project_guideline": -12.0,
        "unreal_project_text": -9.0,
        "unreal_material_metadata": -7.0,
        "unreal_project_asset_path": -4.0,
        "unreal_symbol": -3.0,
        "module_graph": -2.0,
        "epic_docs": -1.0,
    },    "blueprint_analysis": {
        "unreal_blueprint_metadata": -12.0,
        "project_guideline": -9.0,
        "unreal_project_text": -6.0,
        "unreal_project_asset_path": -4.0,
        "unreal_symbol": -3.0,
        "epic_docs": -1.0,
    },
    "blueprint_verification": {
        "unreal_blueprint_metadata": -14.0,
        "project_guideline": -11.0,
        "build_log": -4.0,
        "unreal_project_text": -6.0,
        "unreal_project_asset_path": -4.0,
        "unreal_symbol": -3.0,
        "epic_docs": -1.0,
    },    "compile_fix": {
        "build_log": -10.0,
        "module_graph": -7.0,
        "project_profile": -5.0,
        "unreal_symbol": -8.0,
        "unreal_project_text": -5.0,
        "unreal_source": -3.0,
        "project_guideline": -2.0,
        "epic_docs": -1.0,
        "unreal_project_asset_path": 3.0,
    },
    "runtime_debug": {
        "build_log": -10.0,
        "unreal_project_text": -5.0,
        "project_profile": -5.0,
        "unreal_symbol": -4.0,
        "unreal_source": -2.0,
        "project_guideline": -1.0,
        "unreal_project_asset_path": 1.0,
    },
    "api_lookup": {
        "unreal_symbol": -10.0,
        "module_graph": -6.0,
        "unreal_source": -6.0,
        "epic_docs": -3.0,
        "unreal_project_text": -2.0,
        "project_guideline": -1.0,
        "unreal_project_asset_path": 3.0,
    },
    "module_fix": {
        "unreal_symbol": -10.0,
        "module_graph": -10.0,
        "project_profile": -5.0,
        "build_log": -9.0,
        "unreal_project_text": -5.0,
        "unreal_source": -3.0,
        "project_guideline": -2.0,
        "epic_docs": -1.0,
        "unreal_project_asset_path": 3.0,
    },
    "reflection_fix": {
        "unreal_symbol": -10.0,
        "module_graph": -7.0,
        "project_profile": -5.0,
        "build_log": -9.0,
        "unreal_project_text": -5.0,
        "unreal_source": -3.0,
        "project_guideline": -2.0,
        "epic_docs": -1.0,
        "unreal_project_asset_path": 3.0,
    },
    "prototype_component": {
        "project_guideline": -12.0,
        "unreal_symbol": -10.0,
        "unreal_project_text": -8.0,
        "module_graph": -5.0,
        "project_profile": -5.0,
        "unreal_source": -4.0,
        "build_log": -2.0,
        "epic_docs": -1.0,
    },
    "prototype_subsystem": {
        "project_guideline": -12.0,
        "unreal_symbol": -10.0,
        "unreal_project_text": -8.0,
        "module_graph": -5.0,
        "project_profile": -5.0,
        "unreal_source": -4.0,
        "build_log": -2.0,
        "epic_docs": -1.0,
    },
    "refactor_r0": {
        "project_guideline": -12.0,
        "game_design_doc": -4.0,
        "unreal_project_text": -3.0,
        "unreal_symbol": -2.0,
        "build_log": 1.0,
    },
    "refactor_r1": {
        "project_guideline": -11.0,
        "unreal_symbol": -6.0,
        "unreal_project_text": -5.0,
        "module_graph": -5.0,
        "project_profile": -4.0,
        "unreal_source": -2.0,
    },
    "refactor_r2": {
        "project_guideline": -10.0,
        "unreal_project_text": -8.0,
        "unreal_symbol": -7.0,
        "module_graph": -6.0,
        "build_log": -5.0,
        "project_profile": -5.0,
        "unreal_source": -3.0,
    },
    "refactor_r3": {
        "project_guideline": -9.0,
        "unreal_project_text": -8.0,
        "unreal_symbol": -7.0,
        "module_graph": -6.0,
        "build_log": -6.0,
        "project_profile": -5.0,
    },
    "refactor_r4": {
        "project_guideline": -8.0,
        "unreal_project_text": -7.0,
        "build_log": -6.0,
        "unreal_symbol": -5.0,
        "module_graph": -5.0,
    },
    "review": {
        "unreal_project_text": -5.0,
        "unreal_symbol": -4.0,
        "game_design_doc": -4.0,
        "project_guideline": -4.0,
        "unreal_source": -2.0,
        "epic_docs": -1.0,
        "build_log": -1.0,
        "unreal_blueprint_metadata": -5.0,
        "unreal_material_metadata": -5.0,
        "unreal_animation_metadata": -5.0,
        "unreal_skeletal_mesh_metadata": -5.0,
        "unreal_anim_blueprint_metadata": -5.0,
        "unreal_anim_montage_metadata": -5.0,
        "unreal_sequencer_metadata": -5.0,
        "unreal_project_asset_path": 1.0,
    },
}

MODE_LAYER_BIAS = {
    "planning": {
        "game_design": -9.0,
        "planning": -8.0,
        "genre": -7.0,
        "core_architecture": -3.0,
        "project_specific": -2.0,
        "unreal_source": 4.0,
        "unreal_symbol": 3.0,
    },
    "design": {
        "game_design": -7.0,
        "core_architecture": -6.0,
        "planning": -2.0,
        "genre": -2.0,
        "project_specific": -2.0,
        "type_symbol": -2.0,
        "module_symbol": -1.0,
    },
    "implementation": {
        "project_text": -3.0,
        "type_symbol": -5.0,
        "function_symbol": -4.0,
        "include_symbol": -4.0,
        "module_symbol": -4.0,
        "unreal_source": -3.0,
        "game_design": -1.0,
        "unreal_domain": -2.0,
        "official_docs": -2.0,
    },
    "agent_edit": {
        "unreal_domain": -9.0,
        "project_profile": -8.0,
        "project_text": -7.0,
        "type_symbol": -6.0,
        "function_symbol": -6.0,
        "include_symbol": -5.0,
        "module_symbol": -5.0,
        "module_graph": -5.0,
        "module_fix": -4.0,
        "build_error": -4.0,
        "unreal_source": -2.0,
    },
    "codegen": {
        "type_symbol": -8.0,
        "function_symbol": -7.0,
        "include_symbol": -6.0,
        "module_symbol": -5.0,
        "module_graph": -5.0,
        "project_profile": -4.0,
        "project_text": -4.0,
        "unreal_domain": -2.0,
        "core_architecture": -1.0,
    },
    "shader": {
        "unreal_domain": -10.0,
        "project_text": -9.0,
        "unreal_source": -7.0,
        "type_symbol": -5.0,
        "function_symbol": -5.0,
        "module_symbol": -5.0,
        "module_graph": -5.0,
        "build_error": -2.0,
    },
    "material_analysis": {
        "project_architecture": -10.0,
        "project_text": -6.0,
        "project_asset_path": -5.0,
        "unreal_domain": -5.0,
        "type_symbol": -2.0,
    },
    "blueprint_analysis": {
        "project_architecture": -10.0,
        "project_text": -6.0,
        "project_asset_path": -5.0,
        "unreal_domain": -5.0,
        "type_symbol": -3.0,
        "function_symbol": -3.0,
    },
    "blueprint_verification": {
        "project_architecture": -12.0,
        "project_text": -6.0,
        "project_asset_path": -5.0,
        "unreal_domain": -7.0,
        "type_symbol": -3.0,
        "function_symbol": -4.0,
        "build_error": -3.0,
    },    "compile_fix": {
        "compile_fix": -9.0,
        "module_fix": -8.0,
        "reflection_fix": -8.0,
        "link_fix": -8.0,
        "build_error": -8.0,
        "module_symbol": -7.0,
        "module_graph": -7.0,
        "project_profile": -5.0,
        "type_symbol": -5.0,
        "include_symbol": -5.0,
        "unreal_domain": -2.0,
    },
    "runtime_debug": {
        "runtime_debug": -10.0,
        "build_error": -7.0,
        "function_symbol": -4.0,
        "type_symbol": -3.0,
        "project_text": -3.0,
    },
    "api_lookup": {
        "type_symbol": -9.0,
        "function_symbol": -8.0,
        "include_symbol": -5.0,
        "module_symbol": -3.0,
        "module_graph": -4.0,
        "unreal_source": -3.0,
        "official_docs": -2.0,
    },
    "module_fix": {
        "module_symbol": -10.0,
        "module_graph": -10.0,
        "include_owner": -8.0,
        "include_edge": -8.0,
        "module_fix": -9.0,
        "include_symbol": -6.0,
        "build_error": -4.0,
    },
    "reflection_fix": {
        "type_symbol": -9.0,
        "function_symbol": -7.0,
        "reflection_fix": -9.0,
        "module_graph": -5.0,
        "project_profile": -5.0,
        "include_symbol": -5.0,
        "build_error": -4.0,
    },
    "review": {
        "project_text": -4.0,
        "game_design": -4.0,
        "core_architecture": -3.0,
        "unreal_domain": -2.0,
        "type_symbol": -2.0,
        "function_symbol": -2.0,
    },
    "prototype_component": {
        "unreal_domain": -10.0,
        "project_text": -8.0,
        "type_symbol": -8.0,
        "function_symbol": -7.0,
        "module_symbol": -5.0,
        "core_architecture": -2.0,
    },
    "prototype_subsystem": {
        "unreal_domain": -10.0,
        "project_text": -8.0,
        "type_symbol": -8.0,
        "function_symbol": -7.0,
        "module_symbol": -5.0,
        "core_architecture": -2.0,
    },
    "refactor_r0": {
        "core_architecture": -10.0,
        "project_rule": -9.0,
        "planning": -6.0,
        "project_specific": -2.0,
    },
    "refactor_r1": {
        "core_architecture": -9.0,
        "unreal_domain": -8.0,
        "project_text": -6.0,
        "type_symbol": -5.0,
    },
    "refactor_r2": {
        "unreal_domain": -9.0,
        "project_text": -8.0,
        "type_symbol": -7.0,
        "module_symbol": -6.0,
    },
    "refactor_r3": {
        "unreal_domain": -8.0,
        "project_text": -8.0,
        "compile_fix": -6.0,
        "module_fix": -5.0,
    },
    "refactor_r4": {
        "project_text": -7.0,
        "compile_fix": -6.0,
        "unreal_domain": -5.0,
    },
}

REFACTOR_R0_HINTS = {"r0", "discover", "impact", "ssot", "리팩터", "리팩토링", "영향", "발견", "책임"}
REFACTOR_HINTS = {"refactor", "리팩터", "리팩토링", "boundary", "rewire", "cleanup", "이동", "경계"}
COMPONENT_PROTOTYPE_HINTS = {"uactorcomponent", "component", "컴포넌트", "actorcomponent"}
SUBSYSTEM_PROTOTYPE_HINTS = {"subsystem", "worldsubsystem", "gameinstancesubsystem", "서브시스템"}
PLANNING_HINTS = {
    "계획",
    "기획",
    "프로토타입",
    "핵심",
    "루프",
    "게임",
    "장르",
    "레퍼런스",
    "first",
    "prototype",
    "planning",
    "loop",
}
DESIGN_HINTS = {"설계", "구조", "책임", "검토", "리뷰", "위험", "review", "architecture", "risk"}
REVIEW_HINTS = {
    "review",
    "audit",
    "findings",
    "code review",
    "project review",
    "architecture review",
    "리뷰",
    "코드리뷰",
    "코드 리뷰",
    "프로젝트 리뷰",
    "구조 리뷰",
    "전체 프로젝트",
    "전체 구조",
    "개선사항",
    "개선할",
    "부족한",
    "문제점",
    "위험",
}
DESIGN_HINTS.update({"설계", "구조", "책임", "검토", "위험"})

IMPLEMENTATION_HINTS = {
    "구현",
    "코드",
    "컴파일",
    "compile",
    "cpp",
    "header",
    "UCLASS",
    "UFUNCTION",
    "UPROPERTY",
    "GENERATED_BODY",
    "Build.cs",
}
CODEGEN_HINTS = {
    "생성",
    "작성",
    "만들기",
    "new",
    "create",
    "codegen",
    "code generation",
    "generate code",
    "코드 생성",
    "코드생성",
    "클래스 생성",
    "컴포넌트 생성",
    "서브시스템 생성",
    "component",
    "delegate",
    "multicast",
    "blueprintassignable",
    "broadcast",
    "선언",
    "바인딩",
}
AGENT_EDIT_HINTS = {
    "agent",
    "agentic",
    "edit",
    "apply",
    "modify",
    "change",
    "patch",
    "files",
    "diff",
    "current",
    "already",
    "wrapper",
    "agent_edit",
    "에이전트",
    "수정",
    "변경",
    "반복",
    "중복",
    "현재",
    "파일",
}
COMPILE_FIX_HINTS = {
    "error",
    "에러",
    "오류",
    "compile",
    "compiler",
    "빌드",
    "빌드오류",
    "빌드 오류",
    "컴파일",
    "컴파일오류",
    "컴파일 오류",
    "build",
    "failed",
    "failure",
    "ubt",
    "unrealbuildtool",
    "C1083",
    "LNK2019",
}
RUNTIME_DEBUG_HINTS = {"crash", "assert", "ensure", "debug", "디버깅", "크래시", "런타임", "log"}
API_LOOKUP_HINTS = {"api", "signature", "시그니처", "사용법", "lookup"}
MODULE_FIX_HINTS = {
    "Build.cs",
    "module",
    "dependency",
    "include",
    "C1083",
    "모듈",
    "의존성",
    "인클루드",
    "헤더를 열 수",
    "cannot open include",
    "missing module",
}
REFLECTION_FIX_HINTS = {
    "generated.h",
    "UHT",
    "UnrealHeaderTool",
    "UCLASS",
    "USTRUCT",
    "UFUNCTION",
    "UPROPERTY",
    "reflection",
    "리플렉션",
    "generated",
    "헤더툴",
    "언리얼헤더툴",
}
ASSET_METADATA_HINTS = {
    "blueprint",
    "bp_",
    "widget",
    "material",
    "materials",
    "materialinstance",
    "mi_",
    "m_",
    "texture",
    "parameter",
    "skeletalmesh",
    "skeletal_mesh",
    "animblueprint",
    "anim_bp",
    "anim montage",
    "animmontage",
    "montage",
    "notify",
    "sequencer",
    "levelsequence",
    "level sequence",
    "블루프린트",
    "머티리얼",
    "머터리얼",
    "재질",
    "텍스처",
}


SHADER_HINTS = {
    "shader",
    "셰이더",
    "쉐이더",
    "렌더",
    "렌더링",
    "usf",
    "ush",
    "hlsl",
    "globalshader",
    "materialshader",
    "shaderparameter",
    "rdg",
    "rendergraph",
    "rendercore",
    "shadercore",
    "rhi",
    "vertexfactory",
    "shadercompile",
    "virtual shader path",
}
MATERIAL_PORTING_HINTS = {
    "porting",
    "convert to material",
    "material graph conversion",
    "material graph로",
    "material node로",
    "머티리얼 노드",
    "머티리얼 그래프",
    "플러그인 말고",
    "post process to material",
    "surface material",
    "material function",
    "material parameter collection",
    "mpc",
    "머티리얼 변환",
    "머티리얼 포팅",
    "포스트프로세스 머티리얼",
}
MATERIAL_ANALYSIS_HINTS = {
    "material",
    "머티리얼",
    "메테리얼",
    "매테리얼",
    "materialinstance",
    "material expression",
    "material node",
    "머티리얼 노드",
    "노드 분석",
    "parameter",
    "파라미터",
    "texture parameter",
    "텍스처",
    "scalar parameter",
    "스칼라",
    "vector parameter",
    "벡터",
    "static switch",
    "스태틱 스위치",
    "shading model",
    "셰이딩 모델",
    "blend mode",
    "블렌드 모드",
}
BLUEPRINT_VERIFICATION_HINTS = {
    "verify blueprint",
    "blueprint verification",
    "bp verification",
    "pin link",
    "pin links",
    "node link",
    "node links",
    "graph links",
    "connected pins",
    "editor export",
    "metadata export",
    "bp 확인",
    "블루프린트 확인",
    "핀 연결",
    "노드 연결",
    "그래프 연결",
}
BLUEPRINT_ANALYSIS_HINTS = {
    "blueprint graph",
    "blueprint variable",
    "blueprint function",
    "bp_",
    "widget blueprint",
    "event graph",
    "construction script",
    "function call",
    "call function",
    "pin",
    "node graph",
}
BLUEPRINT_VERIFICATION_HINTS.update({
    "블루프린트 검증",
    "블루프린트 확인",
    "핀 연결",
    "노드 연결",
    "그래프 연결",
})
BLUEPRINT_ANALYSIS_HINTS.update({
    "블루프린트",
    "블루프린트 그래프",
    "블루프린트 구조",
    "블루프린트 변수",
    "블루프린트 함수",
    "이벤트 그래프",
    "컨스트럭션 스크립트",
    "핀",
    "노드",
})


@dataclass
class SearchOptions:
    mode: str = "auto"
    sources: list[str] = field(default_factory=list)
    projects: list[str] = field(default_factory=list)
    layers: list[str] = field(default_factory=list)
    doc_types: list[str] = field(default_factory=list)
    genres: list[str] = field(default_factory=list)
    extensions: list[str] = field(default_factory=list)
    required_terms: list[str] = field(default_factory=list)
    candidate_limit: int = 120


def tokenize(value: str) -> list[str]:
    return [term for term in TERM_RE.findall(value) if len(term) > 1]


def expand_query_terms(terms: list[str]) -> list[str]:
    expanded: list[str] = []
    seen: set[str] = set()
    for term in terms:
        candidates = [term]
        if len(term) > 3 and term[0] in {"A", "E", "F", "I", "S", "T", "U"} and term[1].isupper():
            candidates.append(term[1:])
        for candidate in candidates:
            key = candidate.lower()
            if key not in seen:
                expanded.append(candidate)
                seen.add(key)
    return expanded


def normalize_values(values: list[str] | None) -> list[str]:
    normalized: list[str] = []
    for value in values or []:
        for part in str(value).split(","):
            part = part.strip()
            if part:
                normalized.append(part)
    return normalized


def normalize_extensions(values: list[str] | None) -> list[str]:
    extensions: list[str] = []
    for value in normalize_values(values):
        extensions.append(value if value.startswith(".") else f".{value}")
    return extensions


def has_hint(terms: set[str], raw: str, hints: set[str]) -> bool:
    return any(hint.lower() in terms or hint.lower() in raw for hint in hints)


def has_any_raw(raw: str, markers: tuple[str, ...]) -> bool:
    return any(marker in raw for marker in markers)


def resolve_mode(query: str, mode: str) -> str:
    if mode not in VALID_MODES:
        mode = "auto"
    if mode != "auto":
        return mode

    terms = {term.lower() for term in tokenize(query)}
    raw = query.lower()
    material_raw = has_any_raw(raw, ("\uba38\ud2f0\ub9ac\uc5bc", "\uba54\ud14c\ub9ac\uc5bc", "\ub9e4\ud14c\ub9ac\uc5bc"))
    blueprint_raw = "\ube14\ub8e8\ud504\ub9b0\ud2b8" in raw or bool(re.search(r"\bbp[_-]", raw))
    shader_raw = has_any_raw(raw, ("shader", "\uc170\uc774\ub354", "\uc250\uc774\ub354", "\ub80c\ub354", "\ub80c\ub354\ub9c1"))
    porting_raw = has_any_raw(
        raw,
        ("porting", "convert", "conversion", "\ubcc0\ud658", "\ud3ec\ud305", "post process", "post-process", "\ud3ec\uc2a4\ud2b8\ud504\ub85c\uc138\uc2a4"),
    )
    if material_raw and porting_raw:
        return "material_porting"
    if shader_raw:
        return "shader"
    if blueprint_raw:
        if has_any_raw(raw, ("\uc5f0\uacb0", "\ud655\uc778", "\uac80\uc99d", "pin link", "node link", "graph link")):
            return "blueprint_verification"
        return "blueprint_analysis"
    if material_raw:
        return "material_analysis"

    if has_hint(terms, raw, REFACTOR_R0_HINTS):
        return "refactor_r0"
    if has_hint(terms, raw, SUBSYSTEM_PROTOTYPE_HINTS):
        return "prototype_subsystem"
    if has_hint(terms, raw, COMPONENT_PROTOTYPE_HINTS):
        return "prototype_component"
    if has_hint(terms, raw, REFACTOR_HINTS):
        return "refactor_r2"
    if has_hint(terms, raw, PLANNING_HINTS):
        return "planning"
    if has_hint(terms, raw, REFLECTION_FIX_HINTS):
        return "reflection_fix"
    if has_hint(terms, raw, MODULE_FIX_HINTS):
        return "module_fix"
    if has_hint(terms, raw, RUNTIME_DEBUG_HINTS):
        return "runtime_debug"
    if has_hint(terms, raw, COMPILE_FIX_HINTS):
        return "compile_fix"
    if has_hint(terms, raw, SHADER_HINTS):
        return "shader"
    material_requested = has_hint(terms, raw, MATERIAL_ANALYSIS_HINTS) or has_any_raw(
        raw, ("머티리얼", "메테리얼", "매테리얼")
    )
    material_porting_requested = has_hint(terms, raw, MATERIAL_PORTING_HINTS) and has_any_raw(
        raw, ("porting", "convert", "conversion", "변환", "포팅", "post process", "post-process", "포스트프로세스")
    )
    if material_porting_requested:
        return "material_porting"
    blueprint_requested = has_hint(terms, raw, BLUEPRINT_ANALYSIS_HINTS) or "블루프린트" in raw
    blueprint_verify_requested = blueprint_requested and (
        has_hint(terms, raw, BLUEPRINT_VERIFICATION_HINTS) or
        blueprint_requested and has_any_raw(raw, ("연결", "확인", "검증", "pin link", "node link", "graph link"))
    )
    if blueprint_verify_requested:
        return "blueprint_verification"
    if blueprint_requested:
        return "blueprint_analysis"
    if material_requested:
        return "material_analysis"
    if has_hint(terms, raw, MATERIAL_PORTING_HINTS) or any(
        marker in raw
        for marker in ("post process to material", "convert to material", "material graph conversion")
    ):
        return "material_porting"
    if has_hint(terms, raw, BLUEPRINT_VERIFICATION_HINTS) or any(
        marker in raw
        for marker in ("verify blueprint", "pin links", "node links", "editor export", "metadata export")
    ):
        return "blueprint_verification"
    if has_hint(terms, raw, SHADER_HINTS):
        return "shader"
    if has_hint(terms, raw, MATERIAL_ANALYSIS_HINTS):
        return "material_analysis"
    if has_hint(terms, raw, BLUEPRINT_ANALYSIS_HINTS):
        return "blueprint_analysis"
    if has_hint(terms, raw, REVIEW_HINTS):
        return "review"
    if has_hint(terms, raw, API_LOOKUP_HINTS):
        return "api_lookup"
    if has_hint(terms, raw, AGENT_EDIT_HINTS):
        return "agent_edit"
    if has_hint(terms, raw, CODEGEN_HINTS):
        return "codegen"
    if has_hint(terms, raw, IMPLEMENTATION_HINTS):
        return "implementation"
    if has_hint(terms, raw, DESIGN_HINTS):
        return "design"
    return "implementation"


def make_fts_query(query: str) -> str:
    terms = tokenize(query)
    if not terms:
        terms = [query.strip()] if query.strip() else []
    if not terms:
        return '""'
    return " OR ".join(f'"{term}"' for term in terms[:32])


def add_in_filter(
    clauses: list[str],
    params: list[Any],
    column: str,
    values: list[str],
    available_columns: set[str],
) -> None:
    if not values or column not in available_columns:
        return
    placeholders = ",".join("?" for _ in values)
    clauses.append(f"chunks.{column} in ({placeholders})")
    params.extend(values)


def row_matches_required(row: dict[str, Any], required_terms: list[str]) -> bool:
    if not required_terms:
        return True
    haystack = " ".join(
        str(row.get(name) or "")
        for name in (
            "title",
            "locator",
            "relative_path",
            "symbol_name",
            "module_name",
            "error_code",
            "error_file",
            "text",
        )
    ).lower()
    return all(term.lower() in haystack for term in required_terms)


ASSET_EXACT_SOURCES = (
    "unreal_blueprint_metadata",
    "unreal_material_metadata",
    "unreal_animation_metadata",
    "unreal_skeletal_mesh_metadata",
    "unreal_anim_blueprint_metadata",
    "unreal_anim_montage_metadata",
    "unreal_sequencer_metadata",
    "unreal_asset_registry",
    "unreal_project_asset_path",
    "unreal_level_metadata",
)


def asset_identity_terms(query: str) -> list[str]:
    terms: list[str] = []
    for term in tokenize(query):
        lower = term.lower()
        if "_" in term or lower.startswith(("m_", "mi_", "bp_", "abp_", "wbp_", "sk_", "t_")):
            terms.append(term)
    for match in re.findall(r"/Game/[A-Za-z0-9_./-]+", query):
        terms.append(match.rsplit("/", 1)[-1] or match)
    return list(dict.fromkeys(terms))


def rerank_row(row: dict[str, Any], query_terms: list[str], mode: str) -> dict[str, Any]:
    score = float(row.get("score") or 0.0)
    source = str(row.get("source") or "")
    layer = str(row.get("layer") or "")
    extension = str(row.get("extension") or "")
    symbol_name = str(row.get("symbol_name") or "")
    symbol_kind = str(row.get("symbol_kind") or "")
    module_name = str(row.get("module_name") or "")
    error_code = str(row.get("error_code") or "")
    identity = " ".join(
        str(row.get(name) or "")
        for name in (
            "title",
            "locator",
            "relative_path",
            "extension",
            "genre",
            "symbol_name",
            "symbol_kind",
            "module_name",
            "error_code",
            "error_file",
        )
    )
    identity_terms = {term.lower() for term in tokenize(identity)}
    identity_lower = identity.lower()
    text = str(row.get("text") or "").lower()
    query_lower = " ".join(query_terms).lower()

    score += MODE_SOURCE_BIAS.get(mode, {}).get(source, 0.0)
    score += MODE_LAYER_BIAS.get(mode, {}).get(layer, 0.0)

    matched_terms = 0
    for term in query_terms:
        term_lower = term.lower()
        if term_lower in identity_lower and (source.endswith("_metadata") or "_" in term_lower or "/" in identity_lower):
            score -= 8.0
            matched_terms += 1
        elif term_lower in identity_terms:
            score -= 0.55
            matched_terms += 1
        elif term_lower in text:
            score -= 0.15
            matched_terms += 1

    if query_terms:
        score -= min(2.0, matched_terms / len(query_terms) * 2.0)

    if "uactorcomponent" in query_lower and "actorcomponent" in identity_terms:
        score -= 12.0
    if "build" in query_lower and "cs" in query_lower:
        if identity_lower.endswith(".build.cs") or ".build.cs" in identity_lower:
            score -= 4.0
        elif extension == ".cs":
            score -= 1.0
        if source == "project_guideline" and ("build.cs" in text or "dependency" in text):
            score -= 8.0
    if ("c1083" in query_lower or "cannot open include" in query_lower) and source == "project_guideline":
        if "c1083" in text or "cannot open include" in text or "build.cs" in text:
            score -= 8.0
    if "generated_body" in query_lower and extension == ".h":
        score -= 0.5
    if "generated" in query_lower and "generated.h" in text:
        score -= 4.0
    if mode in {"material_analysis", "material_porting"}:
        if "materialinstancedynamic_" in identity_lower and "materialinstancedynamic" not in query_lower:
            score += 10.0
        if source == "unreal_material_metadata" and any(
            marker in identity_lower for marker in ("m_", "mi_", "/game/")
        ):
            score -= 2.0
    if "uht" in query_lower or "unrealheadertool" in query_lower:
        if error_code.upper() == "UHT" or "unrealheadertool" in text:
            score -= 6.0
        if symbol_kind in {"class", "struct", "interface", "enum", "function", "property"}:
            score -= 2.0
    if "lnk2019" in query_lower and error_code.upper() == "LNK2019":
        score -= 8.0
    if "build" in query_lower and "cs" in query_lower and symbol_kind == "module":
        score -= 5.0
    if "module" in query_lower and symbol_kind == "module" and mode == "module_fix":
        score -= 22.0
    if source == "project_guideline" and "compile error triage" in identity_lower and mode in {"module_fix", "reflection_fix"}:
        score -= 18.0
    if mode == "module_fix" and source == "project_guideline" and "unreal error fix playbook" in identity_lower:
        if "c1083" in query_lower or "cannot open include" in query_lower:
            score -= 18.0
    if mode == "runtime_debug" and source == "project_guideline" and "runtime debugging playbook" in identity_lower:
        score -= 20.0
    if mode == "codegen" and source == "project_guideline" and "codegen recipes" in identity_lower:
        score -= 10.0
        if any(marker in query_lower for marker in ("aactor", "actor", "uobject", "uactorcomponent", "subsystem", "gameinstancesubsystem", "worldsubsystem")):
            if "core types" in identity_lower:
                score -= 18.0
        if any(marker in query_lower for marker in ("enhanced input", "inputaction", "bindaction", "replication", "rpc", "doreplifetime", "gameplaytag")):
            if "gameplay systems" in identity_lower:
                score -= 18.0
    if source == "project_guideline" and "diagram response rules" in identity_lower:
        if mode in {"planning", "design", "review", "agent_edit", "material_analysis", "material_porting", "blueprint_analysis", "blueprint_verification", "shader"} or any(
            marker in query_lower
            for marker in ("diagram", "mermaid", "ascii", "fallback", "structure", "architecture", "dependency", "ownership", "graph", "flow", "node")
        ):
            score -= 16.0
    if mode == "codegen" and any(
        marker in query_lower for marker in ("delegate", "multicast", "blueprintassignable", "broadcast")
    ):
        if source == "project_guideline" and "gameplay systems" in identity_lower:
            score -= 14.0
        if source == "project_guideline" and "codegen recipes" in identity_lower and "delegate" in text:
            score -= 10.0
    if symbol_name and symbol_name.lower() in query_lower:
        score -= 8.0
    if module_name and module_name.lower() in query_lower:
        score -= 4.0
    if mode == "implementation" and "project profile" in query_lower and source == "unreal_project_asset_path":
        score += 45.0
    if mode in {"module_fix", "compile_fix"} and symbol_kind == "module" and "module dependencies" in identity_lower:
        if any(
            marker in query_lower
            for marker in ("c1083", "cannot open include", "missing module", "build.cs", "gameplaytags")
        ):
            score += 35.0
    if mode == "implementation" and source == "project_profile":
        score -= 10.0
    if mode == "agent_edit":
        if source == "project_guideline" and any(
            marker in text or marker in identity_lower
            for marker in (
                "global file edit",
                "agentic",
                "edit discipline",
                "current file",
                "current diff",
                "duplicate",
                "wrapper mandatory",
                "compile readiness",
            )
        ):
            score -= 14.0
        if source == "project_profile":
            score -= 4.0
        if source == "unreal_project_text":
            score -= 3.0
        if ".build.cs" in identity_lower or ".target.cs" in identity_lower:
            score -= 2.0
    if any(marker in query_lower for marker in ASSET_METADATA_HINTS):
        if source == "unreal_blueprint_metadata" and any(
            marker in query_lower for marker in ("blueprint", "bp_", "widget", "블루프린트")
        ):
            score -= 12.0
        if source == "unreal_material_metadata" and any(
            marker in query_lower
            for marker in ("material", "materials", "materialinstance", "mi_", "m_", "texture", "parameter", "머티리얼", "머터리얼", "재질", "텍스처")
        ):
            score -= 12.0
        if source in {
            "unreal_animation_metadata",
            "unreal_skeletal_mesh_metadata",
            "unreal_anim_blueprint_metadata",
            "unreal_anim_montage_metadata",
            "unreal_sequencer_metadata",
        } and any(
            marker in query_lower
            for marker in (
                "skeletalmesh",
                "skeletal_mesh",
                "animblueprint",
                "anim_bp",
                "anim montage",
                "animmontage",
                "montage",
                "notify",
                "sequencer",
                "levelsequence",
                "level sequence",
            )
        ):
            score -= 12.0
        if source == "unreal_project_asset_path":
            score -= 2.0

    if mode == "shader":
        if extension in {".usf", ".ush"}:
            score -= 18.0
        if source == "project_guideline" and any(marker in identity_lower or marker in text for marker in ("shader", "usf", "ush", "rdg", "rendercore")):
            score -= 12.0
        if ".build.cs" in identity_lower and any(marker in query_lower for marker in ("module", "rendercore", "rhi", "shadercore", "plugin")):
            score -= 6.0
    if mode == "material_analysis":
        if source == "unreal_material_metadata":
            score -= 18.0
        if source == "project_guideline" and any(marker in identity_lower or marker in text for marker in ("material graph", "material screenshot", "material expression", "parameter inventory")):
            score -= 12.0
    if mode == "material_porting":
        if extension in {".usf", ".ush"} or "materialgraphcommon" in identity_lower or "tse_mg_" in text:
            score -= 18.0
        if source == "project_guideline" and any(marker in identity_lower or marker in text for marker in ("material graph porting", "post process to material", "hallucination blocklist", "proof levels")):
            score -= 14.0
        if source == "unreal_material_metadata":
            score -= 8.0
    if mode == "blueprint_analysis":
        if source == "unreal_blueprint_metadata":
            score -= 18.0
        if source == "project_guideline" and any(marker in identity_lower or marker in text for marker in ("blueprint graph", "function call", "variable inventory", "pin")):
            score -= 12.0
    if mode == "blueprint_verification":
        if source == "unreal_blueprint_metadata":
            score -= 22.0
        if source == "project_guideline" and any(marker in identity_lower or marker in text for marker in ("blueprint verification", "proof levels", "asset mutation boundary", "pin")):
            score -= 16.0
        if source == "build_log":
            score -= 4.0
    if str(row.get("path_only") or "") == "1" and mode in {"planning", "design", "implementation", "agent_edit", "codegen"}:
        score += 2.0

    updated = dict(row)
    updated["rank_score"] = score
    updated["resolved_mode"] = mode
    return updated


def search(index: Path, query: str, top_k: int, options: SearchOptions | None = None) -> list[dict[str, Any]]:
    options = options or SearchOptions()
    mode = resolve_mode(query, options.mode)
    effective_query = query
    preferred_modes: list[str] = []
    if mode in {"compile_fix", "module_fix", "reflection_fix"}:
        try:
            from failure_memory_rerank import expand_query_with_memory

            mem_dir = Path(__file__).resolve().parent.parent / "data" / "failure_memory"
            project = (options.projects or [""])[0] if options.projects else ""
            effective_query = expand_query_with_memory(query, mem_dir, project=project)
        except Exception:
            effective_query = query
        preferred_modes = preferred_modes_from_error_route(query, mode)
        if preferred_modes:
            effective_query = f"{effective_query} {' '.join(preferred_modes)}"
    if mode == "agent_edit":
        effective_query = (
            query
            + " agent_edit agentic current file current diff duplicate edit no-op "
            + "Global File Edit Rules Agentic Unreal Edit Operating Protocol"
        )
    if mode == "shader":
        effective_query = (
            query
            + " usf ush HLSL shader plugin RenderCore RHI ShaderCore GlobalShader RDG "
            + "virtual shader path Build.cs shader compile"
        )
    if mode == "material_analysis":
        effective_query = (
            query
            + " material material instance material expression scalar vector texture parameter "
            + "static switch blend mode shading model screenshot graph analysis"
        )
    if mode == "blueprint_analysis":
        effective_query = (
            query
            + " blueprint graph variable function call node pin event graph construction script "
            + "generated class parent class metadata"
        )
    sources = normalize_values(options.sources)
    projects = normalize_values(options.projects)
    layers = normalize_values(options.layers)
    doc_types = normalize_values(options.doc_types)
    genres = normalize_values(options.genres)
    extensions = normalize_extensions(options.extensions)
    required_terms = normalize_values(options.required_terms)
    candidate_limit = max(top_k, options.candidate_limit, top_k * 20)

    conn = get_index_connection(index)
    available_columns = table_columns(conn, "chunks")

    select_columns = [
        "chunks.chunk_id",
        "chunks.source",
        "chunks.title",
        "chunks.locator",
        "chunks.chunk_index",
        "chunks.text",
        "bm25(chunks_fts) as score",
    ]
    for column in META_COLUMNS:
        if column in available_columns:
            select_columns.append(f"chunks.{column}")
        else:
            select_columns.append(f"'' as {column}")

    clauses = ["chunks_fts match ?"]
    params: list[Any] = [make_fts_query(effective_query)]
    add_in_filter(clauses, params, "source", sources, available_columns)
    add_in_filter(clauses, params, "project", projects, available_columns)
    add_in_filter(clauses, params, "layer", layers, available_columns)
    add_in_filter(clauses, params, "doc_type", doc_types, available_columns)
    add_in_filter(clauses, params, "genre", genres, available_columns)
    add_in_filter(clauses, params, "extension", extensions, available_columns)
    params.append(candidate_limit)

    rows = conn.execute(
        f"""
        select
            {", ".join(select_columns)}
        from chunks_fts
        join chunks on chunks_fts.rowid = chunks.rowid
        where {" and ".join(clauses)}
        order by score
        limit ?
        """,
        params,
    ).fetchall()

    exact_rows: list[sqlite3.Row] = []
    exact_terms = asset_identity_terms(query)
    if mode == "implementation" and "project profile" in query.lower():
        exact_terms = []
    if exact_terms:
        exact_select_columns = [
            item.replace("bm25(chunks_fts) as score", "-10000.0 as score").replace("chunks.", "")
            for item in select_columns
        ]
        source_placeholders = ",".join("?" for _ in ASSET_EXACT_SOURCES)
        # Merge all exact-term searches into a single query with OR conditions
        # instead of issuing one query per term (N+1 anti-pattern).
        terms_to_match = exact_terms[:6]
        or_clauses = " or ".join(
            "(lower(title) like ? or lower(locator) like ?)" for _ in terms_to_match
        )
        patterns: list[str] = []
        for term in terms_to_match:
            p = f"%{term.lower()}%"
            patterns.extend([p, p])
        exact_params_batch: list[Any] = [*ASSET_EXACT_SOURCES, *patterns, 20 * len(terms_to_match)]
        exact_rows = list(
            conn.execute(
                f"""
                select
                    {", ".join(exact_select_columns)}
                from chunks
                where source in ({source_placeholders})
                  and ({or_clauses})
                limit ?
                """,
                exact_params_batch,
            ).fetchall()
        )
    if exact_rows:
        rows = list(rows) + exact_rows

    query_terms = expand_query_terms(tokenize(effective_query))
    ranked = [
        rerank_row(dict(row), query_terms, mode)
        for row in rows
        if row_matches_required(dict(row), required_terms)
    ]
    ranked.sort(key=lambda row: (float(row.get("rank_score") or 0.0), float(row.get("score") or 0.0)))

    if mode in {"module_fix", "compile_fix", "reflection_fix", "blueprint_analysis"} or (
        mode == "implementation" and "project profile" in query.lower()
    ) or query_wants_taxonomy_sidecar(query, mode):
        guideline_sidecar = fetch_guideline_sidecar(conn, mode, query, limit=max(8, top_k))
        for preferred_mode in preferred_modes_from_error_route(query, mode):
            if preferred_mode != mode:
                guideline_sidecar.extend(fetch_guideline_sidecar(conn, preferred_mode, query, limit=4))
        profile_boost = 200.0 if mode == "implementation" else 120.0
        ranked = merge_ranked_sidecar(
            ranked,
            guideline_sidecar,
            query_terms,
            mode,
            extra_boost=profile_boost,
        )

    if mode == "module_fix":
        sidecar = fetch_module_graph_sidecar(conn, effective_query, limit=max(12, top_k * 2))
        if sidecar:
            query_lower = effective_query.lower()
            header_names = set(_header_basenames_from_query(effective_query))
            sidecar_ranked = [rerank_row(dict(row), query_terms, mode) for row in sidecar]
            for row in sidecar_ranked:
                sym = str(row.get("symbol_name") or "").lower()
                path = str(row.get("locator") or row.get("include_path") or "").lower()
                boost = 0.0
                if sym and sym in query_lower:
                    boost = 45.0
                if sym in header_names or any(sym == h or path.endswith("/" + h) for h in header_names):
                    boost = 100.0
                if boost:
                    row["rank_score"] = float(row.get("rank_score") or 0.0) - boost
            merged_map: dict[str, dict[str, Any]] = {str(r["chunk_id"]): r for r in ranked}
            for row in sidecar_ranked:
                merged_map[str(row["chunk_id"])] = row
            ranked = sorted(
                merged_map.values(),
                key=lambda row: (float(row.get("rank_score") or 0.0), float(row.get("score") or 0.0)),
            )

    structured_sidecars: list[dict[str, Any]] = []
    structured_sidecars.extend(fetch_error_route_sidecar(query, mode, limit=3))
    structured_sidecars.extend(fetch_module_resolver_sidecar(effective_query, mode, limit=5))
    structured_sidecars.extend(fetch_symbol_graph_sidecar(query, mode, limit=5))
    structured_sidecars.extend(fetch_architecture_sidecar(query, mode, limit=5))
    if structured_sidecars:
        ranked = merge_ranked_sidecar(
            ranked,
            structured_sidecars,
            query_terms,
            mode,
            extra_boost=160.0,
        )

    from retrieval_profiles import apply_retrieval_layer_bonus

    ranked = apply_retrieval_layer_bonus(ranked, mode)
    return ranked[:top_k]


def search_hybrid(index: Path, query: str, top_k: int, options: SearchOptions | None = None) -> list[dict[str, Any]]:
    from rag_semantic import hybrid_search

    return hybrid_search(index, query, top_k, options, fts_search_fn=search)
