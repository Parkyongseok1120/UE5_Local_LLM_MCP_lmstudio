#!/usr/bin/env python
"""Explicit Direct RAG refresh operations.

Project-source collection is the default. Unreal Editor launch is possible
only for an editor-metadata scope whose caller also authorizes it explicitly.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from direct_rag_refresh_facts import project_refresh_facts

RefreshScope = Literal["project_source", "editor_metadata", "all"]
ProgressFn = Callable[[str], None]
_SCOPES = {"project_source", "editor_metadata", "all"}


def _editor_metadata_refresh(
    *,
    workspace: Path,
    project: Path,
    index_dir: Path,
    force: bool,
    allow_editor_launch: bool,
) -> dict[str, Any]:
    from sync_editor_metadata import refresh_editor_metadata, sync_editor_metadata
    from editor_export_paths import editor_export_dir_for_project
    from workspace_paths import load_shared_config

    config = load_shared_config()
    # Direct refresh always consumes the captured project's own export directory.
    # A shared arbitrary override cannot prove which project produced its JSONL.
    export_dir = editor_export_dir_for_project(project, use_shared_config=False)
    common = {
        "workspace": workspace,
        "export_dir": str(export_dir) if export_dir else None,
        "index_dir": index_dir,
        "project_name": project.stem,
        "project_file": project,
        "rebuild_index": True,
        "content_path": str(config.get("editorExportContentPath") or "") or None,
    }
    if allow_editor_launch:
        return refresh_editor_metadata(**common, force=force)
    return sync_editor_metadata(
        **common,
        force_ingest=force,
        auto_export=False,
        force_export=False,
    )


def refresh_active_project(
    *,
    scope: RefreshScope = "project_source",
    workspace: Path | None = None,
    project: Path | None = None,
    index_path: Path | None = None,
    force: bool = False,
    allow_editor_launch: bool = False,
    progress: ProgressFn | None = None,
) -> dict[str, Any]:
    from direct_rag_freshness import invalidate_freshness_cache
    from project_context import clear_project_context_cache
    from workspace_paths import find_workspace_root, resolve_active_project_path, resolve_index_path

    def _progress(message: str) -> None:
        if progress is not None:
            progress(message)

    if scope not in _SCOPES:
        return project_refresh_facts({
            "ok": False,
            "errorCode": "INVALID_REFRESH_SCOPE",
            "error": f"Unsupported refresh scope: {scope}",
        })
    ws = (workspace or find_workspace_root()).resolve()
    active = project or resolve_active_project_path(ws)
    if not active:
        return project_refresh_facts(
            {"ok": False, "errorCode": "NO_ACTIVE_PROJECT", "error": "No activeProject set."}
        )
    active = active.expanduser().resolve()
    if not active.is_file() or active.suffix.casefold() != ".uproject":
        return project_refresh_facts({
            "ok": False,
            "errorCode": "ACTIVE_PROJECT_INVALID",
            "error": f"activeProject is not an existing .uproject file: {active}",
        })
    selected_index = (index_path or resolve_index_path(ws)).expanduser().resolve()
    if selected_index.name.casefold() != "rag.sqlite":
        return project_refresh_facts({
            "ok": False,
            "errorCode": "UNSUPPORTED_INDEX_FILENAME",
            "error": (
                "Direct refresh requires an index named rag.sqlite; the configured index "
                f"was {selected_index}. Search remains read-only until this path is migrated."
            ),
        })
    from direct_rag_refresh_target import resolve_project_refresh_target

    target_resolution = resolve_project_refresh_target(selected_index, ws, active)
    if target_resolution.get("ok") is not True:
        return project_refresh_facts(target_resolution)
    selected_index = Path(str(target_resolution["index"])).resolve()
    index_dir = selected_index.parent

    payload: dict[str, Any] = {
        "ok": True,
        "scope": scope,
        "project": str(active),
        "indexPath": str(selected_index),
        "engineIndex": {
            key: value
            for key, value in target_resolution.items()
            if key not in {"ok", "index", "indexDir"}
        },
        "editorLaunchAllowed": bool(allow_editor_launch),
    }
    if scope in {"project_source", "all"}:
        from direct_rag_all_refresh import refresh_source_scope

        source_payload = refresh_source_scope(
            scope=scope,
            workspace=ws,
            project=active,
            index_dir=index_dir,
            allow_editor_launch=allow_editor_launch,
            progress=_progress,
        )
        payload.update({key: value for key, value in source_payload.items() if key != "ok"})
        if source_payload.get("ok") is not True:
            payload["ok"] = False

    if scope == "editor_metadata":
        if allow_editor_launch:
            _progress("editor_metadata: syncing exports and index (Unreal Editor launch authorized)")
        else:
            _progress("editor_metadata: ingesting existing exports without launching Unreal Editor")
        editor_result = _editor_metadata_refresh(
            workspace=ws,
            project=active,
            index_dir=index_dir,
            force=force,
            allow_editor_launch=allow_editor_launch,
        )
        payload["editorMetadataSetup"] = editor_result
        if editor_result.get("ok") is not True:
            payload["ok"] = False

    _progress("invalidating project-scoped caches")
    clear_project_context_cache()
    invalidate_freshness_cache()
    payload["cacheInvalidated"] = ["project_context", "direct_rag_freshness"]
    return project_refresh_facts(payload)


def main() -> int:
    from direct_rag_refresh_cli import main as cli_main

    return cli_main()


if __name__ == "__main__":
    raise SystemExit(main())
