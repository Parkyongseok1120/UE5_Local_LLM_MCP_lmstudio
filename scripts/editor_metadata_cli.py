#!/usr/bin/env python
"""Command-line adapter for Editor metadata collection."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from editor_metadata_catalog import METADATA_FILES
from editor_metadata_jsonl import parse_export_spec
from editor_metadata_merge import merge_export_into_raw
from workspace_paths import find_workspace_root, resolve_index_dir


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Collect editor metadata exports.")
    parser.add_argument(
        "--export",
        action="append",
        default=[],
        help="path:type e.g. C:/x/bp.jsonl:blueprint",
    )
    parser.add_argument("--project-name", required=True)
    parser.add_argument("--project-root", default="")
    parser.add_argument(
        "--out-dir",
        default="",
        help="Output directory (default: configured RAG data directory).",
    )
    parser.add_argument(
        "--replace-project",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Replace the selected project's complete kind snapshot; disabling this "
            "preserves other identities and upserts matching identities."
        ),
    )
    return parser


def _output_dir(out_dir_arg: str) -> Path:
    workspace = find_workspace_root()
    out_dir = Path(out_dir_arg) if out_dir_arg else resolve_index_dir(workspace)
    if out_dir_arg and not out_dir.is_absolute():
        out_dir = workspace / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir


def output_filename(kind: str) -> str:
    return METADATA_FILES.get(kind, f"raw_{kind}_metadata.jsonl")


def main() -> int:
    args = _parser().parse_args()
    out_dir = _output_dir(args.out_dir)
    totals: dict[str, int] = {}
    for spec in args.export:
        try:
            path, kind = parse_export_spec(spec)
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
        if not path.is_file():
            print(f"SKIP missing: {path}")
            continue
        out_path = out_dir / output_filename(kind)
        ingested, replaced = merge_export_into_raw(
            path,
            kind,
            args.project_name,
            out_path,
            project_root=args.project_root,
            replace_project=args.replace_project,
        )
        totals[kind] = totals.get(kind, 0) + ingested
        print(f"Ingested {ingested} rows ({replaced} replaced) -> {out_path}")
    print(json.dumps(totals, indent=2))
    return 0


__all__ = ["main", "output_filename"]
