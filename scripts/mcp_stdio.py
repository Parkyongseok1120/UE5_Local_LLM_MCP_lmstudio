"""UTF-8 safe stdio for MCP servers on Windows (cp949 default breaks tool JSON)."""

from __future__ import annotations

import json
import sys
from typing import Any, TextIO


def configure_stdio_utf8() -> None:
    """Best-effort UTF-8 for text streams; binary writes remain the primary path."""
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    if hasattr(sys.stderr, "reconfigure"):
        try:
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def write_utf8_line(stream: TextIO, text: str) -> None:
    """Write one line as UTF-8 bytes so cp949 consoles never raise UnicodeEncodeError."""
    data = (text + "\n").encode("utf-8", errors="replace")
    buffer = getattr(stream, "buffer", None)
    if buffer is not None:
        buffer.write(data)
        buffer.flush()
        return
    stream.write(text + "\n")
    stream.flush()


def write_json_line(stream: TextIO, payload: dict[str, Any]) -> None:
    write_utf8_line(
        stream,
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
    )
