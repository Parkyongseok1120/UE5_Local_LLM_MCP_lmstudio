#!/usr/bin/env python
"""Programmatic smoke for Phase 0.5 cinematic analysis routing (no LM Studio required)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

from agent_orchestrator import build_agent_plan, classify_task  # noqa: E402

PROMPT = "현재 프로젝트의 시네마틱 시스템 분석"


def main() -> int:
    task = classify_task(PROMPT, "auto")
    if task != "inspect_only":
        print(json.dumps({"ok": False, "error": f"expected inspect_only, got {task}"}))
        return 1

    plan = build_agent_plan(PROMPT, "auto")
    payload = plan.to_dict()
    policy = payload.get("toolPolicy") or []
    tools = [c.get("tool") for c in payload.get("suggestedToolCalls") or []]

    checks = {
        "taskKind": payload.get("taskKind") == "inspect_only",
        "writesAllowedFalse": payload.get("writeGate", {}).get("writesAllowed") is False,
        "searchBeforeRag": "search_files" in policy and "unreal_rag_search" in policy
        and policy.index("search_files") < policy.index("unreal_rag_search"),
        "suggestedSearchFiles": "search_files" in tools,
        "suggestedReadFile": "read_file" in tools,
    }
    ok = all(checks.values())
    print(
        json.dumps(
            {
                "ok": ok,
                "prompt": PROMPT,
                "checks": checks,
                "toolPolicy": policy,
                "suggestedToolCalls": tools,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
