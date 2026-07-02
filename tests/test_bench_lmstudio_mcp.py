#!/usr/bin/env python
"""Tests for LM Studio MCP bench safeguards."""

from __future__ import annotations

import sys
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parents[1]
SCRIPTS = WORKSPACE / "scripts"
sys.path.insert(0, str(SCRIPTS))

import bench_lmstudio_mcp as bench  # noqa: E402


def test_default_kpi_is_not_written_for_embedding_model():
    assert bench.should_write_output(
        bench.DEFAULT_BASELINE,
        "text-embedding-nomic-embed-text-v1.5",
    ) is False


def test_explicit_output_can_record_no_chat_diagnostic(tmp_path):
    out = tmp_path / "diagnostic.json"

    assert bench.should_write_output(out, "text-embedding-nomic-embed-text-v1.5") is True
