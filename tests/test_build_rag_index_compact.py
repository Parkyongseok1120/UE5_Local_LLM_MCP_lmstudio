#!/usr/bin/env python
"""Tests for compact RAG index chunk defaults."""

from __future__ import annotations

import sys
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parents[1]
SCRIPTS = WORKSPACE / "scripts"
sys.path.insert(0, str(SCRIPTS))

from build_rag_index import (  # noqa: E402
    apply_compact_profile_defaults,
    doc_matches_replace_project,
    parse_args,
    replace_project_enabled,
    resolve_chunk_params,
)


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


def test_replace_project_flag_disabled_by_default(monkeypatch):
    monkeypatch.delenv("ENABLE_REPLACE_PROJECT", raising=False)
    assert replace_project_enabled() is False


def test_doc_matches_replace_project_for_symbol_rows():
    assert doc_matches_replace_project(
        "unreal_symbol",
        {"project": "DemoGame", "symbol_name": "UDemoActor"},
        "DemoGame",
    )
    assert not doc_matches_replace_project(
        "unreal_symbol",
        {"project": "OtherGame"},
        "DemoGame",
    )


def test_replace_project_explicit_mismatch_cannot_fall_through_to_path():
    assert not doc_matches_replace_project(
        "unreal_symbol",
        {
            "project": "OtherGame",
            "relative_path": "Projects/DemoGame/Source/DemoGame/DemoActor.cpp",
            "root": "/workspace/Projects/DemoGame",
        },
        "DemoGame",
        host_platform="linux",
    )


def test_replace_project_fallback_requires_an_exact_project_segment():
    assert doc_matches_replace_project(
        "unreal_project_text",
        {"relative_path": "Projects/Game/Source/Game/GameMode.cpp"},
        "Game",
        host_platform="linux",
    )
    assert not doc_matches_replace_project(
        "unreal_project_text",
        {"relative_path": "Projects/GameTools/Source/GameTools/Tool.cpp"},
        "Game",
        host_platform="linux",
    )
    assert not doc_matches_replace_project(
        "unreal_symbol",
        {"root": "/workspace/Projects/GameTools"},
        "Game",
        host_platform="linux",
    )


def test_replace_project_keeps_posix_unicode_names_distinct():
    composed = "Caf\u00e9Game"
    decomposed = "Cafe\u0301Game"

    assert not doc_matches_replace_project(
        "unreal_symbol",
        {"project": decomposed, "root": f"/workspace/{composed}"},
        composed,
        host_platform="linux",
    )
    assert not doc_matches_replace_project(
        "unreal_symbol",
        {"root": f"/workspace/{decomposed}"},
        composed,
        host_platform="linux",
    )


def test_replace_project_folds_ascii_case_only_on_windows():
    assert doc_matches_replace_project(
        "unreal_symbol",
        {"project": "gAmE"},
        "Game",
        host_platform="win32",
    )
    assert doc_matches_replace_project(
        "unreal_symbol",
        {"root": r"C:\Projects\GAME"},
        "Game",
        host_platform="win32",
    )
    assert not doc_matches_replace_project(
        "unreal_symbol",
        {"project": "i\u0307Game"},
        "\u0130Game",
        host_platform="win32",
    )
