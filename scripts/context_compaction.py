"""Transparent context-budget helpers for the optional local wrapper.

The checkpoint contains only conversation facts needed after old messages are
removed.  It never selects a model/tool, owns a route, or carries an executable
next-action directive.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any


SCHEMA_VERSION = 1
DEFAULTS: dict[str, int] = {
    "soft_remaining_tokens": 14_000,
    "hard_remaining_tokens": 8_000,
    "max_output_reserve": 4_096,
    "safety_margin_tokens": 1_024,
    "normal_tool_result_reserve": 3_000,
    "build_tool_result_reserve": 8_000,
    "recent_messages": 8,
}


@dataclass(frozen=True)
class BudgetDecision:
    action: str
    context_length: int
    input_tokens: int
    reserved_tokens: int
    remaining_tokens: int


def estimate_tokens(text: str) -> int:
    return max(1, (len(text or "") + 3) // 4)


def budget_decision(
    *,
    context_length: int,
    input_tokens: int,
    next_tool_name: str = "",
    tool_schema_tokens: int = 0,
    config: dict[str, Any] | None = None,
) -> BudgetDecision:
    cfg = {**DEFAULTS, **(config or {})}
    tool_reserve = (
        int(cfg["build_tool_result_reserve"])
        if any(word in (next_tool_name or "").lower() for word in ("build", "compile"))
        else int(cfg["normal_tool_result_reserve"])
    )
    reserved = (
        int(cfg["max_output_reserve"])
        + int(cfg["safety_margin_tokens"])
        + int(tool_schema_tokens)
        + tool_reserve
    )
    remaining = int(context_length) - int(input_tokens) - reserved
    action = "normal"
    if remaining < int(cfg["hard_remaining_tokens"]):
        action = "hard_compact"
    elif remaining < int(cfg["soft_remaining_tokens"]):
        action = "soft_compact"
    return BudgetDecision(action, int(context_length), int(input_tokens), reserved, remaining)


def _parse_json(content: str) -> list[dict[str, Any]]:
    try:
        value = json.loads(content)
        return [value] if isinstance(value, dict) else []
    except Exception:
        return []


def build_checkpoint(messages: list[dict[str, Any]], previous: dict[str, Any] | None = None) -> dict[str, Any]:
    prior = previous or {}
    touched: list[str] = list(prior.get("modifiedFiles") or [])
    diagnostics: list[str] = list(prior.get("diagnostics") or [])
    objective = str(prior.get("objective") or "")
    latest_user_message = str(prior.get("latestUserMessage") or "")
    unresolved_questions: list[str] = list(prior.get("unresolvedQuestions") or [])
    signature_contracts: list[dict[str, Any]] = list(prior.get("exactSignatureContracts") or [])
    factual_tool_results: list[dict[str, Any]] = list(prior.get("factualToolResults") or [])
    for message in messages:
        content = str(message.get("content") or "")
        if not objective and message.get("role") == "user" and content.strip():
            objective = content.strip()[:1200]
        if message.get("role") == "user" and content.strip():
            latest_user_message = content
            unresolved_questions.extend(
                line.strip() for line in content.splitlines() if line.strip().endswith("?")
            )
        for payload in _parse_json(content):
            for key in ("path", "file", "projectRelative", "projectPath"):
                if isinstance(payload.get(key), str):
                    touched.append(payload[key].replace("\\", "/"))
            for key in ("diagnosticCode", "errorCode", "errorKey", "errorSubkind", "firstError"):
                if payload.get(key) is not None:
                    diagnostics.append(f"{key}={payload[key]}")
            contract = payload.get("signatureContract")
            if isinstance(contract, dict):
                signature_contracts.append(contract)
            if message.get("role") == "tool":
                factual_tool_results.append(
                    {
                        key: payload[key]
                        for key in (
                            "ok", "errorCode", "message", "path", "file", "sha256",
                            "size", "startLine", "endLine", "buildOutcome", "exitCode",
                        )
                        if key in payload
                    }
                )
    return {
        "schemaVersion": SCHEMA_VERSION,
        "checkpointGeneration": int(prior.get("checkpointGeneration") or 0) + 1,
        "objective": objective,
        "latestUserMessage": latest_user_message,
        "unresolvedQuestions": list(dict.fromkeys(unresolved_questions))[-16:],
        "modifiedFiles": list(dict.fromkeys(touched))[-32:],
        "diagnostics": list(dict.fromkeys(diagnostics))[-32:],
        "exactSignatureContracts": signature_contracts[-16:],
        "factualToolResults": factual_tool_results[-16:],
        "buildState": dict(prior.get("buildState") or {}),
    }


def compact_messages(messages: list[dict[str, Any]], checkpoint: dict[str, Any], recent_messages: int = 12) -> list[dict[str, Any]]:
    if not messages:
        return messages
    pinned_system: list[dict[str, Any]] = []
    latest_user: dict[str, Any] | None = None
    rest: list[dict[str, Any]] = []
    latest_user_index = max(
        (index for index, message in enumerate(messages) if message.get("role") == "user"),
        default=-1,
    )
    for index, message in enumerate(messages):
        if message.get("role") == "system":
            pinned_system.append(message)
        elif index == latest_user_index:
            latest_user = message
        else:
            rest.append(message)
    tail = rest[-max(0, recent_messages):] if recent_messages > 0 else []
    summary = {
        "type": "context_checkpoint",
        "schemaVersion": SCHEMA_VERSION,
        "checkpoint": checkpoint,
        "compactedMessageCount": max(0, len(rest) - len(tail)),
        "instruction": "Background context only. The selected model decides whether any tool call or final answer follows.",
    }
    return [
        *pinned_system,
        {"role": "system", "content": json.dumps(summary, ensure_ascii=False, separators=(",", ":"))},
        *tail,
        *([latest_user] if latest_user is not None else []),
    ]


def session_fingerprint(messages: list[dict[str, Any]]) -> str:
    seed = "\n".join(
        f"{message.get('role')}:{message.get('content', '')}"
        for message in messages
        if message.get("role") in {"system", "user"}
    )
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:32]
