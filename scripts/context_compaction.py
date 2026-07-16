"""Deterministic context-budget and checkpoint helpers shared by local wrappers.

The LM Studio Generator uses the JavaScript equivalent. This module deliberately
keeps control-plane state structured so a natural-language summary cannot reset
build recovery or duplicate-call guards.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any


SCHEMA_VERSION = 1
DEFAULTS: dict[str, int] = {
    "soft_remaining_tokens": 10_000,
    "hard_remaining_tokens": 5_000,
    "max_output_reserve": 4_096,
    "normal_tool_result_reserve": 3_000,
    "build_tool_result_reserve": 8_000,
    "recent_messages": 12,
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
    reserved = int(cfg["max_output_reserve"]) + int(tool_schema_tokens) + tool_reserve
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
    required: dict[str, Any] | None = prior.get("requiredNextTool")
    objective = str(prior.get("objective") or "")
    signature_contracts: list[dict[str, Any]] = list(prior.get("exactSignatureContracts") or [])
    for message in messages:
        content = str(message.get("content") or "")
        if not objective and message.get("role") == "user" and content.strip():
            objective = content.strip()[:1200]
        for payload in _parse_json(content):
            if isinstance(payload.get("requiredNextTool"), str):
                required = {
                    "name": payload["requiredNextTool"],
                    "args": payload.get("requiredNextToolArgs") or {},
                }
            if payload.get("ok") is True and (payload.get("phase") == "complete" or payload.get("buildOutcome") == "succeeded"):
                required = None
            for key in ("path", "file", "projectRelative", "projectPath"):
                if isinstance(payload.get(key), str):
                    touched.append(payload[key].replace("\\", "/"))
            for key in ("diagnosticCode", "errorCode", "errorKey", "errorSubkind", "firstError"):
                if payload.get(key) is not None:
                    diagnostics.append(f"{key}={payload[key]}")
            contract = payload.get("signatureContract")
            if isinstance(contract, dict):
                signature_contracts.append(contract)
    return {
        "schemaVersion": SCHEMA_VERSION,
        "checkpointGeneration": int(prior.get("checkpointGeneration") or 0) + 1,
        "objective": objective,
        "modifiedFiles": list(dict.fromkeys(touched))[-32:],
        "diagnostics": list(dict.fromkeys(diagnostics))[-32:],
        "requiredNextTool": required,
        "exactSignatureContracts": signature_contracts[-16:],
        "mutationGeneration": int(prior.get("mutationGeneration") or 0),
        "buildState": dict(prior.get("buildState") or {}),
        "pendingToolCall": prior.get("pendingToolCall"),
        "completedToolCallIds": list(dict.fromkeys(prior.get("completedToolCallIds") or [])),
    }


def compact_messages(messages: list[dict[str, Any]], checkpoint: dict[str, Any], recent_messages: int = 12) -> list[dict[str, Any]]:
    if not messages:
        return messages
    pinned: list[dict[str, Any]] = []
    rest: list[dict[str, Any]] = []
    first_user = False
    for message in messages:
        if message.get("role") == "system" or (message.get("role") == "user" and not first_user):
            pinned.append(message)
            first_user = first_user or message.get("role") == "user"
        else:
            rest.append(message)
    tail = rest[-max(1, recent_messages):]
    summary = {
        "type": "context_checkpoint",
        "schemaVersion": SCHEMA_VERSION,
        "checkpoint": checkpoint,
        "compactedMessageCount": max(0, len(rest) - len(tail)),
        "instruction": "Control fields are authoritative. Re-read current files and trust latest build feedback.",
    }
    return [*pinned, {"role": "system", "content": json.dumps(summary, ensure_ascii=False, separators=(",", ":"))}, *tail]


def session_fingerprint(messages: list[dict[str, Any]]) -> str:
    seed = "\n".join(
        f"{message.get('role')}:{message.get('content', '')}"
        for message in messages
        if message.get("role") in {"system", "user"}
    )
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:32]
