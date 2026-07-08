#!/usr/bin/env python
"""Shared UnrealBuildTool command helpers."""

from __future__ import annotations

import os
from pathlib import Path

KNOWN_UBT_PLATFORMS = {"Win64", "Win32", "Linux", "LinuxArm64", "Mac", "Android", "IOS", "TVOS"}
KNOWN_UBT_CONFIGURATIONS = {"Debug", "DebugGame", "Development", "Test", "Shipping"}
UBT_STABILITY_FLAGS = ["-NoUBA", "-MaxParallelActions=4"]
SOURCE_LIKE_SUFFIXES = {".h", ".hpp", ".cpp", ".c", ".cc", ".cs", ".build.cs", ".target.cs"}


def ubt_subprocess_env(base: dict[str, str] | None = None) -> dict[str, str]:
    """Return subprocess env with .NET roll-forward for UE UBT on newer runtimes."""
    env = dict(os.environ if base is None else base)
    env.setdefault("DOTNET_ROLL_FORWARD", "LatestMajor")
    return env


def sanitize_ubt_target(target: str, *, fallback: str = "HoldoutFixtureEditor") -> tuple[str, str | None]:
    """Reject source-file paths accidentally passed as UBT build targets."""
    raw = str(target or "").strip()
    if not raw:
        return fallback, "empty target replaced with fallback"
    candidate = raw.replace("\\", "/")
    if candidate.endswith(".uproject"):
        return fallback, f"project file used as UBT target: {raw}"
    suffix = Path(candidate).suffix.lower()
    if suffix in SOURCE_LIKE_SUFFIXES or "/source/" in candidate.lower():
        return fallback, f"source path used as UBT target: {raw}"
    return raw, None


def split_ubt_target_spec(
    target: str,
    default_platform: str = "Win64",
    default_configuration: str = "Development",
) -> tuple[str, str, str]:
    """Return target/platform/configuration from either bare or full target specs."""
    parts = str(target or "").strip().split()
    if len(parts) >= 3 and parts[-2] in KNOWN_UBT_PLATFORMS and parts[-1] in KNOWN_UBT_CONFIGURATIONS:
        return " ".join(parts[:-2]), parts[-2], parts[-1]
    return str(target or "").strip(), default_platform, default_configuration


def build_ubt_command(
    ubt_path: Path,
    project_file: Path,
    target: str,
    platform: str = "Win64",
    configuration: str = "Development",
    *,
    stability_flags: list[str] | None = None,
    log_file: Path | None = None,
) -> list[str]:
    """Build a stable UBT command list for local eval/wrapper runs."""
    target_name, resolved_platform, resolved_configuration = split_ubt_target_spec(
        target,
        platform,
        configuration,
    )
    target_name, _ = sanitize_ubt_target(target_name)
    command = [
        str(ubt_path),
        target_name,
        resolved_platform,
        resolved_configuration,
        f"-Project={project_file}",
        "-WaitMutex",
        *(UBT_STABILITY_FLAGS if stability_flags is None else stability_flags),
    ]
    if log_file is not None:
        command.append(f"-Log={log_file}")
    return command
