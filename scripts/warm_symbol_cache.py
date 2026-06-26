#!/usr/bin/env python
"""Warm symbol lookup disk cache for common agent queries."""

from __future__ import annotations

import json
from pathlib import Path

from rag_semantic import symbol_lookup
from workspace_paths import active_project_names, find_workspace_root

DEFAULT_QUERIES = (
    "UActorComponent",
    "UWorldSubsystem",
    "UGameInstanceSubsystem",
    "UHealthComponent",
    "FGameplayTag",
    "DECLARE_DYNAMIC_MULTICAST_DELEGATE",
    "UEnhancedInputComponent",
    "GENERATED_BODY",
    "Build.cs",
)


def load_eval_queries(workspace: Path) -> list[str]:
    queries: list[str] = []
    for rel in (
        "config/rag_eval_unreal_programming_queries.json",
        "config/rag_eval_prototype_queries.json",
        "config/rag_eval_refactor_queries.json",
    ):
        path = workspace / rel
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            for case in data.get("cases") or []:
                query = str(case.get("query") or "").strip()
                if query:
                    queries.append(query)
        except Exception:
            continue
    return queries


def main() -> int:
    workspace = find_workspace_root()
    index = workspace / "data" / "unreal58" / "rag.sqlite"
    if not index.exists():
        print(f"[FAIL] index missing: {index}")
        return 1

    projects = active_project_names()
    seen: set[str] = set()
    warmed = 0
    for query in [*DEFAULT_QUERIES, *load_eval_queries(workspace)]:
        key = query.lower()
        if key in seen:
            continue
        seen.add(key)
        rows = symbol_lookup(index, query, top_k=8, project=projects)
        warmed += 1
        print(f"[warm] {query!r} -> {len(rows)} rows")

    print(f"[PASS] warmed {warmed} symbol queries (activeProject={projects or 'none'})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
