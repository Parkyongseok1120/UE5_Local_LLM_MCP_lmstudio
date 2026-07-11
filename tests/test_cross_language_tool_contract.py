from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from plan_consistency import RAG_ESSENTIAL_TOOLS  # noqa: E402
from tool_policy import exposure_inventory  # noqa: E402


def test_essential_tools_subset_of_inventory() -> None:
    inventory = exposure_inventory()
    essential = set(inventory["essentialProfile"])
    assert essential.issubset(set(RAG_ESSENTIAL_TOOLS))
