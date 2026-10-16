"""Bounded project source inventory shared by collection and freshness readers."""
from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path
from typing import Any
from direct_rag_operation import check_operation
from collect_unreal_projects import ASSET_EXTENSIONS, TEXT_EXTENSIONS, SKIP_DIRS as TEXT_SKIP, has_skip_part as text_skipped
from collect_unreal_project_profile import SKIP_DIRS as PROFILE_SKIP, has_skip_part as profile_skipped
from collect_project_architecture import SOURCE_EXTENSIONS as ARCHITECTURE_EXTENSIONS, SKIP_DIRS as ARCHITECTURE_SKIP, should_skip as architecture_skipped
from collect_unreal_symbols import SOURCE_EXTENSIONS as SYMBOL_EXTENSIONS, SKIP_DIRS as SYMBOL_SKIP

SNAPSHOT_KEY = "project_source_snapshot"
SOURCE_EXTENSIONS = ARCHITECTURE_EXTENSIONS | SYMBOL_EXTENSIONS
# Prune only directories all producers exclude. Config's profile inventory is
# intentionally unfiltered, so its directories are visited within the bound.
SKIP_DIRECTORIES = frozenset(TEXT_SKIP & PROFILE_SKIP & ARCHITECTURE_SKIP & SYMBOL_SKIP)


def _is_input(path: Path, root: Path, has_source: bool) -> bool:
    suffix = path.suffix.lower()
    if suffix in TEXT_EXTENSIONS | ASSET_EXTENSIONS and not text_skipped(path):
        return True
    if (path.name.endswith((".Target.cs", ".Build.cs")) or suffix == ".uplugin") and not profile_skipped(path):
        return True
    if suffix == ".ini" and path.is_relative_to(root / "Config"):
        return True
    in_source = not has_source or path.is_relative_to(root / "Source") or path.is_relative_to(root / "Plugins")
    return in_source and suffix in SOURCE_EXTENSIONS and not architecture_skipped(path)


def source_snapshot(project: Path, *, max_files: int = 20_000, max_seconds: float = 2.0) -> dict[str, Any]:
    """Inventory identity/size/mtime/ctime, including additions and deletions.

    This is filesystem metadata provenance, not a content hash. Missing coverage
    never produces a fresh claim. Symlinked inputs require direct inspection.
    """
    descriptor = project.resolve()
    deadline = time.monotonic() + max_seconds
    entries: list[str] = []
    try:
        paths = [descriptor]
        has_source = (descriptor.parent / "Source").is_dir()
        for root in [descriptor.parent]:
            if not root.exists():
                continue
            def onerror(error: OSError) -> None:
                raise error
            for directory, names, files in os.walk(root, onerror=onerror, followlinks=False):
                check_operation()
                if time.monotonic() >= deadline:
                    return {"status": "unknown", "reason": "snapshot_deadline"}
                base = Path(directory)
                in_config = base.is_relative_to(descriptor.parent / "Config")
                names[:] = sorted(name for name in names if in_config or name not in SKIP_DIRECTORIES)
                if any((base / name).is_symlink() for name in names):
                    return {"status": "unknown", "reason": "snapshot_symlink"}
                for name in sorted(files):
                    check_operation()
                    if time.monotonic() >= deadline:
                        return {"status": "unknown", "reason": "snapshot_deadline"}
                    path = base / name
                    if path != descriptor and _is_input(path, descriptor.parent, has_source):
                        paths.append(path)
                        if len(paths) > max_files:
                            return {"status": "unknown", "reason": "snapshot_file_limit"}
        for path in paths:
            check_operation()
            if time.monotonic() >= deadline:
                return {"status": "unknown", "reason": "snapshot_deadline"}
            if path.is_symlink():
                return {"status": "unknown", "reason": "snapshot_symlink"}
            stat = path.stat()
            relative = path.relative_to(descriptor.parent).as_posix()
            entries.append(f"{relative}\0{stat.st_size}\0{stat.st_mtime_ns}\0{stat.st_ctime_ns}")
        digest = hashlib.sha256("\n".join(sorted(entries)).encode()).hexdigest()
        return {"status": "complete", "fingerprint": "stat-v1:" + digest, "fileCount": len(entries)}
    except (OSError, ValueError) as exc:
        return {"status": "unknown", "reason": "snapshot_io", "error": str(exc)}


__all__ = ["SNAPSHOT_KEY", "source_snapshot"]
