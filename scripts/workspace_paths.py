#!/usr/bin/env python
"""Compatibility facade for portable Unreal workspace and engine paths.

Concrete ownership lives in focused modules. Existing callers can continue to
import the historical ``workspace_paths`` names without inheriting a God Object.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path

from active_project_paths import (
    indexing_tier,
    resolve_active_project_path,
    resolve_active_project_root,
    resolve_active_project_source_root,
)
from editor_export_paths import (
    auto_editor_export_enabled,
    default_editor_export_dir,
    editor_export_content_path,
    editor_export_dir,
    normalize_editor_export_dir,
)
from portable_path_identity import (
    ascii_windows_fold,
    canonical_absolute_path_identity,
    filesystem_path_identity,
    is_windows_host_platform,
    normalize_portable_path,
    resolve_canonical_absolute_path,
)
from unreal_engine_discovery import (
    NUMERIC_ENGINE_ASSOCIATION_RE as _NUMERIC_ENGINE_ASSOCIATION_RE,
    discover_engine_roots_impl as _discover_engine_roots,
    engine_association_folder,
    engine_association_version,
    engine_associations_match,
    engine_build_version,
    engine_location_candidates as _engine_location_candidates,
    engine_root_matches_numeric_association,
    engine_root_numeric_version,
    engine_sort_key as _engine_sort_key,
)
from unreal_engine_registration import (
    MAX_ENGINE_REGISTRATION_BYTES as _MAX_ENGINE_REGISTRATION_BYTES,
    WINDOWS_ENGINE_BUILDS_KEY as _WINDOWS_ENGINE_BUILDS_KEY,
    application_settings_dirs as _application_settings_dirs,
    default_install_ini_paths as _default_install_ini_paths,
    default_launcher_manifest_paths as _default_launcher_manifest_paths,
    install_ini_registration_rows as _install_ini_registration_rows,
    is_engine_root as _is_engine_root,
    launcher_registration_rows as _launcher_registration_rows,
    read_bounded_registration_text as _read_bounded_registration_text,
    registered_engine_installations,
    registered_engine_root as _registered_engine_root,
    valid_engine_association_token as _valid_engine_association_token,
    windows_registry_registration_rows as _windows_registry_registration_rows,
)
from unreal_engine_resolution import (
    configured_engine_roots_by_association as _configured_engine_roots_by_association,
    engine_root_from_config_value as _engine_root_from_config_value,
    engine_root_resolution as _engine_root_resolution,
    resolve_engine_root_for_association_impl,
    unresolved_engine_association as _unresolved_engine_association,
)
from unreal_engine_runtime_paths import resolve_ubt_path_with
from workspace_config import (
    DEFAULT_ENGINE_VERSION,
    DEFAULT_INDEX_NAMESPACE,
    DEFAULT_LMSTUDIO_ROOT,
    DEFAULT_SHARED_CONFIG,
    FALLBACK_INDEX_REL,
    RUNTIME_INDEX_PATH_ENV,
    WORKSPACE_DIR_NAMES,
    active_project_names,
    canonical_workspace_root,
    engine_version_to_namespace,
    find_workspace_root,
    index_namespace_from_version,
    load_shared_config,
    load_workspace_config,
    save_shared_config,
    shared_config_path,
)
from workspace_index_paths import (
    INDEX_CONFIG_KEYS,
    _index_settings,
    _index_settings_at_root,
    _read_workspace_index_settings,
    _read_workspace_index_settings_at_root,
    _resolve_configured_index_path,
    _runtime_index_path_override,
    resolve_engine_version,
    resolve_index_dir,
    resolve_index_namespace,
    resolve_index_path,
    resolve_index_path_in_workspace,
)
from workspace_locator import (
    identity_relative_suffix as _identity_relative_suffix,
    normalize_locator_impl,
)

LEGACY_LOCATOR_PREFIXES: tuple[str, ...] = ()


def discover_engine_roots(
    host_platform: str | None = None,
    environ: dict[str, str] | None = None,
    home: Path | None = None,
    *,
    launcher_manifest_paths: Iterable[Path] | None = None,
    registry_installations: Mapping[str, object] | None = None,
    install_ini_paths: Iterable[Path] | None = None,
    read_system_registry: bool | None = None,
) -> list[Path]:
    """Return validated Unreal roots while retaining the legacy test seam."""

    if all(
        value is None
        for value in (
            host_platform,
            environ,
            home,
            launcher_manifest_paths,
            registry_installations,
            install_ini_paths,
            read_system_registry,
        )
    ):
        return _discover_engine_roots()
    return _discover_engine_roots(
        host_platform,
        environ,
        home,
        launcher_manifest_paths=launcher_manifest_paths,
        registry_installations=registry_installations,
        install_ini_paths=install_ini_paths,
        read_system_registry=read_system_registry,
    )


def resolve_engine_root_for_association(
    association: object,
    start: Path | None = None,
    *,
    explicit_engine_root: str | Path | None = None,
    runtime_config: Mapping[str, object] | None = None,
    host_platform: str | None = None,
    environ: dict[str, str] | None = None,
    home: Path | None = None,
    launcher_manifest_paths: Iterable[Path] | None = None,
    registry_installations: Mapping[str, object] | None = None,
    install_ini_paths: Iterable[Path] | None = None,
    read_system_registry: bool | None = None,
) -> dict[str, str | bool]:
    return resolve_engine_root_for_association_impl(
        association,
        start,
        explicit_engine_root=explicit_engine_root,
        runtime_config=runtime_config,
        host_platform=host_platform,
        environ=environ,
        home=home,
        launcher_manifest_paths=launcher_manifest_paths,
        registry_installations=registry_installations,
        install_ini_paths=install_ini_paths,
        read_system_registry=read_system_registry,
        discover_roots_fn=discover_engine_roots,
        registered_installations_fn=registered_engine_installations,
    )


def resolve_engine_root(start: Path | None = None) -> Path:
    resolution = resolve_engine_root_for_association("", start)
    root = str(resolution.get("engineRoot") or "")
    return Path(root) if root else Path("")


def resolve_ubt_path(start: Path | None = None) -> Path:
    return resolve_ubt_path_with(start, resolve_engine_root)


def resolve_engine_source_root(start: Path | None = None) -> Path:
    return resolve_engine_root(start) / "Engine" / "Source"


def normalize_locator(
    locator: str,
    workspace_root: Path | None = None,
    *,
    host_platform: str | None = None,
) -> str:
    return normalize_locator_impl(
        locator,
        workspace_root,
        host_platform=host_platform,
        legacy_prefixes=LEGACY_LOCATOR_PREFIXES,
        find_workspace_root_fn=find_workspace_root,
        canonical_workspace_root_fn=canonical_workspace_root,
    )
