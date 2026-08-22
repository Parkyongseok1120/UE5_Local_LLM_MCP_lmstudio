#!/usr/bin/env python
"""Resolve default UnrealBuildTool and Engine source paths from an engine root."""

from __future__ import annotations

import os
import sys
from collections.abc import Callable
from pathlib import Path

ResolveEngineRoot = Callable[[Path | None], Path]


def resolve_ubt_path_with(
    start: Path | None,
    resolve_engine_root_fn: ResolveEngineRoot,
) -> Path:
    env_ubt = os.environ.get("UNREAL_UBT_PATH", "").strip()
    if env_ubt:
        return Path(env_ubt).expanduser().resolve()
    engine_root = resolve_engine_root_fn(start)
    if str(engine_root) in {"", "."}:
        name = "UnrealBuildTool.exe" if sys.platform == "win32" else "UnrealBuildTool.dll"
        return Path(name)
    ubt_root = engine_root / "Engine" / "Binaries" / "DotNET" / "UnrealBuildTool"
    names = (
        ("UnrealBuildTool.exe", "UnrealBuildTool.dll")
        if sys.platform == "win32"
        else ("UnrealBuildTool.dll", "UnrealBuildTool.exe")
    )
    candidates = [ubt_root / name for name in names]
    return next((path for path in candidates if path.is_file()), candidates[0])
