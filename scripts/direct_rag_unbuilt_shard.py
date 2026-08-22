#!/usr/bin/env python
"""Resolve a missing engine shard without writing into an incompatible namespace."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from direct_rag_project_engine import normalize_engine_version
from workspace_paths import engine_version_to_namespace, resolve_engine_version

_SHARD_NAMESPACE = re.compile(
    r"^unreal\d+(?:-[a-z0-9](?:[a-z0-9-]{0,22}[a-z0-9])?-[0-9a-f]{10})?$"
)


def is_shard_namespace(value: object) -> bool:
    return bool(_SHARD_NAMESPACE.fullmatch(str(value or "").strip().casefold()))


def shard_namespace(engine_version: str, custom_association: str = "") -> str:
    base = engine_version_to_namespace(engine_version).casefold()
    custom = str(custom_association or "").strip().casefold()
    if not custom:
        return base
    slug = re.sub(r"[^a-z0-9]+", "-", custom).strip("-")[:24] or "custom"
    digest = hashlib.sha256(custom.encode("utf-8")).hexdigest()[:10]
    return f"{base}-{slug}-{digest}"


def resolve_missing_engine_index(
    base: Path,
    workspace: Path,
    *,
    requested_version: str,
    reported_association: object,
    custom_association: str,
    current_version: str,
    descriptors: list[Path],
    allow_unbuilt: bool,
) -> dict[str, Any]:
    projects = [str(path) for path in descriptors]
    if allow_unbuilt:
        configured = normalize_engine_version(resolve_engine_version(workspace))
        requested_namespace = shard_namespace(requested_version, custom_association)
        managed_registry = (
            base.name.casefold() == "rag.sqlite"
            and is_shard_namespace(base.parent.name)
        )
        sibling = (
            base.parent.parent / requested_namespace / "rag.sqlite"
            if managed_registry
            else base
        ).resolve()
        configured_target = (
            base
            if not managed_registry or base.parent.name.casefold() == requested_namespace
            else sibling
            if custom_association
            else base
        )
        if not configured_target.exists() and (
            custom_association
            or requested_version == configured
            or base.parent.name.casefold() == requested_namespace
        ):
            return {
                "ok": True,
                "index": str(configured_target),
                "projectEngineVersion": requested_version,
                "projectEngineAssociation": reported_association,
                "indexEngineVersion": requested_version,
                "projects": projects,
                "usedSiblingIndex": configured_target != base,
                "unbuiltIndex": True,
            }
        if managed_registry and current_version:
            if not sibling.exists():
                return {
                    "ok": True,
                    "index": str(sibling),
                    "projectEngineVersion": requested_version,
                    "indexEngineVersion": requested_version,
                    "projects": projects,
                    "usedSiblingIndex": True,
                    "unbuiltIndex": True,
                }
    return {
        "ok": False,
        "errorCode": "RAG_ENGINE_INDEX_MISMATCH",
        "error": (
            f"The selected project uses Unreal {requested_version}, but no sibling RAG index "
            "with that manifest version is available."
        ),
        "projectEngineVersion": requested_version,
        "projectEngineAssociation": reported_association,
        "indexEngineVersion": current_version or None,
        "configuredIndex": str(base),
        "projects": projects,
    }


__all__ = ["is_shard_namespace", "resolve_missing_engine_index", "shard_namespace"]
