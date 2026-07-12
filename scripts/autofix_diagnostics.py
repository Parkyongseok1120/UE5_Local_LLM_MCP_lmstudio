#!/usr/bin/env python3
"""Structured diagnostics when deterministic autofix steps decline to edit."""

from __future__ import annotations

from typing import Any

_PENDING: list[dict[str, Any]] = []


def clear_autofix_diagnostics() -> None:
    _PENDING.clear()


def record_autofix_diagnostic(
    *,
    step: str,
    code: str,
    reason: str,
    path: str = "",
    finding_codes: list[str] | None = None,
) -> None:
    _PENDING.append(
        {
            "step": step,
            "code": code,
            "reason": reason,
            "path": path,
            "findingCodes": list(finding_codes or []),
        }
    )


def take_autofix_diagnostics() -> list[dict[str, Any]]:
    items = list(_PENDING)
    _PENDING.clear()
    return items
