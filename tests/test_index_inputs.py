#!/usr/bin/env python
"""Tests for canonical RAG index input list."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from index_inputs import RAW_INPUT_FILES, existing_input_paths  # noqa: E402


def test_raw_input_files_contains_core_sources():
    assert "raw_guidelines.jsonl" in RAW_INPUT_FILES
    assert "raw_blueprint_metadata.jsonl" in RAW_INPUT_FILES
    assert "raw_material_metadata.jsonl" in RAW_INPUT_FILES
    assert "raw_projects.jsonl" in RAW_INPUT_FILES


def test_existing_input_paths_only_returns_existing_files(tmp_path):
    present = tmp_path / "raw_guidelines.jsonl"
    present.write_text("{}\n", encoding="utf-8")
    missing = tmp_path / "raw_docs.jsonl"

    paths = existing_input_paths(tmp_path)

    assert present in paths
    assert missing not in paths
