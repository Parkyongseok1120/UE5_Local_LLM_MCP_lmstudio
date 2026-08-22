#!/usr/bin/env python
"""Translate physical and legacy locators to one canonical workspace root."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import Path

from portable_path_identity import filesystem_path_identity, normalize_portable_path

RootResolver = Callable[[Path | None], Path]


def identity_relative_suffix(
    candidate: str,
    prefix: str,
    host_platform: str | None = None,
) -> str | None:
    candidate_path = normalize_portable_path(candidate, strip_project_uri=False)
    prefix_path = normalize_portable_path(prefix, strip_project_uri=False)
    candidate_identity = filesystem_path_identity(
        candidate_path,
        host_platform,
        strip_project_uri=False,
    )
    prefix_identity = filesystem_path_identity(
        prefix_path,
        host_platform,
        strip_project_uri=False,
    )
    if not candidate_identity or not prefix_identity:
        return None
    if candidate_identity == prefix_identity:
        return ""
    boundary = prefix_identity if prefix_identity.endswith("/") else f"{prefix_identity}/"
    if not candidate_identity.startswith(boundary):
        return None
    return candidate_path[len(prefix_path) :].lstrip("/")


def normalize_locator_impl(
    locator: str,
    workspace_root: Path | None,
    *,
    host_platform: str | None,
    legacy_prefixes: Iterable[str],
    find_workspace_root_fn: RootResolver,
    canonical_workspace_root_fn: RootResolver,
) -> str:
    physical_root = (workspace_root or find_workspace_root_fn(None)).resolve()
    canonical_root = canonical_workspace_root_fn(workspace_root)
    text = str(locator or "").strip()
    if not text:
        return text

    normalized = text.replace("\\", "/")
    for legacy in legacy_prefixes:
        suffix = identity_relative_suffix(
            normalized,
            legacy.replace("\\", "/"),
            host_platform,
        )
        if suffix is not None:
            return str(canonical_root / Path(suffix))

    physical_text = str(physical_root).replace("\\", "/")
    suffix = identity_relative_suffix(normalized, physical_text, host_platform)
    if suffix is not None:
        return str(canonical_root / Path(suffix))

    canonical_text = str(canonical_root).replace("\\", "/")
    if identity_relative_suffix(normalized, canonical_text, host_platform) is not None:
        return str(Path(normalized))
    return text
