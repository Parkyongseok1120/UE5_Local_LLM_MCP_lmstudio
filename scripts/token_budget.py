#!/usr/bin/env python
"""Unified token/character budget resolver (Phase 4b + Phase 5 profile scale)."""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from workspace_paths import find_workspace_root

DEFAULT_BUDGET_PATH = "config/token_budget.json"

MODE_DEFAULTS: dict[str, dict[str, Any]] = {
    "plan": {"ragAssemblyChars": 6000, "readFileMaxBytes": 65536, "maxOutputTokens": 6144, "feedbackTailChars": 12000, "historyAttempts": 2, "maxHistoryMessages": 6, "historySummaryMaxChars": 2200},
    "critique": {"ragAssemblyChars": 6000, "readFileMaxBytes": 65536, "maxOutputTokens": 6144, "feedbackTailChars": 12000, "historyAttempts": 2, "maxHistoryMessages": 6, "historySummaryMaxChars": 2200},
    "execute": {"ragAssemblyChars": 10000, "readFileMaxBytes": 65536, "maxOutputTokens": 4096, "feedbackTailChars": 12000, "historyAttempts": 2, "maxHistoryMessages": 8, "historySummaryMaxChars": 2600, "projectSummaryMaxFiles": 30, "projectSummaryMaxChars": 8000},
    "compile_fix": {"ragAssemblyChars": 8000, "readFileMaxBytes": 65536, "maxOutputTokens": 4096, "feedbackTailChars": 12000, "historyAttempts": 2, "maxHistoryMessages": 8, "historySummaryMaxChars": 2800, "compileFixEngineSourceMax": 2, "projectSummaryMaxFiles": 30, "projectSummaryMaxChars": 8000},
    "review": {"ragAssemblyChars": 8000, "readFileMaxBytes": 32768, "maxOutputTokens": 4096, "feedbackTailChars": 8000, "historyAttempts": 1, "maxHistoryMessages": 6, "historySummaryMaxChars": 2000, "pabSummaryMaxChars": 2000},
    "codegen": {"ragAssemblyChars": 10000, "readFileMaxBytes": 65536, "maxOutputTokens": 4096, "feedbackTailChars": 12000, "historyAttempts": 2, "maxHistoryMessages": 8, "historySummaryMaxChars": 2600},
    "api_lookup": {"ragAssemblyChars": 6000, "readFileMaxBytes": 32768, "maxOutputTokens": 2048, "feedbackTailChars": 4000, "historyAttempts": 1, "maxHistoryMessages": 4, "historySummaryMaxChars": 1600},
}

SESSION_DEFAULTS = {
    "newChatPerSlice": True,
    "maxHistoryMessages": 8,
}

ROW_CHARS_DEFAULTS = {
    "plan": 3000,
    "critique": 3000,
    "execute": 5000,
    "compile_fix": 5000,
    "review": 4000,
    "default": 5000,
}


@lru_cache(maxsize=1)
def load_token_budget_file() -> dict[str, Any]:
    root = find_workspace_root()
    path = root / DEFAULT_BUDGET_PATH
    if not path.exists():
        return {"modes": dict(MODE_DEFAULTS), "session": dict(SESSION_DEFAULTS), "maxCharsPerRow": dict(ROW_CHARS_DEFAULTS)}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"modes": dict(MODE_DEFAULTS), "session": dict(SESSION_DEFAULTS), "maxCharsPerRow": dict(ROW_CHARS_DEFAULTS)}


def assembly_budget_scale() -> float:
    try:
        from load_sampling_preset import load_sampling_config, resolve_active_profile

        cfg = load_sampling_config()
        profile = resolve_active_profile(cfg)
        return float(profile.get("assemblyBudgetScale") or 1.0)
    except Exception:
        return 1.0


def mode_budget(mode: str) -> dict[str, Any]:
    data = load_token_budget_file()
    modes = data.get("modes") or {}
    base = dict(MODE_DEFAULTS.get(mode, MODE_DEFAULTS["execute"]))
    if isinstance(modes.get(mode), dict):
        base.update(modes[mode])
    session = dict(SESSION_DEFAULTS)
    if isinstance(data.get("session"), dict):
        session.update(data["session"])
    base["session"] = session
    scale = assembly_budget_scale()
    if "ragAssemblyChars" in base:
        base["ragAssemblyChars"] = int(base["ragAssemblyChars"] * scale)
    return base


def effective_rag_assembly_chars(mode: str) -> int:
    return int(mode_budget(mode).get("ragAssemblyChars") or 12000)


def max_chars_per_row(mode: str) -> int:
    data = load_token_budget_file()
    row_map = data.get("maxCharsPerRow") or ROW_CHARS_DEFAULTS
    base = int(row_map.get(mode) or row_map.get("default") or 5000)
    scale = assembly_budget_scale()
    if scale < 1.0:
        return max(1200, int(base * max(scale, 0.35)))
    if scale > 1.0:
        return min(7000, int(base * min(scale, 1.25)))
    return base


def project_summary_limits(mode: str = "execute") -> tuple[int, int]:
    budget = mode_budget(mode)
    return (
        int(budget.get("projectSummaryMaxFiles") or 30),
        int(budget.get("projectSummaryMaxChars") or 8000),
    )


def feedback_tail_chars(mode: str = "execute") -> int:
    return int(mode_budget(mode).get("feedbackTailChars") or 12000)


def estimate_tokens_from_chars(char_count: int) -> int:
    """Rough chars→tokens for telemetry (English/code heavy)."""
    return max(1, int(char_count / 4))


def chars_to_token_estimate(text: str) -> int:
    return estimate_tokens_from_chars(len(text or ""))
