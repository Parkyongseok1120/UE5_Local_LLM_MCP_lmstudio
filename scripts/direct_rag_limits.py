#!/usr/bin/env python
"""Static response budgets for Direct RAG; independent of model profiles."""

from __future__ import annotations

CODE_DETAIL_ORDER = ("compact", "medium", "large", "full")
CODE_DETAIL_LIMITS = {
    "compact": {
        "assembly_chars": 6_000,
        "row_chars": 2_400,
        "top_k": 6,
        "match_chars": 2_600,
        "max_tool_chars": 10_000,
    },
    "medium": {
        "assembly_chars": 11_000,
        "row_chars": 4_200,
        "top_k": 8,
        "match_chars": 4_500,
        "max_tool_chars": 18_000,
    },
    "large": {
        "assembly_chars": 28_000,
        "row_chars": 7_000,
        "top_k": 12,
        "match_chars": 8_000,
        "max_tool_chars": 40_000,
    },
    "full": {
        "assembly_chars": 56_000,
        "row_chars": 10_000,
        "top_k": 16,
        "match_chars": 12_000,
        "max_tool_chars": 80_000,
    },
}


# Caller-supplied response caps (maxChars) are clamped into this range and
# never exceed the selected detail level's own transport budget.
MIN_MAX_CHARS = 2_000
MAX_MAX_CHARS = max(limits["max_tool_chars"] for limits in CODE_DETAIL_LIMITS.values())


def resolve_detail(value: str | None) -> str:
    normalized = str(value or "compact").strip().casefold()
    return normalized if normalized in CODE_DETAIL_LIMITS else "compact"


def detail_limits(value: str | None) -> dict[str, int]:
    detail = resolve_detail(value)
    return dict(CODE_DETAIL_LIMITS[detail])


def next_detail(value: str | None) -> str | None:
    detail = resolve_detail(value)
    index = CODE_DETAIL_ORDER.index(detail)
    return CODE_DETAIL_ORDER[index + 1] if index + 1 < len(CODE_DETAIL_ORDER) else None


def response_char_limit(detail: str | None, max_chars: object = None, transport_limit: int | None = None) -> int:
    """Effective response cap: detail budget, then transport, then maxChars."""

    limit = int(detail_limits(detail)["max_tool_chars"])
    if transport_limit is not None:
        limit = min(limit, int(transport_limit))
    if isinstance(max_chars, int) and not isinstance(max_chars, bool):
        limit = min(limit, max(MIN_MAX_CHARS, min(max_chars, MAX_MAX_CHARS)))
    return limit


__all__ = [
    "MAX_MAX_CHARS",
    "MIN_MAX_CHARS",
    "detail_limits",
    "next_detail",
    "resolve_detail",
    "response_char_limit",
]
