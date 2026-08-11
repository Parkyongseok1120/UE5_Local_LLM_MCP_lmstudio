from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from route_recovery_policy import (  # noqa: E402
    load_route_recovery_policy,
    recovery_codes,
    route_recovery_action,
)


def test_every_recovery_code_has_one_declared_action() -> None:
    policy = load_route_recovery_policy()
    actions = policy["defaultActions"]
    assert recovery_codes("recoveryActionCodes") == frozenset(actions)
    for error_code in actions:
        recovery = route_recovery_action(error_code)
        assert recovery["action"]
        assert isinstance(recovery["isTool"], bool)


def test_all_declared_tool_actions_exist_in_stable_public_catalog() -> None:
    policy = load_route_recovery_policy()
    manifest = json.loads(
        (ROOT / "config" / "stable_tool_manifest.json").read_text(encoding="utf-8")
    )
    public_tools = set(manifest["ragEssential"]) | set(manifest["agentEssential"])
    tool_actions = {
        str(item["action"])
        for item in policy["defaultActions"].values()
        if item.get("isTool") is True
    }
    tool_actions.add(str(policy["fallbackAction"]["action"]))
    assert tool_actions <= public_tools


def test_auth_mismatch_routes_to_real_tool_without_exposing_a_fake_action() -> None:
    recovery = route_recovery_action("TASK_AUTH_MISMATCH")
    assert recovery == {"action": "unreal_agent_plan", "isTool": True}


def test_unknown_code_falls_back_to_active_task_listing() -> None:
    assert route_recovery_action("SOMETHING_NEW") == {
        "action": "unreal_task_list_active",
        "isTool": True,
    }
