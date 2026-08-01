from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from mcp_tool_compact import (  # noqa: E402
    compact_agent_plan_payload,
    compact_code_sketch_payload,
    compact_structured_payload,
)


def test_compact_structured_payload_stays_valid_json() -> None:
    payload = {
        "ok": True,
        "results": [{"text": "x" * 5000, "path": f"/p/{idx}"} for idx in range(200)],
        "notes": "y" * 9000,
    }
    compact = compact_structured_payload(payload, max_bytes=8000)
    serialized = json.dumps(compact, ensure_ascii=False)
    assert len(serialized) <= 8000
    assert compact.get("error") != "structuredContent could not be serialized"
    assert compact.get("_structuredTruncated") is True


def test_code_sketch_compaction_preserves_every_known_bad_replacement() -> None:
    payload = {
        "ok": False,
        "verdictSummary": "0 verified, 0 weak, 12 known_bad, 100 unverified",
        "knownBadCount": 12,
        "unverifiedCount": 100,
        "weakCount": 0,
        "results": [
            {
                "symbol": f"bad{index}",
                "verdict": "known_bad",
                "replacement": f"UseReplacement{index}",
                "note": "n" * 500,
            }
            for index in range(12)
        ] + [
            {
                "symbol": f"unknown{index}",
                "verdict": "unverified",
                "note": "u" * 500,
            }
            for index in range(100)
        ],
    }
    compact = compact_code_sketch_payload(payload, max_bytes=2_000)
    bad = [row for row in compact["results"] if row.get("verdict") == "known_bad"]
    assert len(bad) == 12
    assert [row["replacement"] for row in bad] == [
        f"UseReplacement{index}" for index in range(12)
    ]


def test_generic_compaction_routes_sketch_payload_to_safe_compactor() -> None:
    payload = {
        "ok": False,
        "verdictSummary": "known bad",
        "knownBadCount": 8,
        "results": [
            {
                "symbol": f"bad{index}",
                "verdict": "known_bad",
                "replacement": f"replacement{index}",
                "note": "x" * 1_000,
            }
            for index in range(8)
        ],
    }
    compact = compact_structured_payload(payload, max_bytes=2_000)
    assert len(compact["results"]) == 8
    assert all(row.get("replacement") for row in compact["results"])


def test_generic_compaction_never_discards_replacements_for_tiny_budget() -> None:
    payload = {
        "ok": False,
        "verdictSummary": "known bad",
        "knownBadCount": 3,
        "results": [
            {
                "symbol": f"bad{index}",
                "verdict": "known_bad",
                "replacement": f"complete replacement {index}",
            }
            for index in range(3)
        ],
    }

    compact = compact_structured_payload(payload, max_bytes=300)

    assert [row["replacement"] for row in compact["results"]] == [
        f"complete replacement {index}" for index in range(3)
    ]


def test_agent_plan_compaction_preserves_authorization_and_bounds_repeated_request() -> None:
    authorization = {
        "taskSessionId": "session",
        "authToken": "secret",
        "planId": "plan",
        "planRevision": "2",
        "activeSliceId": "slice",
        "routeHash": "route",
        "routePhase": "planner",
    }
    payload = {
        "request": "r" * 20_000,
        "taskKind": "edit",
        "editStrategy": "exact_patch",
        "evidencePlan": {"queries": ["q" * 20_000], "gates": ["gate"]},
        "suggestedToolCalls": [{"tool": "read_file", "args": {"query": "q" * 5_000}}],
        "writeGate": {"writesAllowed": True},
        "toolRoute": {"activeTools": ["read_file"], "pendingGates": ["gate"]},
        "taskAuthorization": authorization,
        "taskAuthorizationRequiredForWrites": True,
        "writeToolAuthorizationArgs": {"taskAuthorization": authorization},
        "authorizationRetryPolicy": {"reuseExistingAuthorization": True},
        "checkpoints": ["c" * 2_000 for _ in range(30)],
        "stopConditions": ["s" * 2_000 for _ in range(30)],
        "retryPolicy": ["p" * 2_000 for _ in range(30)],
        "notes": ["n" * 2_000 for _ in range(30)],
    }
    compact = compact_agent_plan_payload(payload, max_bytes=8_000)
    assert compact["taskAuthorization"] == authorization
    assert compact["writeToolAuthorizationArgs"]["taskAuthorization"] == authorization
    assert len(compact["request"]) < 1_300
    assert len(json.dumps(compact, ensure_ascii=False)) <= 8_000
    assert len(json.dumps(compact, ensure_ascii=False)) < len(json.dumps(payload, ensure_ascii=False))


def test_generic_plan_compaction_never_shrinks_authorization_for_tiny_budget() -> None:
    authorization = {
        "taskSessionId": "session",
        "authToken": "secret-token",
        "planId": "plan",
        "planRevision": "2",
        "activeSliceId": "slice",
        "routeHash": "route",
        "routePhase": "planner",
    }
    payload = {
        "request": "r" * 10_000,
        "taskKind": "edit",
        "writeGate": {"writesAllowed": True},
        "toolRoute": {"activeTools": ["read_file"]},
        "taskAuthorization": authorization,
        "taskAuthorizationRequiredForWrites": True,
        "writeToolAuthorizationArgs": {"taskAuthorization": authorization},
        "authorizationRetryPolicy": {"reuseExistingAuthorization": True},
    }

    compact = compact_structured_payload(payload, max_bytes=200)

    assert compact["taskAuthorization"] == authorization
    assert compact["writeToolAuthorizationArgs"]["taskAuthorization"] == authorization
