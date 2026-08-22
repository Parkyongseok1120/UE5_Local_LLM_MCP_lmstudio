#!/usr/bin/env python
"""Static response budgets for Direct RAG; independent of model profiles."""

from __future__ import annotations

CODE_DETAIL_ORDER = ("compact", "medium", "large", "full")
CODE_DETAIL_LIMITS = {
    "compact": (10_000, 3_000, 6, 10_000),
    "medium": (18_000, 5_000, 8, 18_000),
    "large": (40_000, 8_000, 12, 40_000),
    "full": (80_000, 12_000, 16, 80_000),
}


def resolve_detail(value: str | None) -> str:
    normalized = str(value or "compact").strip().casefold()
    return normalized if normalized in CODE_DETAIL_LIMITS else "compact"


def detail_limits(value: str | None) -> dict[str, int]:
    detail = resolve_detail(value)
    assembly, row, top_k, tool = CODE_DETAIL_LIMITS[detail]
    return {
        "assembly_chars": assembly,
        "row_chars": row,
        "top_k": top_k,
        "max_tool_chars": tool,
    }


def next_detail(value: str | None) -> str | None:
    detail = resolve_detail(value)
    index = CODE_DETAIL_ORDER.index(detail)
    return CODE_DETAIL_ORDER[index + 1] if index + 1 < len(CODE_DETAIL_ORDER) else None


__all__ = ["detail_limits", "next_detail", "resolve_detail"]
