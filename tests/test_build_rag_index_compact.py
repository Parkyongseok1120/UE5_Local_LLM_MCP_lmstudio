#!/usr/bin/env python
"""Tests for compact RAG index chunk defaults."""

from __future__ import annotations

import sys
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parents[1]
SCRIPTS = WORKSPACE / "scripts"
sys.path.insert(0, str(SCRIPTS))

from build_rag_index import apply_compact_profile_defaults, parse_args, resolve_chunk_params  # noqa: E402


def _compact_args(*extra: str):
    args = parse_args(["--input", "dummy.jsonl", "--compact-profile", *extra])
    apply_compact_profile_defaults(args)
    return args


def test_compact_profile_scales_default_chunk_params():
    args = _compact_args()

    assert args.chunk_tokens == 720
    assert args.overlap_tokens == 96


def test_compact_profile_respects_explicit_chunk_tokens():
    args = _compact_args("--chunk-tokens", "1000")

    assert args.chunk_tokens == 1000
    assert args.overlap_tokens == 96


def test_compact_profile_respects_explicit_overlap_tokens():
    args = _compact_args("--overlap-tokens", "80")

    assert args.chunk_tokens == 720
    assert args.overlap_tokens == 80


def test_compact_profile_custom_scale():
    args = _compact_args("--compact-profile-scale", "0.75")

    assert args.chunk_tokens == 675
    assert args.overlap_tokens == 90


def test_symbol_chunk_params_stay_symbol_sized():
    chunk_tokens, overlap_tokens = resolve_chunk_params(
        "unreal_symbol",
        {},
        default_chunk_tokens=720,
        default_overlap_tokens=96,
    )

    assert chunk_tokens == 300
    assert overlap_tokens == 60


def test_module_graph_still_skips_text_chunking():
    chunk_tokens, overlap_tokens = resolve_chunk_params(
        "module_graph",
        {},
        default_chunk_tokens=720,
        default_overlap_tokens=96,
    )

    assert chunk_tokens is None
    assert overlap_tokens is None
