#!/usr/bin/env python
"""CLI adapter for the Direct RAG refresh orchestrator."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Refresh Direct active-project RAG inputs/index.")
    parser.add_argument(
        "--scope",
        choices=("all", "editor_metadata", "project_source"),
        default="project_source",
    )
    parser.add_argument("--force", action="store_true")
    parser.add_argument(
        "--allow-editor-launch",
        action="store_true",
        help="Explicitly authorize Unreal Editor launch for editor_metadata/all.",
    )
    parser.add_argument("--workspace", default="")
    args = parser.parse_args()
    from rag_refresh import refresh_active_project

    payload = refresh_active_project(
        scope=args.scope,
        force=args.force,
        allow_editor_launch=args.allow_editor_launch,
        workspace=Path(args.workspace).resolve() if args.workspace else None,
    )
    print(json.dumps(payload, ensure_ascii=False))
    return 0 if payload.get("ok") is True else 1


__all__ = ["main"]
