#!/usr/bin/env python
"""Resolve a set of exact projects to one normalized Unreal engine binding."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from unreal_engine_discovery import engine_association_version

ProjectBindingResolver = Callable[[Path, Path], dict[str, Any]]


def resolve_request_engine_binding(
    descriptors: list[Path],
    workspace: Path,
    resolver: ProjectBindingResolver,
) -> dict[str, Any]:
    facts = [resolver(project, workspace) for project in descriptors]
    failed = next((item for item in facts if item.get("ok") is not True), None)
    if failed is not None:
        return failed
    unknown = next((item for item in facts if item.get("engineVersionStatus") == "unknown"
                    or item.get("engineVersionSource") == "folder_name_inferred"), None)
    if unknown is not None:
        # A directory name can help locate an index, but it cannot establish
        # which engine the project uses. Do not silently drop the reader's
        # unknown provenance when matching a shard and reporting its version.
        return {
            "ok": False,
            "errorCode": "PROJECT_ENGINE_VERSION_UNRESOLVED",
            "error": "The project engine version is unverified; observe Build.version or declare an EngineAssociation before selecting an engine-bound RAG index.",
            "engineVersionStatus": "unknown",
            "engineVersionSource": unknown.get("engineVersionSource"),
            "inferredEngineVersion": unknown.get("engineVersion"),
            "engineRoot": unknown.get("engineRoot"),
            "project": unknown.get("project"),
        }
    bindings = {
        (
            str(item["engineVersion"]),
            str(item.get("engineAssociation") or "").strip(),
        )
        for item in facts
    }
    normalized = {
        (
            version,
            ""
            if not association or engine_association_version(association) == version
            else association.casefold(),
        )
        for version, association in bindings
    }
    if len(normalized) != 1:
        return {
            "ok": False,
            "errorCode": "RAG_MULTI_ENGINE_QUERY_UNSUPPORTED",
            "error": "One RAG call cannot combine projects bound to different Unreal versions.",
            "projectEngineVersions": sorted({str(item["engineVersion"]) for item in facts}),
            "projectEngineAssociations": sorted(
                {str(item.get("engineAssociation") or "") for item in facts}
            ),
            "projects": [str(path) for path in descriptors],
        }
    requested_version, requested_association = next(iter(bindings))
    custom_association = next(iter(normalized))[1]
    return {
        "ok": True,
        "facts": facts,
        "requestedVersion": requested_version,
        "requestedAssociation": requested_association,
        "customAssociation": custom_association,
        "reportedAssociation": requested_association if custom_association else None,
    }


__all__ = ["resolve_request_engine_binding"]
