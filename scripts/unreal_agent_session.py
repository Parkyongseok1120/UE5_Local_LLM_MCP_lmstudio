#!/usr/bin/env python
"""Orchestrated Unreal agent session: genre + RAG context + next steps."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from rag_context import assemble_context
from rag_search import SearchOptions, search_hybrid
from resolve_genre_adapters import resolve_genre_adapters
from workspace_paths import load_shared_config

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
from agent_orchestrator import build_agent_plan  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Unreal agent session orchestrator")
    parser.add_argument("--request", required=True)
    parser.add_argument("--mode", default="auto")
    parser.add_argument("--genres", nargs="*", default=[])
    parser.add_argument("--top-k", type=int, default=6)
    parser.add_argument("--hybrid", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--include-matches", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--index", type=Path, default=Path("data/unreal58/rag.sqlite"))
    parser.add_argument("--session-id", default="")
    args = parser.parse_args()

    rag_root = Path(__file__).resolve().parent.parent
    index = args.index if args.index.is_absolute() else rag_root / args.index
    if not index.is_file():
        print(json.dumps({"ok": False, "error": f"index missing: {index}"}, ensure_ascii=False))
        return 2

    config = load_shared_config()
    genres = resolve_genre_adapters(args.request, args.genres or None)
    options = SearchOptions(mode=args.mode, genres=genres, candidate_limit=max(40, args.top_k * 10))
    rows = (
        search_hybrid(index, args.request, args.top_k, options)
        if args.hybrid
        else __import__("rag_search").search(index, args.request, args.top_k, options)
    )
    from rag_delivery import deliver_rag_result

    delivery = deliver_rag_result(
        tool="unreal_agent_session",
        active_project=str(config.get("activeProject") or ""),
        query=args.request,
        mode=args.mode,
        scope="project_preferred",
        detail_level="compact",
        top_k=args.top_k,
        hybrid=args.hybrid,
        index_path=index,
        session_id=args.session_id,
        rows=rows,
    )
    delivered_rows = list(delivery.get("rows") or [])
    context = assemble_context(delivered_rows, args.request, args.mode) if not delivery.get("suppressed") else ""
    plan = build_agent_plan(args.request, args.mode)

    payload = {
        "ok": True,
        "phase": "plan",
        "taskKind": plan.task_kind,
        "editStrategy": plan.edit_strategy,
        "activeProject": config.get("activeProject"),
        "resolvedGenres": genres,
        "mode": args.mode,
        "matchCount": len(delivered_rows),
        "contextPreview": context[:4000],
        "ragDelivery": {
            "fingerprint": delivery.get("fingerprint"),
            "repeat": delivery.get("repeat"),
            "suppressed": delivery.get("suppressed", False),
        },
        "taskPlan": plan.to_dict(),
        "toolPolicy": plan.tool_policy,
        "nextSteps": plan.tool_policy or [
            "unreal_get_active_project (confirm .uproject)",
            "read_file_range/read_file on target Source files (unreal-agent)",
            "replace_in_file minimal patch (unreal-agent; write_file only for brand-new files)",
            "do not use run_javascript/js-code-sandbox/Deno file APIs for project file I/O",
            "build_unreal_project (unreal-agent)",
            "on failure: unreal_rag_search mode=compile_fix with log excerpt",
        ],
    }
    if args.include_matches:
        payload["matches"] = delivered_rows
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
