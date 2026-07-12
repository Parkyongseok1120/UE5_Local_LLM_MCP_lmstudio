from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from mcp_tool_compact import compact_structured_payload  # noqa: E402


def test_compact_structured_payload_stays_valid_json() -> None:
    payload = {
        "ok": True,
        "results": [{"text": "x" * 5000, "path": f"/p/{idx}"} for idx in range(200)],
        "notes": "y" * 9000,
    }
    compact = compact_structured_payload(payload, max_bytes=8000)
    serialized = json.dumps(compact, ensure_ascii=False)
    assert len(serialized) <= 8000
    assert compact.get("error") != "structuredContent could not be serialized"
    assert compact.get("_structuredTruncated") is True
