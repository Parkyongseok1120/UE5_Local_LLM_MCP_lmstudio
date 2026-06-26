#!/usr/bin/env python
"""Run retrieval regression checks against the local RAG index."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from rag_search import SearchOptions, search
from workspace_paths import resolve_index_path

FALLBACK_INDEX = Path("data/unreal58/rag.sqlite")


OPTION_ALIASES = {
    "source": "sources",
    "sources": "sources",
    "project": "projects",
    "projects": "projects",
    "layer": "layers",
    "layers": "layers",
    "doc_type": "doc_types",
    "doc_types": "doc_types",
    "genre": "genres",
    "genres": "genres",
    "extension": "extensions",
    "extensions": "extensions",
    "required_term": "required_terms",
    "required_terms": "required_terms",
}

MATCH_FIELDS = (
    "source",
    "layer",
    "doc_type",
    "project",
    "genre",
    "extension",
    "symbol_name",
    "symbol_kind",
    "module_name",
    "error_code",
    "error_file",
)


def load_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"invalid query set JSON: {path}:{exc.lineno}:{exc.colno} {exc.msg}") from exc


def list_value(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    return [str(value)]


def merge_options(defaults: dict[str, Any], case_options: dict[str, Any]) -> tuple[int, SearchOptions]:
    merged = dict(defaults)
    merged.update(case_options)
    top_k = int(merged.pop("top_k", 5))

    search_kwargs: dict[str, Any] = {
        "mode": str(merged.pop("mode", "auto")),
        "candidate_limit": int(merged.pop("candidate_limit", 120)),
    }
    for key, target in OPTION_ALIASES.items():
        if key in merged:
            search_kwargs[target] = list_value(merged.pop(key))

    unknown = ", ".join(sorted(merged))
    if unknown:
        raise SystemExit(f"unknown query option(s): {unknown}")

    return top_k, SearchOptions(**search_kwargs)


def row_matches(row: dict[str, Any], expected: dict[str, Any]) -> bool:
    title_contains = expected.get("title_contains")
    if title_contains and str(title_contains).lower() not in str(row.get("title") or "").lower():
        return False

    locator_contains = expected.get("locator_contains")
    if locator_contains and str(locator_contains).lower() not in str(row.get("locator") or "").lower():
        return False

    text_contains = expected.get("text_contains")
    if text_contains and str(text_contains).lower() not in str(row.get("text") or "").lower():
        return False

    for field in MATCH_FIELDS:
        if field in expected and str(row.get(field) or "") != str(expected[field]):
            return False

    return True


def summarize_rows(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "  no results"
    lines: list[str] = []
    for rank, row in enumerate(rows, start=1):
        lines.append(
            "  "
            + f"{rank}. {row.get('source')} | {row.get('layer')} | {row.get('genre')} | "
            + f"{row.get('symbol_kind')} {row.get('symbol_name')} | "
            + f"{row.get('module_name')} | {row.get('error_code')} | "
            + f"{row.get('title')} | chunk {row.get('chunk_index')}"
        )
    return "\n".join(lines)


def evaluate_case(index: Path, defaults: dict[str, Any], case: dict[str, Any]) -> tuple[bool, str]:
    case_id = str(case.get("id") or "<unnamed>")
    query = str(case.get("query") or "").strip()
    if not query:
        return False, f"[FAIL] {case_id}: missing query"

    top_k, options = merge_options(defaults, dict(case.get("options") or {}))
    rows = search(index, query, top_k, options)

    failures: list[str] = []
    for expected in case.get("expected") or []:
        max_rank = int(expected.get("max_rank") or top_k)
        matched_rank = None
        for rank, row in enumerate(rows, start=1):
            if rank <= max_rank and row_matches(row, expected):
                matched_rank = rank
                break
        if matched_rank is None:
            failures.append(json.dumps(expected, ensure_ascii=False))

    if failures:
        details = "\n".join(
            [
                f"[FAIL] {case_id}: {len(failures)} expected document(s) missing from top {top_k}",
                "missing:",
                *[f"  {item}" for item in failures],
                "actual:",
                summarize_rows(rows),
            ]
        )
        return False, details

    return True, f"[PASS] {case_id}: {len(case.get('expected') or [])} expected document(s) found"


def main(args: argparse.Namespace) -> int:
    index = Path(args.index) if args.index else default_index_path()
    if not index.exists():
        print(f"index does not exist: {index}", file=sys.stderr)
        return 2

    query_set = load_json(Path(args.query_set))
    defaults = dict(query_set.get("defaults") or {})
    cases = list(query_set.get("cases") or [])
    if not cases:
        print("query set has no cases", file=sys.stderr)
        return 2

    passed = 0
    failed = 0
    for case in cases:
        ok, message = evaluate_case(index, defaults, case)
        print(message)
        if args.verbose and not ok:
            print()
        if ok:
            passed += 1
        else:
            failed += 1

    print(f"\nsummary: {passed} passed, {failed} failed, {len(cases)} total")
    return 1 if failed else 0


def default_index_path() -> Path:
    try:
        return resolve_index_path()
    except Exception:
        return FALLBACK_INDEX


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate RAG retrieval query sets.")
    parser.add_argument("--index", default="", help="Path to rag.sqlite (default: workspace indexPath)")
    parser.add_argument("--query-set", default="config/rag_eval_game_design_queries.json")
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(main(parse_args()))
