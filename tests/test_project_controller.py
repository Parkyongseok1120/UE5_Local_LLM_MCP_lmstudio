from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from project_controller import active_project_readiness  # noqa: E402


def test_project_status_without_active_project() -> None:
    payload = active_project_readiness(ROOT)
    assert "ok" in payload
