from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from mcp_public_contract import sanitize_model_payload


def test_public_payload_recursively_hides_rotating_authorization_fields() -> None:
    payload = sanitize_model_payload(
        {
            "taskAuthorization": {
                "taskSessionId": "task-1",
                "ownerCapability": "owner-1",
                "authToken": "token",
                "planId": "plan",
                "planRevision": 2,
                "activeSliceId": "slice",
                "routeHash": "route",
                "routePhase": "executor",
            },
            "nextActionArgs": {
                "taskAuthorization": {
                    "taskSessionId": "task-1",
                    "ownerCapability": "owner-1",
                    "authToken": "next-token",
                }
            },
            "routeHash": "visible-diagnostic",
        }
    )

    assert payload["taskAuthorization"] == {
        "taskSessionId": "task-1",
        "ownerCapability": "owner-1",
    }
    assert payload["nextActionArgs"]["taskAuthorization"] == {
        "taskSessionId": "task-1",
        "ownerCapability": "owner-1",
    }
    assert payload["routeHash"] == "visible-diagnostic"
