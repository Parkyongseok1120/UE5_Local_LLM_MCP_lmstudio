#!/usr/bin/env python
"""Load tool orchestration policy from config/tool_orchestration.json."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any


@lru_cache(maxsize=1)
def load_tool_orchestration() -> dict[str, Any]:
    path = Path(__file__).resolve().parent.parent / "config" / "tool_orchestration.json"
    if not path.is_file():
        return {"tasks": {}}
    return json.loads(path.read_text(encoding="utf-8-sig"))


def tool_sequence_for_task(task_kind: str) -> list[str]:
    cfg = load_tool_orchestration()
    task = (cfg.get("tasks") or {}).get(task_kind) or {}
    seq = task.get("sequence") or []
    return [str(s) for s in seq]


def writes_allowed_for_task(task_kind: str) -> bool:
    cfg = load_tool_orchestration()
    task = (cfg.get("tasks") or {}).get(task_kind) or {}
    return bool(task.get("writesAllowed", False))
