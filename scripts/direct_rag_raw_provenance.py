#!/usr/bin/env python
"""Fail closed when raw corpus ownership disagrees with a build binding."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterator

from direct_rag_raw_scope import is_project_scoped_raw
from index_inputs import RAW_INPUT_FILES
from workspace_paths import filesystem_path_identity

ENGINE_RAW_FILES = ("raw_source.jsonl", "raw_symbols.jsonl")


def _rows(path: Path) -> Iterator[dict[str, Any]]:
    if not path.is_file():
        return
    for line_no, line in enumerate(
        path.read_text(encoding="utf-8-sig", errors="strict").splitlines(), start=1
    ):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Invalid raw provenance JSONL {path.name}:{line_no}: {exc}") from exc
        if not isinstance(row, dict):
            raise RuntimeError(f"Raw provenance row must be an object: {path.name}:{line_no}")
        yield row


def _metadata(row: dict[str, Any]) -> dict[str, Any]:
    value = row.get("metadata")
    return value if isinstance(value, dict) else {}


def _project_descriptors(index_dir: Path) -> set[Path]:
    descriptors: set[Path] = set()
    for name in RAW_INPUT_FILES:
        if name in ENGINE_RAW_FILES:
            continue
        for row in _rows(index_dir / name):
            metadata = _metadata(row)
            source = str(row.get("source") or "").strip()
            root_text = str(
                metadata.get("project_root") or metadata.get("projectRoot") or ""
            ).strip()
            project = str(metadata.get("project") or row.get("project") or "").strip()
            project_scoped = is_project_scoped_raw(source, metadata, name)
            if not project_scoped:
                continue
            if not root_text or not project:
                raise RuntimeError(
                    f"Project-scoped raw row lacks exact root/stem provenance: {name}"
                )
            root = Path(root_text).expanduser().resolve()
            descriptor = root / f"{project}.uproject"
            if not descriptor.is_file():
                raise RuntimeError(
                    f"Raw project provenance does not identify an exact descriptor: {name}"
                )
            descriptors.add(descriptor.resolve())
    return descriptors


def _engine_source_roots(index_dir: Path) -> set[str]:
    roots: set[str] = set()
    for name in ENGINE_RAW_FILES:
        for row in _rows(index_dir / name):
            metadata = _metadata(row)
            root = filesystem_path_identity(
                metadata.get("root") or "", strip_project_uri=False
            )
            if not root:
                raise RuntimeError(f"Engine raw row has no collector root: {name}")
            path_text = str(row.get("path") or "").strip()
            if path_text:
                path = Path(path_text).expanduser().resolve()
                source_root = Path(str(metadata.get("root"))).expanduser().resolve()
                if not path.is_relative_to(source_root):
                    raise RuntimeError(f"Engine raw row escapes its collector root: {name}")
            roots.add(root)
    return roots


def validate_raw_provenance(
    *,
    index_dir: Path,
    workspace: Path,
    engine_version: str,
    engine_association: str,
    engine_root: str = "",
) -> dict[str, Any]:
    from direct_rag_manifest_binding import engine_bindings_match
    from direct_rag_project_engine import project_engine_version

    resolved_engine_roots: set[Path] = set()
    descriptors = _project_descriptors(index_dir)
    for descriptor in descriptors:
        binding = project_engine_version(descriptor, workspace)
        if binding.get("ok") is not True or not engine_bindings_match(
            str(binding.get("engineVersion") or ""),
            str(binding.get("engineAssociation") or ""),
            engine_version,
            engine_association,
        ):
            return {
                "ok": False,
                "errorCode": "RAG_RAW_PROJECT_ENGINE_MISMATCH",
                "error": f"Raw project belongs to a different engine binding: {descriptor}",
            }
        root_text = str(binding.get("engineRoot") or "").strip()
        if root_text:
            resolved_engine_roots.add(Path(root_text).expanduser().resolve())
    if engine_root:
        resolved_engine_roots.add(Path(engine_root).expanduser().resolve())
    if len(resolved_engine_roots) > 1:
        return {
            "ok": False,
            "errorCode": "RAG_RAW_MULTI_ENGINE_CORPUS",
            "error": "Raw inputs resolve to more than one Unreal Engine root.",
        }
    raw_roots = _engine_source_roots(index_dir)
    if raw_roots and not resolved_engine_roots:
        return {
            "ok": False,
            "errorCode": "RAG_RAW_ENGINE_PROVENANCE_REQUIRED",
            "error": "Engine raw inputs require an exact project or engine-root provenance.",
        }
    if resolved_engine_roots:
        expected = filesystem_path_identity(
            next(iter(resolved_engine_roots)) / "Engine" / "Source",
            strip_project_uri=False,
        )
        if raw_roots and raw_roots != {expected}:
            return {
                "ok": False,
                "errorCode": "RAG_RAW_ENGINE_ROOT_MISMATCH",
                "error": "Engine raw inputs were collected from a different Engine/Source root.",
                "expectedRoot": expected,
                "rawRoots": sorted(raw_roots),
            }
    return {
        "ok": True,
        "projectCount": len(descriptors),
        "engineRawRoots": sorted(raw_roots),
    }


__all__ = ["validate_raw_provenance"]
