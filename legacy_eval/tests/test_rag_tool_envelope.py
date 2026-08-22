from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from unreal_rag_mcp import structured_payload_is_error  # noqa: E402


def test_structured_payload_is_error_from_ok_false() -> None:
    assert structured_payload_is_error({"ok": False, "error": "Unknown task: x"}) is True


def test_structured_payload_is_error_from_ok_true() -> None:
    assert structured_payload_is_error({"ok": True, "status": "running"}) is False


def test_structured_payload_is_error_explicit_is_error() -> None:
    assert structured_payload_is_error({"ok": True, "isError": True}) is True
