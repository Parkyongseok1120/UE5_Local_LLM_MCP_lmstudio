#!/usr/bin/env python
from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from refactor_plan import _mask_comments_and_strings, scan_symbol_impact  # noqa: E402


def test_comment_masking_removes_line_comment_matches():
    text = "void Foo() {}\n// GhostSymbol call\nint x = 1;"
    masked = _mask_comments_and_strings(text)
    assert "GhostSymbol" not in masked
    assert "void Foo" in masked


def test_scan_skips_comment_only_symbol(tmp_path: Path):
    module = tmp_path / "Source" / "Game" / "Private"
    module.mkdir(parents=True)
    (module / "Example.cpp").write_text(
        "#include \"Example.h\"\nvoid UExample::Run() {}\n// GhostSymbol\n",
        encoding="utf-8",
    )
    (tmp_path / "Game.uproject").write_text('{"Modules":[{"Name":"Game","Type":"Runtime"}]}', encoding="utf-8")
    result = scan_symbol_impact(str(tmp_path), "GhostSymbol", max_files=10)
    assert result.get("ok") is True
    assert result.get("matchCount", len(result.get("matches") or [])) == 0
