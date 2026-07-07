#!/usr/bin/env python
"""Manual RAG refresh entry points for MCP and CLI."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Literal

RefreshScope = Literal["project_source", "editor_metadata", "all"]


def refresh_active_project(
    *,
    scope: RefreshScope = "all",
    workspace: Path | None = None,
    project: Path | None = None,
    force: bool = False,
) -> dict[str, Any]:
    from active_project_sync import sync_active_project
    from index_staleness import invalidate_stale_cache
    from on_active_project_changed import ensure_active_project_ready
    from project_context import clear_project_context_cache
    from project_switch_invalidate import clear_wrapper_snapshot_cache
    from workspace_paths import find_workspace_root, resolve_active_project_path

    ws = workspace or find_workspace_root()
    active = project or resolve_active_project_path()
    if not active:
        return {"ok": False, "error": "No activeProject set."}

    payload: dict[str, Any] = {
        "ok": True,
        "scope": scope,
        "project": str(active),
    }

    if scope in {"project_source", "all"}:
        sync_result = sync_active_project(workspace=ws, project=active)
        payload["projectSourceSync"] = sync_result
        if not sync_result.get("ok", True):
            payload["ok"] = False

    if scope in {"editor_metadata", "all"}:
        setup = ensure_active_project_ready(active, force=force, skip_plugin=True)
        payload["editorMetadataSetup"] = setup
        if not setup.get("ok", True):
            payload["ok"] = False

    clear_project_context_cache()
    clear_wrapper_snapshot_cache()
    invalidate_stale_cache()
    payload["cacheInvalidated"] = ["project_context", "wrapper_snapshot_cache", "index_staleness"]
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Refresh active project RAG inputs/index.")
    parser.add_argument(
        "--scope",
        choices=["project_source", "editor_metadata", "all"],
        default="all",
    )
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    payload = refresh_active_project(scope=args.scope, force=args.force)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
