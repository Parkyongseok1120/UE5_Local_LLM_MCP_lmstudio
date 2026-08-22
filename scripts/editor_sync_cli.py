#!/usr/bin/env python
"""CLI adapter for the Direct Editor metadata sync façade."""

from __future__ import annotations

import argparse
import json


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Sync exact-project Editor metadata exports into a RAG index."
    )
    parser.add_argument("--export-dir", default="")
    parser.add_argument("--index-dir", default="")
    parser.add_argument("--project-name", default="")
    parser.add_argument("--project", default="", help="Exact .uproject path.")
    parser.add_argument("--no-rebuild", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument(
        "--auto-export",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Explicitly authorize an Unreal Editor export when synchronization needs it.",
    )
    parser.add_argument("--content-path", default="")
    parser.add_argument("--scope", default="")
    parser.add_argument("--mode", default="auto", choices=["auto", "headless", "request"])
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Explicitly authorize Editor export, ingest, and rebuild.",
    )
    return parser


def main() -> int:
    from sync_editor_metadata import refresh_editor_metadata, sync_editor_metadata

    args = _parser().parse_args()
    common = {
        "export_dir": args.export_dir or None,
        "index_dir": args.index_dir or None,
        "project_name": args.project_name or None,
        "project_file": args.project or None,
        "rebuild_index": not args.no_rebuild,
        "content_path": args.content_path or None,
        "export_scope": args.scope or None,
        "export_mode": args.mode,
    }
    if args.refresh:
        payload = refresh_editor_metadata(**common, force=args.force)
    else:
        payload = sync_editor_metadata(
            **common,
            force_ingest=args.force,
            auto_export=args.auto_export,
        )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("ok") else 1


__all__ = ["main"]
