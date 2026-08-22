#!/usr/bin/env python
"""Read and preserve the Unreal engine provenance of one RAG generation."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from direct_rag_generation_identity import read_consistent_manifest
from direct_rag_project_engine import normalize_engine_version
from unreal_engine_discovery import engine_association_version


def read_manifest_engine_binding(index_dir: Path) -> tuple[str, str]:
    payload = read_consistent_manifest(index_dir)
    return (
        normalize_engine_version(payload.get("engineVersion")),
        str(payload.get("engineAssociation") or "").strip(),
    )


def read_manifest_generation_identity(index_dir: Path) -> dict[str, str]:
    payload = read_consistent_manifest(index_dir)
    return {
        "engineVersion": normalize_engine_version(payload.get("engineVersion")),
        "engineAssociation": str(payload.get("engineAssociation") or "").strip(),
        "generationId": str(payload.get("generationId") or "").strip(),
    }


def _custom_association(value: str) -> str:
    text = value.strip()
    return "" if not text or engine_association_version(text) else text.casefold()


def engine_bindings_match(
    requested_version: str,
    requested_association: str,
    indexed_version: str,
    indexed_association: str,
) -> bool:
    if normalize_engine_version(requested_version) != normalize_engine_version(indexed_version):
        return False
    requested_custom = _custom_association(requested_association)
    indexed_custom = _custom_association(indexed_association)
    return requested_custom == indexed_custom if requested_custom or indexed_custom else True


def resolve_generation_engine_binding(
    index_dir: Path,
    *,
    engine_version: str | None,
    engine_association: str | None,
) -> dict[str, Any]:
    """Use explicit provenance or preserve an existing generation's binding."""

    existing_version, existing_association = read_manifest_engine_binding(index_dir)
    requested_version = (
        normalize_engine_version(engine_version)
        if engine_version is not None
        else existing_version
    )
    requested_association = (
        str(engine_association or "").strip()
        if engine_association is not None
        else existing_association
    )
    if engine_version is not None and str(engine_version).strip() and not requested_version:
        return {
            "ok": False,
            "errorCode": "RAG_ENGINE_BINDING_INVALID",
            "error": f"Invalid explicit Unreal engine version: {engine_version}",
        }
    if existing_version and requested_version and existing_version != requested_version:
        return {
            "ok": False,
            "errorCode": "RAG_ENGINE_BINDING_MISMATCH",
            "error": (
                f"Refusing to rebuild an Unreal {existing_version} RAG generation as "
                f"Unreal {requested_version}."
            ),
        }
    existing_custom = _custom_association(existing_association)
    requested_custom = _custom_association(requested_association)
    if engine_association is not None and existing_custom != requested_custom and (
        existing_custom or requested_custom
    ):
        return {
            "ok": False,
            "errorCode": "RAG_ENGINE_BINDING_MISMATCH",
            "error": "Refusing to change a custom EngineAssociation on an existing RAG generation.",
        }
    return {
        "ok": True,
        "engineVersion": requested_version,
        "engineAssociation": requested_association,
    }


__all__ = [
    "engine_bindings_match",
    "read_manifest_engine_binding",
    "read_manifest_generation_identity",
    "resolve_generation_engine_binding",
]
