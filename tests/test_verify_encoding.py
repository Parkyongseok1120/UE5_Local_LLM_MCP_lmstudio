from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from verify_encoding import TEXT_SUFFIXES, scan_paths  # noqa: E402


def test_text_fixture_suffix_is_part_of_encoding_gate() -> None:
    assert ".txt" in TEXT_SUFFIXES


def test_encoding_gate_rejects_bom_and_invalid_utf8(tmp_path: Path) -> None:
    bom = tmp_path / "bom.txt"
    bom.write_bytes(b"\xef\xbb\xbftext")
    invalid = tmp_path / "invalid.txt"
    invalid.write_bytes(b"\xff\xfe")
    clean = tmp_path / "clean.txt"
    clean.write_text("한글 UTF-8", encoding="utf-8")

    issues = scan_paths([bom, invalid, clean])

    assert any(item.startswith("BOM:") for item in issues)
    assert any(item.startswith("INVALID_UTF8:") for item in issues)
    assert not any(str(clean) in item for item in issues)
