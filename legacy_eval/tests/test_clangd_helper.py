"""Archived tests for the unexposed clangd helper."""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

from clangd_helper import document_symbols, header_source_pair  # noqa: E402


def test_header_source_pair_h():
    root = Path(__file__).resolve().parent
    pair = header_source_pair(root, "test_python_direct_rag_server.py")
    assert "source" in pair or "path" in pair


def test_document_symbols_heuristic():
    root = Path(__file__).resolve().parent.parent / "scripts"
    result = document_symbols(root, "direct_rag_server.py")
    assert result.get("ok") is True
    assert result.get("symbols")
