#!/usr/bin/env python
"""Dedicated default entry point for task-free Unreal RAG capabilities."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from direct_rag_server import DirectRagServer
from direct_rag_startup_recovery import recover_startup_refreshes
from mcp_stdio import configure_stdio_utf8, write_utf8_line
from workspace_paths import resolve_index_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Direct Unreal RAG MCP server")
    parser.add_argument(
        "--index",
        default="",
        help="Optional explicit rag.sqlite path; defaults to workspace configuration.",
    )
    arguments = parser.parse_args()
    index = (
        Path(arguments.index).expanduser().resolve()
        if str(arguments.index).strip()
        else resolve_index_path()
    )
    configure_stdio_utf8()
    if index.name.casefold() == "rag.sqlite":
        for recovery in recover_startup_refreshes(index):
            if recovery.get("reason") in {"refresh_busy", "recovery_failed"}:
                write_utf8_line(
                    sys.stderr,
                    "Direct RAG startup recovery: "
                    f"{recovery.get('indexDir')}: {recovery.get('error')}",
                )
    DirectRagServer(index).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
