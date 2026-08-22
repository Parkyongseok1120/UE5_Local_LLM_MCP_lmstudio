#!/usr/bin/env python
"""Create a stable, validated snapshot of project-local Editor exports."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from editor_export_contract import MANIFEST_NAME, completed_export_files


def create_editor_export_snapshot(
    export_dir: Path,
    staging_parent: Path,
    project: Path,
    *,
    expected_scope: str = "",
) -> Path:
    source = export_dir.expanduser().resolve()
    _manifest, exports = completed_export_files(
        source, project, expected_scope=expected_scope
    )
    staging_parent.mkdir(parents=True, exist_ok=True)
    snapshot = Path(
        tempfile.mkdtemp(prefix=".direct-editor-snapshot-", dir=str(staging_parent))
    ).resolve()
    try:
        copied: set[Path] = set()
        for path, _kind in exports:
            if path in copied:
                continue
            copied.add(path)
            before = path.stat()
            destination = snapshot / path.name
            shutil.copy2(path, destination)
            after = path.stat()
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise RuntimeError(f"Editor export changed while snapshotting: {path}")
        shutil.copy2(source / MANIFEST_NAME, snapshot / MANIFEST_NAME)
        completed_export_files(snapshot, project, expected_scope=expected_scope)
        return snapshot
    except BaseException:
        shutil.rmtree(snapshot, ignore_errors=True)
        raise


def discard_editor_export_snapshot(snapshot: Path | None) -> None:
    if snapshot is not None:
        shutil.rmtree(snapshot, ignore_errors=True)


__all__ = ["create_editor_export_snapshot", "discard_editor_export_snapshot"]
