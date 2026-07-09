#!/usr/bin/env python
"""Tests for CI-safe Mermaid validation in report rendering."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from render_report import render_report, validate_mermaid_diagrams  # noqa: E402


def test_valid_mermaid_flowchart_passes_validation() -> None:
    report = """# Diagram

```mermaid
flowchart TD
    User["User / Caller"] --> Request["Request API"]
    Request --> Owner["State Owner"]
```
"""

    payload = validate_mermaid_diagrams(report)

    assert payload["ok"] is True
    assert payload["blockCount"] == 1
    assert payload["errorCount"] == 0
    assert payload["blocks"][0]["diagramType"] == "flowchart"


def test_mermaid_validation_reports_common_render_risks() -> None:
    report = """# Broken Diagram

```mermaid
flowchart TD
    end[End]
    A[Process (main)] --> B
    click A callback
```
"""

    payload = validate_mermaid_diagrams(report)
    codes = {
        issue["code"]
        for block in payload["blocks"]
        for issue in block["errors"]
    }

    assert payload["ok"] is False
    assert "reserved_node_id" in codes
    assert "unquoted_special_label" in codes
    assert "forbidden_directive" in codes


def test_render_report_omits_mermaid_metadata_when_irrelevant(tmp_path: Path) -> None:
    payload = render_report("# Plain Report\n\nNo diagram here.", output_path=tmp_path / "plain.md")

    assert payload["ok"] is True
    assert "mermaidValidation" not in payload
    assert Path(payload["outputPath"]).is_file()


def test_render_report_keeps_success_with_invalid_mermaid_metadata(tmp_path: Path) -> None:
    report = """# Report

```mermaid
flowchart TD
    A[Step: One] --> B
```
"""

    payload = render_report(report, output_path=tmp_path / "diagram.md")

    assert payload["ok"] is True
    assert Path(payload["outputPath"]).is_file()
    assert payload["mermaidValidation"]["ok"] is False
    assert payload["mermaidValidation"]["blockCount"] == 1
