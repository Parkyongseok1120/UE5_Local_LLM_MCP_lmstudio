from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from apply_patch import apply_patch, validate_patch_item  # noqa: E402


def test_single_line_patch_applies_with_leading_whitespace(tmp_path: Path) -> None:
    target = tmp_path / "Demo.cpp"
    target.write_text("\tOnScoreChanged.Broadcast();\n", encoding="utf-8")
    ok, msg, updated = apply_patch(target, "OnScoreChanged.Broadcast();", "OnScoreChanged.Broadcast(0);")
    assert ok, msg
    assert "Broadcast(0)" in updated


def test_validate_patch_item_reports_nearest_line(tmp_path: Path) -> None:
    target = tmp_path / "Demo.cpp"
    target.write_text("\tOnScoreChanged.Broadcast();\n", encoding="utf-8")
    ok, msg = validate_patch_item(target, "OnHoldoutScoreChanged.Broadcast();", "ignored")
    assert not ok
    assert "nearest" in msg.lower()
