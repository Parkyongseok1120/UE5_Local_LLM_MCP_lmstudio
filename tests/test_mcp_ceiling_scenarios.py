#!/usr/bin/env python
"""MCP ceiling scenario fixture validation."""

from __future__ import annotations

import json
from pathlib import Path

SCENARIOS = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "mcp_ceiling_scenarios.json"


def test_mcp_ceiling_scenarios_load():
    rows = json.loads(SCENARIOS.read_text(encoding="utf-8-sig"))
    assert len(rows) >= 2
    for row in rows:
        assert row.get("id")
        assert row.get("user")
        assert row.get("expect_tool") or row.get("expect_tools")
