#!/usr/bin/env python
"""Message history compaction helpers for the Unreal compile wrapper."""

from __future__ import annotations

import json
import re
from typing import Any

import token_budget

COMPACT_SUMMARY_PREFIX = "Conversation compact summary:"


def tail_text(text: str, limit: int) -> str:
    if limit <= 0 or len(text) <= limit:
        return text
    return text[-limit:]


def _first_matching_line(text: str, markers: tuple[str, ...]) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        lower = stripped.lower()
        if any(marker in lower for marker in markers):
            return stripped[:220]
    return ""


def _extract_json_payload(text: str) -> dict[str, Any] | None:
    stripped = text.strip()
    if not stripped:
        return None
    try:
        value = json.loads(stripped)
    except Exception:
        match = re.search(r"\{.*\}", stripped, flags=re.DOTALL)
        if not match:
            return None
        try:
            value = json.loads(match.group(0))
        except Exception:
            return None
    return value if isinstance(value, dict) else None


def _extract_paths(value: Any) -> list[str]:
    paths: list[str] = []

    def visit(item: Any) -> None:
        if isinstance(item, dict):
            for key, child in item.items():
                if key.lower() in {"path", "file", "relative_path", "target"} and isinstance(child, str):
                    if re.search(r"\.(h|hpp|cpp|c|cc|cs|ini|json|uproject|uplugin|md|txt)$", child, re.IGNORECASE):
                        paths.append(child.replace("\\", "/"))
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)

    visit(value)
    return list(dict.fromkeys(paths))[:12]


def trim_compact_summary(text: str, max_chars: int) -> str:
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    header = COMPACT_SUMMARY_PREFIX
    note = "\n...[compact summary truncated]\n"
    if max_chars <= len(header) + len(note) + 80:
        return (header + note + text[-max(0, max_chars - len(header) - len(note)):]).strip()
    head_limit = min(max_chars // 3, 700)
    head = text[:head_limit].rstrip()
    tail_limit = max_chars - len(head) - len(note)
    tail = text[-max(0, tail_limit):].lstrip()
    if not head.startswith(header):
        head = header
    return (head + note + tail).strip()


def summarize_compacted_messages(messages: list[dict[str, str]], max_chars: int) -> tuple[str, list[str]]:
    lines: list[str] = [
        COMPACT_SUMMARY_PREFIX,
        f"- Compacted messages: {len(messages)}",
    ]
    carried_summaries: list[str] = []
    facts: list[str] = []
    touched_paths: list[str] = []
    dropped_error_keys: list[str] = []

    for message in messages:
        role = str(message.get("role") or "unknown")
        content = str(message.get("content") or "").strip()
        if not content:
            continue
        if content.startswith(COMPACT_SUMMARY_PREFIX):
            carried_summaries.append(tail_text(content, max_chars // 3).strip())
            continue
        payload = _extract_json_payload(content)
        if payload is not None:
            answer = str(payload.get("answer") or "").strip()
            if answer:
                facts.append(f"{role}: answer={answer[:220]}")
            paths = _extract_paths(payload)
            if paths:
                touched_paths.extend(paths)
                facts.append(f"{role}: edited/touched {', '.join(paths[:6])}")
            continue
        if role == "user":
            request = _first_matching_line(
                content,
                ("user request:", "compile loop attempt", "previous validation", "build feedback", "error", "failed"),
            )
            if request:
                facts.append(f"user: {request}")
            for line in content.splitlines():
                lower = line.lower()
                if "errorkey" in lower or "error subkind" in lower or "c1083" in lower or "fatal error" in lower:
                    dropped_error_keys.append(line.strip()[:180])
        else:
            summary = _first_matching_line(content, ("error", "failed", "validation", "build", "patched", "wrote", "answer"))
            if summary:
                facts.append(f"{role}: {summary}")

    for summary in carried_summaries[-2:]:
        if summary:
            lines.append("- Prior summary:")
            lines.extend(f"  {line}" for line in summary.splitlines()[:12])
    if touched_paths:
        unique_paths = list(dict.fromkeys(touched_paths))[:12]
        lines.append("- Files touched or proposed: " + ", ".join(unique_paths))
    if facts:
        lines.append("- Important prior facts:")
        lines.extend(f"  - {fact}" for fact in facts[-18:])
    lines.append("- Instruction: use this as continuity only; current project state and latest RAG/build feedback remain authoritative.")
    return trim_compact_summary("\n".join(lines), max_chars), list(dict.fromkeys(dropped_error_keys))[:8]


def cap_message_history(
    messages: list[dict[str, str]],
    mode: str = "execute",
    *,
    history_turns: int | None = None,
) -> list[dict[str, str]]:
    if len(messages) <= 1:
        return messages
    budget = token_budget.mode_budget(mode)
    max_messages = int(budget.get("maxHistoryMessages") or 8)
    history_attempts = int(history_turns if history_turns is not None else budget.get("historyAttempts") or 2)
    summary_chars = int(budget.get("historySummaryMaxChars") or 2400)
    keep_tail = min(max(max_messages - 1, 0), history_attempts * 2)
    if keep_tail <= 0:
        keep_tail = 4
    if len(messages) <= 1 + keep_tail:
        return messages
    compacted = messages[1:-keep_tail]
    summary, _ = summarize_compacted_messages(compacted, summary_chars)
    return [messages[0], {"role": "system", "content": summary}] + messages[-keep_tail:]


def prepare_messages_for_attempt(
    messages: list[dict[str, str]],
    mode: str = "execute",
    *,
    attempt: int = 1,
    history_turns: int | None = None,
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    metrics: dict[str, Any] = {
        "compactedMessageCount": 0,
        "summaryCharsBefore": 0,
        "summaryCharsAfter": 0,
        "droppedErrorKeys": [],
    }
    if attempt <= 1 or len(messages) <= 1:
        return messages, metrics
    budget = token_budget.mode_budget(mode)
    session = budget.get("session") if isinstance(budget.get("session"), dict) else {}
    summary_chars = int(budget.get("historySummaryMaxChars") or 1800)
    if bool(session.get("newChatPerSlice", True)):
        metrics["compactedMessageCount"] = max(0, len(messages) - 1)
        metrics["summaryCharsBefore"] = sum(len(str(m.get("content") or "")) for m in messages[1:])
        summary, dropped = summarize_compacted_messages(messages[1:], summary_chars)
        metrics["summaryCharsAfter"] = len(summary)
        metrics["droppedErrorKeys"] = dropped
        return [messages[0], {"role": "system", "content": summary}], metrics
    capped = cap_message_history(messages, mode, history_turns=history_turns)
    metrics["compactedMessageCount"] = max(0, len(messages) - len(capped))
    return capped, metrics


def count_compact_summary_messages(messages: list[dict[str, str]]) -> int:
    return sum(1 for message in messages if str(message.get("content") or "").startswith(COMPACT_SUMMARY_PREFIX))
