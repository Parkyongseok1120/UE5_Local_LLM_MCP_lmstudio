#!/usr/bin/env python
"""One-shot factual query used by release verification."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from direct_rag_result import to_mcp_tool_result
from direct_rag_runtime import DirectRagRuntime
from direct_rag_search import rag_search
from workspace_paths import find_workspace_root, resolve_index_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe the Direct RAG search path")
    parser.add_argument("--query", default="UActorComponent")
    parser.add_argument("--index", type=Path, default=None)
    parser.add_argument("--top-k", type=int, default=1)
    args = parser.parse_args()
    index = (args.index or resolve_index_path()).expanduser().resolve()
    runtime = DirectRagRuntime(
        index=index,
        workspace=find_workspace_root(),
        _notifier=lambda _message, _level: None,
    )
    result = rag_search(
        runtime,
        {
            "query": args.query,
            "top_k": args.top_k,
            "scope": "engine",
            "detailLevel": "compact",
        },
    )
    payload = to_mcp_tool_result(
        result,
        tool_name="unreal_rag_search",
    )["structuredContent"]
    print(json.dumps(payload, ensure_ascii=False))
    return 0 if payload.get("ok") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
