#!/usr/bin/env python
"""Tests for error taxonomy classification."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from error_taxonomy import classify_error_subkind  # noqa: E402


def test_lnk2019():
    _sub, mode = classify_error_subkind("unresolved external symbol MyFunc", "LNK2019")
    assert mode == "link_fix"
