#!/usr/bin/env python
"""RAG index health and rebuild status helpers."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

RAW_INPUT_FILES = (
    "raw_guidelines.jsonl",
    "raw_game_design.jsonl",
    "raw_symbols.jsonl",
    "raw_module_graph.jsonl",
    "raw_project_profiles.jsonl",
    "raw_build_logs.jsonl",
    "raw_docs.jsonl",
    "raw_source.jsonl",
    "raw_projects.jsonl",
)


def _iso_mtime(path: Path) -> str | None:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()
    except OSError:
        return None


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
        "indexPath": str(index),
        "indexExists": index.exists(),
        "chunksJsonl": _file_info(chunks_jsonl),
        "chunkCount": 0,
        "sourceBreakdown": {},
        "layerBreakdown": {},
        "lastBuiltAt": None,
    }

    if index.exists():
        info["indexFile"] = _file_info(index)
        info["lastBuiltAt"] = info["indexFile"]["modifiedAt"]
        conn = sqlite3.connect(index)
        try:
            info["chunkCount"] = int(conn.execute("select count(*) from chunks").fetchone()[0])
            for row in conn.execute(
                "select source, count(*) from chunks group by source order by count(*) desc"
            ):
                info["sourceBreakdown"][str(row[0])] = int(row[1])
            for row in conn.execute(
                "select layer, count(*) from chunks where layer != '' group by layer order by count(*) desc limit 20"
            ):
                info["layerBreakdown"][str(row[0])] = int(row[1])
        except sqlite3.DatabaseError as exc:
            info["indexError"] = str(exc)
            info["indexReadable"] = False
        finally:
            conn.close()
    else:
        info["indexFile"] = _file_info(index)

    info.setdefault("indexReadable", bool(index.exists()))
    if info.get("indexError"):
        info["okForChat"] = False
        info["chatAction"] = "stop_and_report_rag_rebuild_required"
        info["chatMessage"] = (
            "RAG index is present but unreadable. Do not search project files for RAG repair scripts from MCP chat; "
            "tell the user to run .\\rag.ps1 build or .\\rag.ps1 doctor from the RAG workspace."
        )
    elif index.exists() and int(info.get("chunkCount") or 0) > 0:
        info["okForChat"] = True
        info["chatAction"] = "continue"
    else:
        info["okForChat"] = False
        info["chatAction"] = "stop_and_report_rag_rebuild_required"
        info["chatMessage"] = (
            "RAG index is missing or empty. Do not continue with RAG-dependent coding claims; "
            "tell the user to run .\\rag.ps1 build or .\\rag.ps1 doctor from the RAG workspace."
        )
    return info


def rebuild_status(index: Path, data_dir: Path | None = None) -> dict[str, Any]:
    data_dir = data_dir or index.parent
    health = index_health(index, data_dir)
    inputs: list[dict[str, Any]] = []
    newest_input_mtime: float | None = None
    newest_input_name: str | None = None

    for name in RAW_INPUT_FILES:
        path = data_dir / name
        file_info = _file_info(path)
        inputs.append({"name": name, **file_info})
        if file_info["exists"] and file_info["modifiedAt"]:
            mtime = Path(path).stat().st_mtime
            if newest_input_mtime is None or mtime > newest_input_mtime:
                newest_input_mtime = mtime
                newest_input_name = name

    index_mtime = index.stat().st_mtime if index.exists() else None
    stale = False
    reason = "index-up-to-date"

    if not index.exists():
        stale = True
        reason = "index-missing"
    elif health.get("indexError"):
        stale = True
        reason = "index-unreadable"
    elif newest_input_mtime is not None and (index_mtime is None or newest_input_mtime > index_mtime):
        stale = True
        reason = f"input-newer-than-index ({newest_input_name})"
    elif health["chunkCount"] == 0:
        stale = True
        reason = "index-empty"

    chunks_jsonl = data_dir / "chunks.jsonl"
    if chunks_jsonl.exists() and index.exists():
        if chunks_jsonl.stat().st_mtime > index.stat().st_mtime:
            stale = True
            reason = "chunks-jsonl-newer-than-index"

    return {
        **health,
        "needsRebuild": stale,
        "reason": reason,
        "rawInputs": inputs,
        "buildManifest": _file_info(data_dir / "build_manifest.json"),
        "recommendedCommand": ".\\rag.ps1 build",
        "recommendedDoctorCommand": ".\\rag.ps1 doctor",
        "chatAction": health.get("chatAction", "continue"),
        "chatMessage": health.get("chatMessage", ""),
        "collectHints": [
            ".\\rag.ps1 collect-guidelines",
            ".\\rag.ps1 collect-projects -CopyProjectText",
            ".\\rag.ps1 collect-symbols",
            ".\\rag.ps1 collect-module-graph",
        ],
        "architecture": {
            "unrealRagRole": "Evidence retrieval for Unreal C++ knowledge, guidelines, symbols, and project text.",
            "unrealAgentRole": "Sandboxed file edits and UnrealBuildTool execution in WORKSPACE_ROOT.",
            "loraRole": "Answer format and behavior tuning; knowledge stays in RAG.",
        },
    }


def capabilities_summary() -> dict[str, Any]:
    return {
        "tools": {
            "unreal_rag_search": "Mode-aware hybrid FTS + symbol retrieval.",
            "unreal_symbol_lookup": "Shortcut for class/function/API symbol lookup.",
            "unreal_open_project_picker": "Open Windows GUI to pick active .uproject.",
            "unreal_get_active_project": "Read shared activeProject for RAG and agent.",
            "unreal_set_active_project": "Set or clear shared activeProject (.uproject path).",
            "unreal_rag_health": "Index size, chunk counts, source breakdown.",
            "unreal_rag_rebuild_status": "Whether raw inputs are newer than the index.",
            "unreal_start_compile_loop": "Start wrapper as background job (non-blocking).",
            "unreal_compile_loop_status": "Poll wrapper job progress and output.",
        },
        "cliAlternatives": {
            "wrapper": ".\\rag.ps1 wrapper -Question \"...\"",
            "query": ".\\rag.ps1 query -Question \"...\"",
            "build": ".\\rag.ps1 build",
        },
    }
