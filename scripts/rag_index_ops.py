#!/usr/bin/env python
"""RAG index health and rebuild status helpers."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from incremental_build import manifest_stale
from direct_rag_generation_identity import RagGenerationTransitionError
from direct_rag_readonly_db import connect_readonly
from index_inputs import RAW_INPUT_FILES

REQUIRED_CHUNK_COLUMNS = frozenset({"project_root"})


def _file_info(path: Path) -> dict[str, Any]:
    try:
        stat = path.stat()
        return {
            "path": str(path),
            "exists": True,
            "sizeBytes": stat.st_size,
            "modifiedAt": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
        }
    except OSError:
        return {"path": str(path), "exists": False, "sizeBytes": 0, "modifiedAt": None}


def index_health(index: Path, data_dir: Path | None = None) -> dict[str, Any]:
    data_dir = data_dir or index.parent
    chunks_jsonl = data_dir / "chunks.jsonl"
    info: dict[str, Any] = {
        "executionStatus": "succeeded",
        "indexStatus": "unknown",
        "projectBindingStatus": "unknown",
        "errorCode": "",
        "indexPath": str(index),
        "indexExists": index.exists(),
        "chunksJsonl": _file_info(chunks_jsonl),
        "chunkCount": 0,
        "sourceBreakdown": {},
        "layerBreakdown": {},
        "schemaMissingColumns": [],
        "forbiddenSourceRows": 0,
        "lastBuiltAt": None,
    }

    if index.exists():
        info["indexFile"] = _file_info(index)
        info["lastBuiltAt"] = info["indexFile"]["modifiedAt"]
        conn: sqlite3.Connection | None = None
        try:
            conn = connect_readonly(index)
            columns = {
                str(row[1])
                for row in conn.execute("pragma table_info(chunks)").fetchall()
            }
            info["schemaMissingColumns"] = sorted(REQUIRED_CHUNK_COLUMNS - columns)
            info["chunkCount"] = int(conn.execute("select count(*) from chunks").fetchone()[0])
            info["forbiddenSourceRows"] = int(
                conn.execute(
                    "select count(*) from chunks where source = ?",
                    ("unreal_failure_memory",),
                ).fetchone()[0]
            )
            for row in conn.execute(
                "select source, count(*) from chunks group by source order by count(*) desc"
            ):
                info["sourceBreakdown"][str(row[0])] = int(row[1])
            for row in conn.execute(
                "select layer, count(*) from chunks where layer != '' group by layer order by count(*) desc limit 20"
            ):
                info["layerBreakdown"][str(row[0])] = int(row[1])
        except (sqlite3.Error, OSError, RagGenerationTransitionError) as exc:
            info["indexError"] = str(exc)
            info["indexReadable"] = False
        finally:
            if conn is not None:
                conn.close()
    else:
        info["indexFile"] = _file_info(index)

    info.setdefault("indexReadable", bool(index.exists()))
    if info.get("indexError"):
        info["indexStatus"] = "unavailable"
        info["errorCode"] = "RAG_INDEX_UNREADABLE"
    elif index.exists() and int(info.get("chunkCount") or 0) == 0:
        info["indexStatus"] = "not_ready"
        info["errorCode"] = "RAG_INDEX_EMPTY"
    elif info.get("schemaMissingColumns"):
        info["indexStatus"] = "migration_required"
        info["errorCode"] = "RAG_INDEX_SCHEMA_OUTDATED"
    elif int(info.get("forbiddenSourceRows") or 0) > 0:
        info["indexStatus"] = "migration_required"
        info["errorCode"] = "RAG_INDEX_FORBIDDEN_SOURCE_PRESENT"
    elif index.exists() and int(info.get("chunkCount") or 0) > 0:
        info["indexStatus"] = "ready"
    else:
        info["indexStatus"] = "not_ready"
        info["errorCode"] = "RAG_INDEX_MISSING"
    return info


def rebuild_status(index: Path, data_dir: Path | None = None) -> dict[str, Any]:
    data_dir = data_dir or index.parent
    health = index_health(index, data_dir)
    inputs: list[dict[str, Any]] = []
    for name in RAW_INPUT_FILES:
        path = data_dir / name
        file_info = _file_info(path)
        inputs.append({"name": name, **file_info})

    manifest_path = data_dir / "build_manifest.json"
    stale, reason = manifest_stale(data_dir, manifest_path, index)
    if health.get("indexError"):
        stale, reason = True, "index-unreadable"
    elif health.get("schemaMissingColumns"):
        stale, reason = True, "index-schema-outdated"
    elif int(health.get("forbiddenSourceRows") or 0) > 0:
        stale, reason = True, "forbidden-source-present"
    elif health["chunkCount"] == 0:
        stale, reason = True, "index-empty"

    return {
        **health,
        "needsRebuild": stale,
        "reason": reason,
        "rawInputs": inputs,
        "buildManifest": _file_info(manifest_path),
    }


def capabilities_summary() -> dict[str, Any]:
    return {
        "tools": {
            "unreal_rag_search": "Mode-aware hybrid FTS + symbol retrieval.",
            "unreal_symbol_lookup": "Shortcut for class/function/API symbol lookup.",
            "unreal_get_active_project": "Read shared activeProject for RAG and agent.",
            "unreal_set_active_project": "Set or clear shared activeProject (.uproject path).",
            "unreal_rag_health": "Index size, chunk counts, source breakdown.",
            "unreal_rag_rebuild_status": "Whether raw inputs are newer than the index.",
            "unreal_rag_refresh": "Synchronously refresh selected active-project index inputs.",
            "unreal_rag_capabilities": "Describe the bounded Direct RAG surface.",
        },
        "cliAlternatives": {
            "doctor": ".\\rag.ps1 doctor",
            "build": ".\\rag.ps1 build",
            "refresh": ".\\rag.ps1 refresh",
        },
    }
