#!/usr/bin/env python
"""Benchmark LM Studio MCP chat tool-call reliability."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from preflight_lmstudio import (  # noqa: E402
    AUTO_MODEL_ALIASES,
    extract_assistant_text,
    is_embedding_model,
    resolve_lmstudio_model,
)

DEFAULT_BASELINE = ROOT / "data" / "baseline" / "mcp-tool-call-kpi.json"

TOOL_PROBE_SYSTEM = """You are an Unreal MCP agent. Rules:
- Call exactly one MCP tool before answering.
- First turn must call unreal_get_active_project (or the tool named in the user message).
- Do not answer from memory.
- The functions are attached as API tools. Use the function-calling API only.
- Never write tool calls as plain text, JSON, XML, markdown, or <tool_call> tags.
- When calling a tool, assistant message content must be empty. Do not narrate why you are calling it.
- After the tool returns, visible reply: one short English sentence only; no thinking process text.
"""

SCENARIOS = (
    {
        "id": "get_active_project",
        "user": "Call unreal_get_active_project now. Do not answer until the tool returns.",
        "expect_tool": "unreal_get_active_project",
    },
    {
        "id": "agent_plan_bootstrap",
        "user": "Call unreal_agent_plan with mode=compile_fix and request='missing generated.h in header'. One tool only.",
        "expect_tool": "unreal_agent_plan",
    },
)


def fetch_v0_models(base_url: str, timeout: float = 5.0) -> list[dict[str, Any]]:
    base = base_url.rstrip("/").removesuffix("/v1")
    req = Request(f"{base}/api/v0/models", method="GET")
    with urlopen(req, timeout=timeout) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    models = payload.get("models") or payload.get("data") or []
    return models if isinstance(models, list) else []


def resolve_loaded_chat_model(base_url: str, requested: str = "", timeout: float = 5.0) -> str:
    if requested and requested not in AUTO_MODEL_ALIASES:
        return requested
    try:
        models = fetch_v0_models(base_url, timeout=timeout)
    except (URLError, OSError, json.JSONDecodeError):
        return resolve_lmstudio_model(base_url if base_url.endswith("/v1") else f"{base_url.rstrip('/')}/v1", requested, timeout)
    loaded = [
        str(row.get("id") or row.get("path") or "")
        for row in models
        if str(row.get("state") or "").lower() == "loaded"
    ]
    for model_id in loaded:
        if model_id and not is_embedding_model(model_id):
            return model_id
    return resolve_lmstudio_model(
        base_url if base_url.endswith("/v1") else f"{base_url.rstrip('/')}/v1",
        requested,
        timeout,
    )


def chat_completion(
    base_url: str,
    model: str,
    messages: list[dict[str, str]],
    *,
    tools: list[dict[str, Any]] | None = None,
    timeout: float = 120.0,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": 0.1,
        "max_tokens": 512,
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    req = Request(
        f"{base_url.rstrip('/')}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def probe_tools_schema() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": "unreal_get_active_project",
                "description": "Return the active Unreal project root and uproject path.",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "unreal_agent_plan",
                "description": "Build an agent plan for the given request and mode.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "request": {"type": "string"},
                        "mode": {"type": "string"},
                    },
                    "required": ["request"],
                },
            },
        },
    ]


def thinking_leak_rate(text: str) -> bool:
    lowered = text.lower()
    markers = (
        "here's a thinking process",
        "here is a thinking process",
        "thinking process:",
        "let me think step",
        "the user wants me",
        "i need to make",
        "i should do",
        "let me make this function call",
        "they explicitly want",
    )
    return any(marker in lowered for marker in markers)


def run_scenario(base_url: str, model: str, scenario: dict[str, Any]) -> dict[str, Any]:
    messages = [
        {"role": "system", "content": TOOL_PROBE_SYSTEM},
        {"role": "user", "content": scenario["user"]},
    ]
    try:
        response = chat_completion(base_url, model, messages, tools=probe_tools_schema())
    except Exception as exc:
        return {
            "id": scenario["id"],
            "pass": False,
            "error": str(exc),
            "toolCalls": 0,
            "thinkingLeak": False,
        }
    choice = (response.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    tool_calls = message.get("tool_calls") or []
    visible = extract_assistant_text(message)
    names = [
        str((call.get("function") or {}).get("name") or "")
        for call in tool_calls
        if isinstance(call, dict)
    ]
    expected = scenario.get("expect_tool", "")
    ok = bool(tool_calls) and (not expected or expected in names)
    content_with_tool_call = bool(tool_calls) and bool(visible.strip())
    return {
        "id": scenario["id"],
        "pass": ok,
        "toolCalls": len(tool_calls),
        "toolNames": names,
        "thinkingLeak": thinking_leak_rate(visible) or content_with_tool_call,
        "contentWithToolCall": content_with_tool_call,
        "visibleTail": visible[:240],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Bench LM Studio MCP tool-call KPI.")
    parser.add_argument("--url", default="http://localhost:1234/v1")
    parser.add_argument("--model", default="")
    parser.add_argument("--out", type=Path, default=DEFAULT_BASELINE)
    args = parser.parse_args()

    model = resolve_loaded_chat_model(args.url, args.model)
    results = [run_scenario(args.url, model, scenario) for scenario in SCENARIOS]
    passed = sum(1 for row in results if row.get("pass"))
    payload = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "model": model,
        "url": args.url,
        "passCount": passed,
        "total": len(results),
        "passRate": passed / len(results) if results else 0.0,
        "thinkingLeakCount": sum(1 for row in results if row.get("thinkingLeak")),
        "results": results,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if passed >= 1 else 1


if __name__ == "__main__":
    raise SystemExit(main())
