#!/usr/bin/env python
"""Canonical build proof parsing shared by Python wrapper and Node MCP."""

from __future__ import annotations

import re
from typing import Any

EXECUTOR_SETUP_PATTERNS = (
    r"Executing up to \d+ processes, one per physical core",
    r"Building \d+ action(?:s)? with \d+ process(?:es)?",
)

COMPILE_ACTION_PATTERN = re.compile(r"\[(\d+)/(\d+)\]\s+Compile\b", re.IGNORECASE)
LINK_ACTION_PATTERN = re.compile(r"\[(\d+)/(\d+)\]\s+Link\b", re.IGNORECASE)
RUN_ACTIONS_PATTERN = re.compile(r"run\s+(\d+)\s+action\(s\)", re.IGNORECASE)
BUILDING_ACTIONS_PATTERN = re.compile(r"Building\s+(\d+)\s+action\(s\)", re.IGNORECASE)
UP_TO_DATE_PATTERN = re.compile(r"Target is up to date|run\s+0\s+action\(s\)", re.IGNORECASE)


def _max_action_total(pattern: re.Pattern[str], text: str) -> int:
    total = 0
    for match in pattern.finditer(text):
        try:
            total = max(total, int(match.group(2)))
        except (TypeError, ValueError):
            continue
    return total


def parse_build_proof(
    ok: bool,
    output: str,
    *,
    log_path: str = "",
) -> dict[str, Any]:
    text = str(output or "")
    compile_line_count = len(COMPILE_ACTION_PATTERN.findall(text))
    link_line_count = len(LINK_ACTION_PATTERN.findall(text))
    compile_action_count = _max_action_total(COMPILE_ACTION_PATTERN, text)
    link_action_count = _max_action_total(LINK_ACTION_PATTERN, text)
    run_match = RUN_ACTIONS_PATTERN.search(text)
    building_match = BUILDING_ACTIONS_PATTERN.search(text)
    declared_total_actions = max(
        int(run_match.group(1)) if run_match else 0,
        int(building_match.group(1)) if building_match else 0,
        compile_action_count,
        link_action_count,
    )
    highest_observed_action_index = max(compile_action_count, link_action_count)
    has_compile_or_link_evidence = compile_line_count > 0 or link_line_count > 0
    executor_setup_seen = any(re.search(pattern, text, re.IGNORECASE) for pattern in EXECUTOR_SETUP_PATTERNS)
    executor_only = executor_setup_seen and not has_compile_or_link_evidence
    target_up_to_date = bool(ok and UP_TO_DATE_PATTERN.search(text))

    if not ok:
        proof_level = "Failed"
    elif has_compile_or_link_evidence:
        proof_level = "Built"
    elif target_up_to_date:
        proof_level = "BuiltStale"
    else:
        proof_level = "BuiltUnverified"

    return {
        "ok": bool(ok),
        "targetUpToDate": target_up_to_date,
        "actionCount": highest_observed_action_index,
        "compileLineCount": compile_line_count,
        "linkLineCount": link_line_count,
        "declaredTotalActions": declared_total_actions,
        "highestObservedActionIndex": highest_observed_action_index,
        "compileActionCount": compile_action_count,
        "linkActionCount": link_action_count,
        "executorOnly": executor_only,
        "proofLevel": proof_level,
        "logPath": log_path,
    }


def proof_level_from_build_output(ok: bool, output: str) -> str:
    return str(parse_build_proof(ok, output).get("proofLevel") or "BuiltUnverified")
