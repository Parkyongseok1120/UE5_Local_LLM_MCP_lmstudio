from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from render_report import resolve_report_output_path  # noqa: E402


def test_resolve_report_output_path_rejects_outside_workspace(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        resolve_report_output_path(tmp_path, "C:/outside/report.md", format="md")


def test_resolve_report_output_path_uses_reports_root(tmp_path: Path) -> None:
    out = resolve_report_output_path(tmp_path, "note.md", format="md")
    assert out.parent == (tmp_path / "data" / "reports").resolve()
