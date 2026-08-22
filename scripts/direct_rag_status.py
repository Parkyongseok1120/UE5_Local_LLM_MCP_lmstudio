#!/usr/bin/env python
"""Print the factual Direct RAG health payload for portable CLI diagnostics."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from types import SimpleNamespace

from direct_rag_index import rag_health
from workspace_paths import find_workspace_root, resolve_index_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Report Direct RAG index and project-binding facts.")
    parser.add_argument("--index", default="", help="Explicit rag.sqlite path.")
    parser.add_argument("--workspace", default="", help="Workspace root used for path resolution.")
    args = parser.parse_args()

    workspace = (
        Path(args.workspace).expanduser().resolve()
        if str(args.workspace).strip()
        else find_workspace_root()
    )
    index = (
        Path(args.index).expanduser().resolve()
        if str(args.index).strip()
        else resolve_index_path(workspace)
    )
    result = rag_health(
        SimpleNamespace(index=index, workspace=workspace, notify=lambda *_args, **_kwargs: None),
        {},
    )
    print(json.dumps(result.payload, ensure_ascii=False, indent=2))
    return 0 if result.payload.get("ok") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
