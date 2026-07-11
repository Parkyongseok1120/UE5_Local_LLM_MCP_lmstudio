from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from mermaid_validate import sanitize_report_markdown, validate_mermaid_block  # noqa: E402


def test_invalid_mermaid_degrades_to_ascii() -> None:
    result = validate_mermaid_block("flowchart TD\nA->>B\nclick A callback")
    assert result["safeToRender"] is False
    assert result["asciiFallback"]


def test_sanitize_report_replaces_invalid_fence() -> None:
    text = "# Title\n\n```mermaid\nflowchart TD\nA->>B\nclick A callback\n```\n"
    out = sanitize_report_markdown(text, mode="sanitize")
    assert "```mermaid" not in out["text"]
    assert out["degraded"] is True
