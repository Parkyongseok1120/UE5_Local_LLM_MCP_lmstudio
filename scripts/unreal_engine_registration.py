#!/usr/bin/env python
"""Read and validate Unreal Engine installations registered by the host OS."""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Iterable, Mapping
from pathlib import Path

from portable_path_identity import canonical_absolute_path_identity

MAX_ENGINE_REGISTRATION_BYTES = 2 * 1024 * 1024
WINDOWS_ENGINE_BUILDS_KEY = r"SOFTWARE\Epic Games\Unreal Engine\Builds"


def is_engine_root(path: Path) -> bool:
    engine = path / "Engine"
    return engine.is_dir() and ((engine / "Source").is_dir() or (engine / "Build").is_dir())


def application_settings_dirs(
    host_platform: str | None = None,
    environ: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> list[Path]:
    """Return only Unreal's documented per-host application settings roots."""

    host = host_platform or sys.platform
    env = os.environ if environ is None else environ
    user_home = Path.home() if home is None else home
    candidates: list[Path] = []
    if host == "win32":
        program_data = str(
            env.get("PROGRAMDATA")
            or env.get("ProgramData")
            or env.get("ALLUSERSPROFILE")
            or ""
        ).strip()
        if program_data:
            candidates.append(Path(program_data) / "Epic")
    elif host == "darwin":
        candidates.append(user_home / "Library" / "Application Support" / "Epic")
    else:
        candidates.append(user_home / ".config" / "Epic")

    unique: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        try:
            resolved = candidate.expanduser().resolve()
        except (OSError, RuntimeError):
            continue
        key = canonical_absolute_path_identity(resolved, host)
        if key and key not in seen:
            seen.add(key)
            unique.append(resolved)
    return unique


def default_launcher_manifest_paths(
    host_platform: str | None = None,
    environ: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> list[Path]:
    if (host_platform or sys.platform) != "win32":
        return []
    return [
        root / "UnrealEngineLauncher" / "LauncherInstalled.dat"
        for root in application_settings_dirs(host_platform, environ, home)
    ]


def default_install_ini_paths(
    host_platform: str | None = None,
    environ: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> list[Path]:
    if (host_platform or sys.platform) not in {"darwin", "linux"}:
        return []
    return [
        root / "UnrealEngine" / "Install.ini"
        for root in application_settings_dirs(host_platform, environ, home)
    ]


def read_bounded_registration_text(path: Path) -> str:
    try:
        size = path.stat().st_size
        if size < 0 or size > MAX_ENGINE_REGISTRATION_BYTES:
            return ""
        return path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError):
        return ""


def valid_engine_association_token(value: object) -> str:
    token = str(value or "").strip()
    if not token or len(token) > 256 or token in {".", "..", "(Default)"}:
        return ""
    if any(ord(char) < 32 for char in token):
        return ""
    if any(char in token for char in ("/", "\\", "=", "[", "]")):
        return ""
    return token


def registered_engine_root(value: object) -> Path | None:
    raw = str(value or "").strip()
    if not raw or len(raw) > 32767 or any(char in raw for char in ("\0", "\r", "\n")):
        return None
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in {"'", '"'}:
        raw = raw[1:-1].strip()
    candidate = Path(raw)
    if not candidate.is_absolute():
        return None
    try:
        resolved = candidate.resolve()
    except (OSError, RuntimeError):
        return None
    return resolved if is_engine_root(resolved) else None


def launcher_registration_rows(paths: Iterable[Path]) -> list[tuple[str, object, str]]:
    rows: list[tuple[str, object, str]] = []
    for manifest in paths:
        text = read_bounded_registration_text(Path(manifest))
        if not text:
            continue
        try:
            payload = json.loads(text)
        except (ValueError, json.JSONDecodeError):
            continue
        installations = payload.get("InstallationList") if isinstance(payload, dict) else None
        for item in installations if isinstance(installations, list) else []:
            if isinstance(item, dict):
                rows.append(
                    (
                        str(item.get("AppName") or ""),
                        item.get("InstallLocation"),
                        "launcher-manifest",
                    )
                )
    return rows


def install_ini_registration_rows(paths: Iterable[Path]) -> list[tuple[str, object, str]]:
    rows: list[tuple[str, object, str]] = []
    for config_path in paths:
        text = read_bounded_registration_text(Path(config_path))
        if not text:
            continue
        section = ""
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line or line.startswith((";", "#")):
                continue
            if line.startswith("[") and line.endswith("]"):
                section = line[1:-1].strip()
                continue
            if section != "Installations" or "=" not in line:
                continue
            association, root = line.split("=", 1)
            rows.append((association, root, "install-ini"))
    return rows


def windows_registry_registration_rows(
    injected: Mapping[str, object] | None,
    *,
    read_system_registry: bool,
) -> list[tuple[str, object, str]]:
    if injected is not None:
        return [(key, value, "windows-registry") for key, value in injected.items()]
    if not read_system_registry or sys.platform != "win32":
        return []
    try:
        import winreg

        rows: list[tuple[str, object, str]] = []
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            WINDOWS_ENGINE_BUILDS_KEY,
            0,
            winreg.KEY_READ,
        ) as key:
            for index in range(512):
                try:
                    association, root, value_type = winreg.EnumValue(key, index)
                except OSError:
                    break
                if value_type in {winreg.REG_SZ, winreg.REG_EXPAND_SZ}:
                    rows.append((association, root, "windows-registry"))
        return rows
    except (ImportError, OSError):
        return []


def registered_engine_installations(
    host_platform: str | None = None,
    environ: Mapping[str, str] | None = None,
    home: Path | None = None,
    *,
    launcher_manifest_paths: Iterable[Path] | None = None,
    registry_installations: Mapping[str, object] | None = None,
    install_ini_paths: Iterable[Path] | None = None,
    read_system_registry: bool | None = None,
) -> list[dict[str, str]]:
    """Enumerate bounded, validated OS registrations without modifying them."""

    host = host_platform or sys.platform
    if read_system_registry is None:
        read_system_registry = (
            host_platform is None
            and environ is None
            and home is None
            and registry_installations is None
        )
    rows: list[tuple[str, object, str]] = []
    if host == "win32":
        manifests = (
            default_launcher_manifest_paths(host, environ, home)
            if launcher_manifest_paths is None
            else list(launcher_manifest_paths)
        )
        rows.extend(launcher_registration_rows(manifests))
        rows.extend(
            windows_registry_registration_rows(
                registry_installations,
                read_system_registry=bool(read_system_registry),
            )
        )
    elif host in {"darwin", "linux"}:
        config_paths = (
            default_install_ini_paths(host, environ, home)
            if install_ini_paths is None
            else list(install_ini_paths)
        )
        rows.extend(install_ini_registration_rows(config_paths))

    installations: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for raw_association, raw_root, source in rows:
        association = valid_engine_association_token(raw_association)
        root = registered_engine_root(raw_root)
        if not association or root is None:
            continue
        key = (association, canonical_absolute_path_identity(root, host))
        if key in seen:
            continue
        seen.add(key)
        installations.append(
            {"association": association, "engineRoot": str(root), "source": source}
        )
    installations.sort(
        key=lambda item: (
            item["association"],
            canonical_absolute_path_identity(item["engineRoot"], host),
        )
    )
    return installations
