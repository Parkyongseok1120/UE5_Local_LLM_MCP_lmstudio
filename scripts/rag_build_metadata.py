"""Normalize indexed routing fields for RAG chunks."""

from __future__ import annotations

from pathlib import Path

from portable_path_identity import filesystem_path_identity
from rag_build_classification import infer_doc_type, infer_genre, infer_layer

def metadata_fields(
    source: str,
    title: str,
    locator: str,
    metadata: dict,
) -> dict[str, str]:
    project_root_raw = str(
        metadata.get("project_root") or metadata.get("projectRoot") or ""
    ).strip()
    project_root = ""
    if project_root_raw:
        project_root = filesystem_path_identity(
            Path(project_root_raw).expanduser().resolve(),
            strip_project_uri=False,
        )
    return {
        "project": str(metadata.get("project") or ""),
        "project_root": project_root,
        "relative_path": str(metadata.get("relative_path") or ""),
        "extension": str(metadata.get("extension") or Path(locator).suffix or "").lower(),
        "layer": infer_layer(source, title, metadata),
        "doc_type": infer_doc_type(source, metadata),
        "genre": (
            str(metadata.get("genre") or infer_genre(title, metadata))
            if source in {"project_guideline", "game_design_doc"}
            else ""
        ),
        "path_only": "1" if metadata.get("path_only") else "0",
        "symbol_name": str(metadata.get("symbol_name") or ""),
        "symbol_kind": str(metadata.get("symbol_kind") or ""),
        "module_name": str(metadata.get("module_name") or ""),
        "error_code": str(metadata.get("error_code") or ""),
        "error_file": str(metadata.get("error_file") or ""),
    }


__all__ = ["metadata_fields"]
