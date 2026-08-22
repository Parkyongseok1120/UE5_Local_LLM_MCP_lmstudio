#!/usr/bin/env python
"""Lock and run one Direct active-project source refresh."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from direct_rag_project_refresh import refresh_project_source_generation
from direct_rag_refresh_lock import DirectRagRefreshBusyError, index_refresh_lock
from direct_rag_refresh_transaction import recover_interrupted_refresh
from workspace_paths import find_workspace_root, resolve_active_project_path, resolve_index_dir


def sync_active_project(
    *,
    project: Path | None = None,
    index_dir: Path | None = None,
    workspace: Path | None = None,
    progress: Callable[[str], None] | None = None,
    editor_export_dir: Path | None = None,
) -> dict[str, Any]:
    ws = (workspace or find_workspace_root()).resolve()
    active = project or resolve_active_project_path(ws)
    if active is None or not active.is_file() or active.suffix.casefold() != ".uproject":
        return {"ok": False, "error": "activeProject is not set or missing"}
    active = active.expanduser().resolve()
    idx = (index_dir or resolve_index_dir(ws)).expanduser().resolve()
    from direct_rag_refresh_target import resolve_project_refresh_target

    target_resolution = resolve_project_refresh_target(idx, ws, active)
    if target_resolution.get("ok") is not True:
        return {
            **target_resolution,
            "project": str(active),
            "stageCommitted": False,
        }
    idx = Path(str(target_resolution["indexDir"]))
    try:
        with index_refresh_lock(idx):
            recovery = recover_interrupted_refresh(idx)
            result = refresh_project_source_generation(
                project=active,
                index_dir=idx,
                workspace=ws,
                progress=progress,
                editor_export_dir=editor_export_dir,
            )
            if recovery.get("recovered"):
                result["recovery"] = recovery
            return result
    except DirectRagRefreshBusyError as exc:
        return {
            "ok": False,
            "errorCode": "RAG_REFRESH_BUSY",
            "error": str(exc),
            "project": str(active),
            "indexDir": str(idx),
            "stageCommitted": False,
        }


def main() -> int:
    payload = sync_active_project()
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
