"""Resolve and persist the installer's exact Unreal project/engine binding."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from unreal_engine_discovery import (
    engine_association_folder,
    engine_location_candidates,
    engine_root_matches_numeric_association,
    engine_root_numeric_version,
)
from unreal_engine_registration import (
    default_launcher_manifest_paths,
    is_engine_root,
    launcher_registration_rows,
    registered_engine_root,
)
from unreal_engine_resolution import resolve_engine_root_for_association_impl


MAX_UPROJECT_BYTES = 4 * 1024 * 1024


@dataclass(frozen=True)
class InstallerEngineBinding:
    association: str
    engine_root: Path | None
    source: str


def project_engine_association(project: Path | None) -> str:
    """Read the exact association from one selected descriptor, failing closed."""

    if project is None:
        return ""
    try:
        if project.stat().st_size > MAX_UPROJECT_BYTES:
            raise ValueError(f"active project descriptor is too large: {project}")
        payload = json.loads(project.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not read active project descriptor {project}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"active project descriptor must contain a JSON object: {project}")
    association = payload.get("EngineAssociation", "")
    if not isinstance(association, str):
        raise ValueError(f"active project EngineAssociation must be a string: {project}")
    return association.strip()


def resolve_installer_engine_binding(
    *,
    active_project: Path | None,
    requested_engine_root: Path | None,
    selection: str,
    shared_config: dict[str, Any],
    workspace_root: Path,
) -> InstallerEngineBinding:
    """Delegate selection to the canonical resolver and enforce numeric parity."""

    association = project_engine_association(active_project)
    selection_kind = str(selection or "").strip().casefold()
    explicit_root = None if selection_kind == "environment" else requested_engine_root
    resolution_config: dict[str, Any] = {} if selection_kind == "launcher" else shared_config
    resolution = resolve_engine_root_for_association_impl(
        association,
        workspace_root,
        explicit_engine_root=explicit_root,
        runtime_config=resolution_config,
    )
    if not resolution.get("ok"):
        if association or explicit_root is not None:
            raise ValueError(str(resolution.get("error") or "Unreal Engine resolution failed"))
        return InstallerEngineBinding(association="", engine_root=None, source="")

    root_text = str(resolution.get("engineRoot") or "").strip()
    engine_root = Path(root_text).resolve() if root_text else None
    if engine_root is not None and not is_engine_root(engine_root):
        raise ValueError(f"resolved engine root is not a usable Unreal Engine layout: {engine_root}")
    if (
        engine_root is not None
        and association
        and not engine_root_matches_numeric_association(engine_root, association)
    ):
        label = "--engine-root" if explicit_root is not None else "Resolved engine"
        raise ValueError(
            f"{label} does not match the active project's EngineAssociation "
            f"{association!r}: {engine_root}"
        )
    return InstallerEngineBinding(
        association=association,
        engine_root=engine_root,
        source=str(resolution.get("source") or ""),
    )


def bind_installer_engine(
    *,
    active_project: Path | None,
    requested_engine_root: Path | None,
    selection: str,
    shared_config: dict[str, Any],
    workspace_root: Path,
) -> InstallerEngineBinding:
    """Resolve once, then update only the installer's shared engine fields."""

    binding = resolve_installer_engine_binding(
        active_project=active_project,
        requested_engine_root=requested_engine_root,
        selection=selection,
        shared_config=shared_config,
        workspace_root=workspace_root,
    )
    shared_config["defaultEngineRoot"] = (
        str(binding.engine_root) if binding.engine_root is not None else ""
    )
    if binding.association and not engine_association_folder(binding.association):
        mappings = shared_config.get("engineRootsByAssociation")
        shared_config["engineRootsByAssociation"] = {
            **(mappings if isinstance(mappings, dict) else {}),
            binding.association: str(binding.engine_root),
        }
    return binding


def launcher_manifest_engine_locations() -> list[Path]:
    """Compatibility projection over the canonical launcher registration reader."""

    roots = []
    for _association, value, _source in launcher_registration_rows(
        default_launcher_manifest_paths()
    ):
        root = registered_engine_root(value)
        if root is not None and root not in roots:
            roots.append(root)
    return roots


def detect_engine_root(association: str = "") -> Path | None:
    """Compatibility helper backed by the canonical resolver/discovery policy."""

    resolution = resolve_engine_root_for_association_impl(
        association,
        runtime_config={},
    )
    root = str(resolution.get("engineRoot") or "") if resolution.get("ok") else ""
    return Path(root).resolve() if root else None


def engine_version_from_root(engine_root: Path | None) -> str:
    return engine_root_numeric_version(engine_root) if engine_root is not None else ""


common_engine_locations = engine_location_candidates
engine_root_is_valid = is_engine_root


__all__ = [
    "InstallerEngineBinding",
    "bind_installer_engine",
    "common_engine_locations",
    "detect_engine_root",
    "engine_root_is_valid",
    "engine_version_from_root",
    "launcher_manifest_engine_locations",
    "project_engine_association",
    "resolve_installer_engine_binding",
]
