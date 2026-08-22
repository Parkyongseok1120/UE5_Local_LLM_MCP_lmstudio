#!/usr/bin/env python
"""Orchestrate a staged, validated SQLite FTS RAG index build."""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import uuid
from pathlib import Path

from rag_build_classification import infer_doc_type, infer_genre, infer_layer
from rag_build_input import (
    JsonlInputError,
    approx_tokens,
    chunk_text,
    read_jsonl,
    resolve_chunk_params,
)
from rag_build_metadata import metadata_fields
from rag_build_outputs import (
    BuildOutputPaths,
    promote_outputs,
    resolved_engine_version,
    write_manifest,
)
from rag_build_schema import create_schema
from rag_build_writer import ChunkIndexWriter
from workspace_config import find_workspace_root
from workspace_index_paths import resolve_index_dir

__all__ = [
    "JsonlInputError",
    "apply_compact_profile_defaults",
    "approx_tokens",
    "build",
    "chunk_text",
    "create_schema",
    "infer_doc_type",
    "infer_genre",
    "infer_layer",
    "metadata_fields",
    "parse_args",
    "read_jsonl",
    "resolve_chunk_params",
    "resolved_engine_version",
]


def build(args: argparse.Namespace) -> None:
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    workspace_root = (
        Path(args.workspace_root).resolve()
        if args.workspace_root
        else find_workspace_root()
    )
    input_paths = [Path(value) for value in args.input]
    generation_id = uuid.uuid4().hex
    paths = BuildOutputPaths.create(out_dir, f"{os.getpid()}-{generation_id}")
    conn: sqlite3.Connection | None = None
    writer: ChunkIndexWriter | None = None
    try:
        conn = sqlite3.connect(paths.staged_sqlite)
        create_schema(conn)
        conn.execute(
            "insert into index_meta(key, value) values ('generation_id', ?)",
            (generation_id,),
        )
        with paths.staged_chunks.open("w", encoding="utf-8") as chunks_file:
            writer = ChunkIndexWriter(
                conn,
                chunks_file,
                workspace_root,
                chunk_tokens=args.chunk_tokens,
                overlap_tokens=args.overlap_tokens,
            )
            for _, _, document in read_jsonl(input_paths):
                writer.add(document)
            writer.finish()
        conn.commit()
        conn.close()
        conn = None
    except BaseException:
        if conn is not None:
            conn.rollback()
            conn.close()
        paths.discard_staged()
        raise

    if writer is None:  # pragma: no cover - construction precedes all input work
        raise RuntimeError("RAG build writer was not initialized")
    write_manifest(
        paths,
        args=args,
        workspace_root=workspace_root,
        generation_id=generation_id,
        input_paths=input_paths,
        total_chunks=writer.total_chunks,
        engine_evidence_chunks=writer.engine_evidence_chunks,
        project_evidence_chunks=writer.project_evidence_chunks,
    )
    promote_outputs(paths, replace=os.replace)

    print(f"done: wrote {writer.total_chunks} chunks")
    print(f"workspace: {workspace_root}")
    print(f"chunks: {paths.chunks}")
    print(f"sqlite: {paths.sqlite}")


def _explicit_option_names(argv: list[str]) -> set[str]:
    return {
        arg.split("=", 1)[0].lstrip("-").replace("-", "_")
        for arg in argv
        if arg.startswith("-")
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    raw_args = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(description="Build SQLite RAG index from JSONL docs.")
    parser.add_argument("--input", nargs="+", required=True)
    parser.add_argument("--out-dir", default="", help="Output directory (default: configured RAG data directory).")
    parser.add_argument("--workspace-root", default="", help="Normalize legacy locators to this workspace root.")
    parser.add_argument("--engine-version", default="", help="Exact engine version provenance for this generation.")
    parser.add_argument("--engine-association", default="", help="Exact project EngineAssociation provenance, when applicable.")
    parser.add_argument(
        "--indexing-tier",
        choices=("lite", "standard", "full"),
        default="",
        help="Indexing tier provenance for this generation.",
    )
    parser.add_argument("--chunk-tokens", type=int, default=900)
    parser.add_argument("--overlap-tokens", type=int, default=120)
    parser.add_argument(
        "--compact-profile-scale",
        type=float,
        default=0.80,
        help="Scale used by --compact-profile when chunk sizes were not explicit.",
    )
    parser.add_argument(
        "--compact-profile",
        action="store_true",
        help="Scale default chunks to 720/96 for context-constrained local models.",
    )
    args = parser.parse_args(raw_args)
    if not args.out_dir:
        args.out_dir = str(resolve_index_dir(args.workspace_root or None))
    args._explicit_args = _explicit_option_names(raw_args)
    return args


def apply_compact_profile_defaults(args: argparse.Namespace) -> None:
    """Scale chunk defaults without overriding explicit caller choices."""
    if not getattr(args, "compact_profile", False):
        return
    explicit = set(getattr(args, "_explicit_args", set()))
    scale = float(getattr(args, "compact_profile_scale", 0.80) or 0.80)
    if "chunk_tokens" not in explicit:
        args.chunk_tokens = max(1, int(args.chunk_tokens * scale))
    if "overlap_tokens" not in explicit:
        args.overlap_tokens = max(0, int(args.overlap_tokens * scale))
    if args.overlap_tokens >= args.chunk_tokens:
        args.overlap_tokens = max(0, args.chunk_tokens // 8)
    print(
        f"[compact-profile] chunk_tokens={args.chunk_tokens} "
        f"overlap_tokens={args.overlap_tokens}"
    )


if __name__ == "__main__":
    _args = parse_args()
    apply_compact_profile_defaults(_args)
    build(_args)
