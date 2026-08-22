#!/usr/bin/env python
"""Discover Unreal Engine roots and interpret project association tokens."""

from __future__ import annotations

import json
import os
import re
import sys
from collections.abc import Iterable, Mapping
from pathlib import Path

from portable_path_identity import canonical_absolute_path_identity
from unreal_engine_registration import is_engine_root, registered_engine_installations

NUMERIC_ENGINE_ASSOCIATION_RE = re.compile(
    r"^(?:UE_)?(\d+(?:\.\d+)+)$",
    re.IGNORECASE,
)


def engine_location_candidates(
    host_platform: str | None = None,
    environ: dict[str, str] | None = None,
    home: Path | None = None,
) -> list[Path]:
    host = host_platform or sys.platform
    env = os.environ if environ is None else environ
    user_home = Path.home() if home is None else home
    if host == "win32":
        roots = []
        for env_name in ("ProgramFiles", "ProgramFiles(x86)"):
            value = env.get(env_name, "").strip()
            if value:
                roots.append(Path(value) / "Epic Games")
        return roots
    if host == "darwin":
        return [Path("/Users/Shared/Epic Games"), Path("/Applications/Epic Games")]
    return [
        user_home / "UnrealEngine",
        user_home / "Epic Games",
        Path("/opt/UnrealEngine"),
        Path("/opt/Epic Games"),
    ]


def engine_association_folder(association: object) -> str:
    """Return an installed folder name only for a numeric association."""

    match = NUMERIC_ENGINE_ASSOCIATION_RE.fullmatch(str(association or "").strip())
    return f"UE_{match.group(1)}" if match else ""


def engine_association_version(association: object) -> str:
    match = NUMERIC_ENGINE_ASSOCIATION_RE.fullmatch(str(association or "").strip())
    return match.group(1) if match else ""


def engine_associations_match(left: object, right: object) -> bool:
    """Compare numeric aliases while keeping custom/source identifiers exact."""

    left_text = str(left or "").strip()
    right_text = str(right or "").strip()
    left_version = engine_association_version(left_text)
    right_version = engine_association_version(right_text)
    if left_version or right_version:
        return bool(left_version and right_version and left_version == right_version)
    return bool(left_text and left_text == right_text)


def engine_build_version(engine_root: Path) -> str:
    """Read major.minor strictly from Engine/Build/Build.version."""

    build_version = engine_root / "Engine" / "Build" / "Build.version"
    if build_version.is_file():
        try:
            payload = json.loads(build_version.read_text(encoding="utf-8-sig"))
            major = int(payload.get("MajorVersion"))
            minor = int(payload.get("MinorVersion"))
            return f"{major}.{minor}"
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            pass
    return ""


def engine_root_numeric_version(engine_root: Path) -> str:
    """Read an engine's major.minor, using its folder only as discovery fallback."""

    build_version = engine_build_version(engine_root)
    if build_version:
        return build_version
    match = re.search(
        r"UE[_ -]?(\d+(?:\.\d+)*)",
        engine_root.name,
        flags=re.IGNORECASE,
    )
    return match.group(1) if match else ""


def engine_root_matches_numeric_association(
    engine_root: Path,
    association: object,
) -> bool:
    requested = engine_association_version(association)
    actual = engine_root_numeric_version(engine_root)
    return not requested or bool(actual and actual == requested)


def engine_sort_key(path: Path) -> tuple[tuple[int, ...], str]:
    version_text = engine_root_numeric_version(path)
    version = tuple(int(part) for part in version_text.split(".")) if version_text else ()
    return version, path.name.casefold()


def discover_engine_roots_impl(
    host_platform: str | None = None,
    environ: dict[str, str] | None = None,
    home: Path | None = None,
    *,
    launcher_manifest_paths: Iterable[Path] | None = None,
    registry_installations: Mapping[str, object] | None = None,
    install_ini_paths: Iterable[Path] | None = None,
    read_system_registry: bool | None = None,
) -> list[Path]:
    candidates: list[Path] = []
    seen: set[str] = set()
    for registration in registered_engine_installations(
        host_platform,
        environ,
        home,
        launcher_manifest_paths=launcher_manifest_paths,
        registry_installations=registry_installations,
        install_ini_paths=install_ini_paths,
        read_system_registry=read_system_registry,
    ):
        root = Path(registration["engineRoot"])
        key = canonical_absolute_path_identity(root, host_platform)
        if key and key not in seen:
            seen.add(key)
            candidates.append(root)
    for location in engine_location_candidates(host_platform, environ, home):
        if not location.is_dir():
            continue
        roots = [location] if is_engine_root(location) else []
        try:
            # The Engine layout, not a fixed UE minor range, is the contract.
            roots.extend(path for path in location.glob("UE_*") if is_engine_root(path))
        except OSError:
            continue
        for root in roots:
            resolved = root.resolve()
            key = canonical_absolute_path_identity(resolved, host_platform)
            if key not in seen:
                seen.add(key)
                candidates.append(resolved)
    candidates.sort(key=engine_sort_key, reverse=True)
    return candidates


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
    """Return validated Unreal roots in newest-first order for the host."""

    return discover_engine_roots_impl(
        host_platform,
        environ,
        home,
        launcher_manifest_paths=launcher_manifest_paths,
        registry_installations=registry_installations,
        install_ini_paths=install_ini_paths,
        read_system_registry=read_system_registry,
    )
