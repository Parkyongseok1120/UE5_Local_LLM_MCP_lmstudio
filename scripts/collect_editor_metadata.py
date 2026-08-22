#!/usr/bin/env python
"""Compatibility facade for focused Editor metadata collection owners."""

from editor_metadata_cli import main
from editor_metadata_jsonl import ingest_export, parse_export_spec
from editor_metadata_merge import merge_export_into_raw
from editor_metadata_projection import (
    ANIMATION_ASSET_SOURCE_MAP,
    SOURCE_MAP,
    UASSET_SOURCES,
    row_to_chunk,
    source_for_row,
)

__all__ = [
    "ANIMATION_ASSET_SOURCE_MAP",
    "SOURCE_MAP",
    "UASSET_SOURCES",
    "ingest_export",
    "main",
    "merge_export_into_raw",
    "parse_export_spec",
    "row_to_chunk",
    "source_for_row",
]


if __name__ == "__main__":
    raise SystemExit(main())
