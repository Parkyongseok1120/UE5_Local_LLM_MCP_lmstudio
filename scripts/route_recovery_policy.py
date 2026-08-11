"""Shared, declarative route-recovery policy for both MCP runtimes."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any


POLICY_PATH = Path(__file__).resolve().parents[1] / "config" / "task_route_recovery_policy.json"


@lru_cache(maxsize=1)
def load_route_recovery_policy() -> dict[str, Any]:
    policy = json.loads(POLICY_PATH.read_text(encoding="utf-8-sig"))
    if int(policy.get("version") or 0) < 1:
        raise RuntimeError("task route recovery policy version is missing")
    actions = policy.get("defaultActions")
    if not isinstance(actions, dict):
        raise RuntimeError("task route recovery defaultActions must be an object")
    return policy


def recovery_codes(kind: str) -> frozenset[str]:
    values = load_route_recovery_policy().get(kind) or []
    return frozenset(str(item) for item in values if str(item))


def route_recovery_action(error_code: str = "") -> dict[str, Any]:
    policy = load_route_recovery_policy()
    actions = policy.get("defaultActions") or {}
    selected = actions.get(str(error_code or "")) or policy.get("fallbackAction") or {}
    return {
        "action": str(selected.get("action") or "unreal_task_list_active"),
        "isTool": bool(selected.get("isTool")),
    }


def route_recovery_next_action(error_code: str = "") -> str:
    return str(route_recovery_action(error_code)["action"])


def route_recovery_action_is_tool(error_code: str = "") -> bool:
    return bool(route_recovery_action(error_code)["isTool"])
