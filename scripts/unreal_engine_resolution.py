#!/usr/bin/env python
"""Bind one Unreal project association to one exact engine installation."""

from __future__ import annotations

import os
import sys
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path

from portable_path_identity import (
    canonical_absolute_path_identity,
    filesystem_path_identity,
)
from unreal_engine_discovery import (
    discover_engine_roots,
    engine_association_folder,
    engine_associations_match,
    engine_root_matches_numeric_association,
)
from unreal_engine_registration import is_engine_root, registered_engine_installations
from workspace_config import (
    canonical_workspace_root,
    load_shared_config,
    load_workspace_config,
)

Resolution = dict[str, str | bool]
DiscoverRoots = Callable[..., list[Path]]
RegisteredInstallations = Callable[..., list[dict[str, str]]]


def engine_root_from_config_value(value: object, start: Path | None = None) -> Path:
    raw = str(value or "").strip()
    native = raw.replace("\\", os.sep).replace("/", os.sep)
    candidate = Path(native).expanduser()
    if not candidate.is_absolute():
        candidate = canonical_workspace_root(start) / candidate
    return candidate.resolve()


def configured_engine_roots_by_association(start: Path | None = None) -> dict[str, str]:
    """Return exact custom-association mappings with workspace precedence."""

    roots: dict[str, str] = {}
    for source in (load_shared_config(), load_workspace_config(start)):
        entries = source.get("engineRootsByAssociation") if isinstance(source, dict) else None
        if not isinstance(entries, dict):
            continue
        for association, root in entries.items():
            key = str(association or "").strip()
            value = str(root or "").strip()
            if key and value:
                roots[key] = value
    return roots


def engine_root_resolution(*, engine_root: Path, source: str, association: str) -> Resolution:
    return {
        "ok": True,
        "engineRoot": str(engine_root.resolve()),
        "source": source,
        "requestedEngineAssociation": association,
        "errorCode": "",
        "error": "",
    }


def unresolved_engine_association(association: str, detail: str) -> Resolution:
    return {
        "ok": False,
        "engineRoot": "",
        "source": "",
        "requestedEngineAssociation": association,
        "errorCode": "ENGINE_ASSOCIATION_UNRESOLVED",
        "error": (
            f"ENGINE_ASSOCIATION_UNRESOLVED: EngineAssociation {association!r} {detail}. "
            "Set engineRoot, UNREAL_ENGINE_ROOT, or an exact engineRootsByAssociation entry."
        ),
    }


def resolve_engine_root_for_association_impl(
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
    discover_roots_fn: DiscoverRoots = discover_engine_roots,
    registered_installations_fn: RegisteredInstallations = registered_engine_installations,
) -> Resolution:
    """Resolve a binding without silently substituting another project engine."""

    association_text = str(association or "").strip()
    host = host_platform or sys.platform
    env = os.environ if environ is None else environ
    injected_discovery = any(
        value is not None
        for value in (
            host_platform,
            environ,
            home,
            launcher_manifest_paths,
            registry_installations,
            install_ini_paths,
        )
    )
    should_read_system_registry = (
        not injected_discovery if read_system_registry is None else bool(read_system_registry)
    )

    def discovered_roots() -> list[Path]:
        if not injected_discovery and read_system_registry is None:
            return discover_roots_fn()
        return discover_roots_fn(
            host_platform=host,
            environ=env,
            home=home,
            launcher_manifest_paths=launcher_manifest_paths,
            registry_installations=registry_installations,
            install_ini_paths=install_ini_paths,
            read_system_registry=should_read_system_registry,
        )

    def registered_installations() -> list[dict[str, str]]:
        return registered_installations_fn(
            host_platform=host,
            environ=env,
            home=home,
            launcher_manifest_paths=launcher_manifest_paths,
            registry_installations=registry_installations,
            install_ini_paths=install_ini_paths,
            read_system_registry=should_read_system_registry,
        )

    def resolve_override(value: object, source: str) -> Resolution | None:
        raw = str(value or "").strip()
        if not raw:
            return None
        root = engine_root_from_config_value(raw, start)
        if is_engine_root(root):
            return engine_root_resolution(
                engine_root=root,
                source=source,
                association=association_text,
            )
        if association_text:
            return unresolved_engine_association(
                association_text,
                f"could not use {source} ({root})",
            )
        return None

    explicit = resolve_override(explicit_engine_root, "argument")
    if explicit is not None:
        return explicit
    environment = resolve_override(env.get("UNREAL_ENGINE_ROOT", ""), "environment")
    environment_association_is_managed = "UNREAL_ENGINE_ROOT_ASSOCIATION" in env
    environment_association = str(env.get("UNREAL_ENGINE_ROOT_ASSOCIATION") or "").strip()
    stale_managed_environment = bool(
        association_text
        and environment_association_is_managed
        and not engine_associations_match(environment_association, association_text)
    )

    def same_resolved_root(left: Resolution | None, right: Resolution | None) -> bool:
        if not left or not right or not left.get("ok") or not right.get("ok"):
            return False
        return canonical_absolute_path_identity(left.get("engineRoot"), host) == (
            canonical_absolute_path_identity(right.get("engineRoot"), host)
        )

    if association_text:
        configured_roots = (
            configured_engine_roots_by_association(start)
            if runtime_config is None
            else runtime_config.get("engineRootsByAssociation")
        )
        mapped_root = (
            configured_roots.get(association_text)
            if isinstance(configured_roots, Mapping)
            else None
        )
        if mapped_root:
            mapped = resolve_override(mapped_root, "config.engineRootsByAssociation")
            if mapped is not None:
                if (
                    not stale_managed_environment
                    and environment is not None
                    and same_resolved_root(environment, mapped)
                ):
                    return environment
                return mapped

        requested_folder = engine_association_folder(association_text)
        if requested_folder and runtime_config is not None:
            configured_default = resolve_override(
                runtime_config.get("defaultEngineRoot"),
                "config.defaultEngineRoot",
            )
            if (
                configured_default is not None
                and configured_default.get("ok")
                and engine_root_matches_numeric_association(
                    Path(str(configured_default.get("engineRoot") or "")),
                    association_text,
                )
            ):
                return configured_default
        registered_matches: dict[str, dict[str, str]] = {}
        for registration in registered_installations():
            if not engine_associations_match(registration.get("association"), association_text):
                continue
            registered_root = Path(registration["engineRoot"])
            if requested_folder and not engine_root_matches_numeric_association(
                registered_root,
                association_text,
            ):
                continue
            key = canonical_absolute_path_identity(registered_root, host)
            registered_matches.setdefault(key, registration)
        if len(registered_matches) > 1:
            return unresolved_engine_association(
                association_text,
                "has multiple conflicting registered engine roots",
            )
        if registered_matches:
            registration = next(iter(registered_matches.values()))
            registered = engine_root_resolution(
                engine_root=Path(registration["engineRoot"]),
                source=f"registered.{registration['source']}",
                association=association_text,
            )
            if (
                not stale_managed_environment
                and environment is not None
                and same_resolved_root(environment, registered)
            ):
                return environment
            return registered

        if environment is not None and not stale_managed_environment:
            if not environment.get("ok"):
                return environment
            if engine_root_matches_numeric_association(
                Path(str(environment.get("engineRoot") or "")),
                association_text,
            ):
                return environment

        if requested_folder:
            requested_identity = filesystem_path_identity(
                requested_folder,
                host,
                strip_project_uri=False,
            )
            for candidate in discovered_roots():
                if filesystem_path_identity(
                    candidate.name,
                    host,
                    strip_project_uri=False,
                ) == requested_identity:
                    return engine_root_resolution(
                        engine_root=candidate,
                        source="EngineAssociation",
                        association=association_text,
                    )
            return unresolved_engine_association(
                association_text,
                f"does not have an installed {requested_folder} engine",
            )
        return unresolved_engine_association(
            association_text,
            "is a custom/source-build identifier without an exact mapping",
        )

    if environment is not None:
        return environment
    configured_defaults = (
        (("runtime.defaultEngineRoot", runtime_config.get("defaultEngineRoot")),)
        if runtime_config is not None
        else (
            ("config.defaultEngineRoot", load_workspace_config(start).get("defaultEngineRoot")),
            ("shared.defaultEngineRoot", load_shared_config().get("defaultEngineRoot")),
        )
    )
    for source, value in configured_defaults:
        resolved = resolve_override(value, source)
        if resolved is not None:
            return resolved
    for candidate in discovered_roots():
        return engine_root_resolution(
            engine_root=candidate,
            source="latest-installed",
            association="",
        )
    return {
        "ok": False,
        "engineRoot": "",
        "source": "",
        "requestedEngineAssociation": "",
        "errorCode": "ENGINE_ROOT_UNRESOLVED",
        "error": "Could not resolve an Unreal Engine installation.",
    }
