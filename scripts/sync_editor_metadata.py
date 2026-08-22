#!/usr/bin/env python
"""Compatibility façade for exact-project Direct Editor metadata sync."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from editor_sync_context import EditorSyncContextError, resolve_editor_sync_context
from editor_sync_coordinator import sync_editor_context


def sync_editor_metadata(
    *,
    workspace: Path | None = None,
    export_dir: str | Path | None = None,
    index_dir: str | Path | None = None,
    project_name: str | None = None,
    project_file: str | Path | None = None,
    rebuild_index: bool = True,
    force_ingest: bool = False,
    auto_export: bool = False,
    force_export: bool = False,
    content_path: str | None = None,
    export_scope: str | None = None,
    export_mode: str = "auto",
) -> dict[str, Any]:
    try:
        context = resolve_editor_sync_context(
            workspace=workspace,
            export_dir=export_dir,
            index_dir=index_dir,
            project_name=project_name,
            project_file=project_file,
        )
    except EditorSyncContextError as exc:
        return exc.as_payload()
    return sync_editor_context(
        context,
        rebuild_index=rebuild_index,
        force_ingest=force_ingest,
        auto_export=auto_export,
        force_export=force_export,
        content_path=content_path,
        export_scope=export_scope,
        export_mode=export_mode,
    )


def refresh_editor_metadata(
    *,
    workspace: Path | None = None,
    export_dir: str | Path | None = None,
    index_dir: str | Path | None = None,
    project_name: str | None = None,
    project_file: str | Path | None = None,
    rebuild_index: bool = True,
    content_path: str | None = None,
    export_scope: str | None = None,
    export_mode: str = "auto",
    force: bool = False,
) -> dict[str, Any]:
    """Explicitly authorize Editor export before exact-project ingest."""

    return sync_editor_metadata(
        workspace=workspace,
        export_dir=export_dir,
        index_dir=index_dir,
        project_name=project_name,
        project_file=project_file,
        rebuild_index=rebuild_index,
        force_ingest=force,
        auto_export=True,
        force_export=True,
        content_path=content_path,
        export_scope=export_scope,
        export_mode=export_mode,
    )


def main() -> int:
    from editor_sync_cli import main as cli_main

    return cli_main()


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["refresh_editor_metadata", "sync_editor_metadata"]
