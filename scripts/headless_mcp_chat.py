#!/usr/bin/env python3
"""Chat through LM Studio while exposing every configured stdio MCP tool.

The stock ``lms chat`` command does not accept MCP integrations.  This small
headless client bridges ``~/.lmstudio/mcp.json`` servers to LM Studio's
OpenAI-compatible chat endpoint and automatically executes requested tools.
Server-side safety controls (for example ALLOW_WRITE=0) remain authoritative.
"""

from __future__ import annotations

import argparse
import json
import os
import selectors
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

PROTOCOL_VERSION = "2025-11-25"


class McpError(RuntimeError):
    """Raised when an MCP process cannot satisfy a JSON-RPC request."""


@dataclass
class McpProcess:
    label: str
    spec: dict[str, Any]
    timeout: float
    process: subprocess.Popen[str] | None = None
    next_id: int = 1

    def start(self) -> None:
        command = [str(self.spec["command"]), *map(str, self.spec.get("args", []))]
        env = os.environ.copy()
        env.update({str(key): str(value) for key, value in self.spec.get("env", {}).items()})
        self.process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            bufsize=1,
            env=env,
        )
        self.request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "lmstudio-headless-mcp-chat", "version": "1.0"},
            },
        )
        self.notify("notifications/initialized", {})

    def _write(self, payload: dict[str, Any]) -> None:
        if self.process is None or self.process.stdin is None:
            raise McpError(f"{self.label}: MCP process is not running")
        self.process.stdin.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
        self.process.stdin.flush()

    def _read_response(self, request_id: int) -> dict[str, Any]:
        if self.process is None or self.process.stdout is None:
            raise McpError(f"{self.label}: MCP process is not running")
        selector = selectors.DefaultSelector()
        selector.register(self.process.stdout, selectors.EVENT_READ)
        try:
            while True:
                if not selector.select(self.timeout):
                    raise McpError(f"{self.label}: timed out waiting for JSON-RPC response")
                line = self.process.stdout.readline()
                if not line:
                    code = self.process.poll()
                    raise McpError(f"{self.label}: MCP process exited unexpectedly ({code})")
                try:
                    response = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if response.get("id") == request_id:
                    if "error" in response:
                        raise McpError(f"{self.label}: {response['error']}")
                    return response.get("result", {})
        finally:
            selector.close()

    def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        request_id = self.next_id
        self.next_id += 1
        self._write({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        return self._read_response(request_id)

    def notify(self, method: str, params: dict[str, Any]) -> None:
        self._write({"jsonrpc": "2.0", "method": method, "params": params})

    def tools(self) -> list[dict[str, Any]]:
        return list(self.request("tools/list", {}).get("tools", []))

    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return self.request("tools/call", {"name": name, "arguments": arguments})

    def close(self) -> None:
        if self.process is None:
            return
        if self.process.stdin is not None:
            self.process.stdin.close()
        try:
            self.process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()


def post_chat(url: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"LM Studio HTTP {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"LM Studio server is unreachable at {url}: {exc}") from exc


def post_chat_stream(
    url: str,
    payload: dict[str, Any],
    timeout: float,
    emit: Callable[[str], None],
) -> tuple[dict[str, Any], bool]:
    """Consume OpenAI-compatible SSE chunks and rebuild one assistant message."""
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Accept": "text/event-stream", "Content-Type": "application/json"},
        method="POST",
    )
    content_parts: list[str] = []
    tool_calls: dict[int, dict[str, Any]] = {}
    printed = False
    status_active = False
    status_frame = 0
    last_status_update = 0.0
    status_frames = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")

    def show_status(label: str, *, force: bool = False) -> None:
        nonlocal last_status_update, status_active, status_frame
        if not sys.stderr.isatty():
            return
        now = time.monotonic()
        if not force and now - last_status_update < 0.08:
            return
        frame = status_frames[status_frame % len(status_frames)]
        status_frame += 1
        last_status_update = now
        status_active = True
        print(f"\r\033[2K{frame} {label}", end="", file=sys.stderr, flush=True)

    def clear_status() -> None:
        nonlocal status_active
        if status_active:
            print("\r\033[2K", end="", file=sys.stderr, flush=True)
            status_active = False

    show_status("LM Studio 응답 대기 중…", force=True)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            content_type = response.headers.get_content_type()
            if content_type != "text/event-stream":
                result = json.load(response)
                choices = result.get("choices", [])
                if not choices:
                    raise RuntimeError(f"LM Studio returned no choices: {result}")
                message = choices[0].get("message", {})
                content = str(message.get("content") or "")
                if content:
                    clear_status()
                    emit(content)
                    printed = True
                return message, printed

            for raw_line in response:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if not data or data == "[DONE]":
                    continue
                try:
                    event = json.loads(data)
                except json.JSONDecodeError:
                    continue
                if event.get("error"):
                    raise RuntimeError(f"LM Studio streaming error: {event['error']}")
                choices = event.get("choices", [])
                if not choices:
                    continue
                delta = choices[0].get("delta", {})
                if delta.get("reasoning_content"):
                    show_status("생각 중…")
                content = delta.get("content")
                if isinstance(content, str) and content:
                    clear_status()
                    content_parts.append(content)
                    emit(content)
                    printed = True
                for fragment in delta.get("tool_calls") or []:
                    show_status("MCP 도구 선택 중…")
                    index = int(fragment.get("index", 0))
                    call = tool_calls.setdefault(
                        index,
                        {
                            "id": "",
                            "type": "function",
                            "function": {"name": "", "arguments": ""},
                        },
                    )
                    if fragment.get("id"):
                        call["id"] += str(fragment["id"])
                    if fragment.get("type"):
                        call["type"] = fragment["type"]
                    function = fragment.get("function") or {}
                    if function.get("name"):
                        call["function"]["name"] += str(function["name"])
                    if function.get("arguments"):
                        call["function"]["arguments"] += str(function["arguments"])
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"LM Studio HTTP {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"LM Studio server is unreachable at {url}: {exc}") from exc
    finally:
        clear_status()

    message: dict[str, Any] = {
        "role": "assistant",
        "content": "".join(content_parts) or None,
    }
    if tool_calls:
        message["tool_calls"] = [tool_calls[index] for index in sorted(tool_calls)]
    return message, printed


def tool_result_text(result: dict[str, Any]) -> str:
    structured = result.get("structuredContent")
    if structured is not None:
        return json.dumps(structured, ensure_ascii=False)
    content = result.get("content", [])
    texts = [str(item.get("text", "")) for item in content if item.get("type") == "text"]
    return "\n".join(texts) if texts else json.dumps(result, ensure_ascii=False)


def compact_in_background(
    messages: list[dict[str, Any]],
    tool_definitions: list[dict[str, Any]],
    *,
    node: str,
    adapter: Path,
    context_length: int,
    soft_remaining_tokens: int,
    compact_above_messages: int,
) -> list[dict[str, Any]]:
    estimated_input_tokens = (len(json.dumps(messages, ensure_ascii=False)) + len(json.dumps(tool_definitions))) // 4
    measurement = {
        "contextLength": context_length,
        "inputTokens": estimated_input_tokens,
        "remainingTokens": context_length - estimated_input_tokens - 8192 - 1536,
        "exact": False,
        "messageCount": len(messages),
    }
    payload = {
        "messages": messages,
        "measurement": measurement,
        "options": {
            "softRemainingTokens": soft_remaining_tokens,
            "hardRemainingTokens": 8000,
            "maxOutputReserve": 8192,
            "safetyMarginTokens": 1536,
            "recentCompleteTurns": 2,
            "compactAboveMessageCount": compact_above_messages,
            "maxCheckpointChars": 22000,
            "maxToolResultChars": 1200,
        },
    }
    completed = subprocess.run(
        [node, str(adapter)],
        input=json.dumps(payload, ensure_ascii=False),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=15,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr or "Headless context compactor failed")
    result = json.loads(completed.stdout)
    return list(result.get("messages", messages))


def run_turn(
    *,
    messages: list[dict[str, Any]],
    prompt: str,
    model: str,
    endpoint: str,
    tool_definitions: list[dict[str, Any]],
    tool_owners: dict[str, McpProcess],
    timeout: float,
    max_rounds: int,
    compactor_node: str,
    compactor_adapter: Path,
    context_length: int,
    soft_remaining_tokens: int,
    compact_above_messages: int,
    stream: bool,
) -> str:
    messages.append({"role": "user", "content": prompt})
    for _ in range(max_rounds):
        messages[:] = compact_in_background(
            messages,
            tool_definitions,
            node=compactor_node,
            adapter=compactor_adapter,
            context_length=context_length,
            soft_remaining_tokens=soft_remaining_tokens,
            compact_above_messages=compact_above_messages,
        )
        payload = {
            "model": model,
            "messages": messages,
            "tools": tool_definitions,
            "tool_choice": "auto",
            "temperature": 0,
            "stream": stream,
        }
        if stream:
            message, printed = post_chat_stream(
                endpoint,
                payload,
                timeout,
                lambda text: print(text, end="", flush=True),
            )
        else:
            response = post_chat(endpoint, payload, timeout)
            choices = response.get("choices", [])
            if not choices:
                raise RuntimeError(f"LM Studio returned no choices: {response}")
            message = choices[0].get("message", {})
            content = str(message.get("content") or "")
            if content:
                print(content, end="", flush=True)
            printed = bool(content)
        tool_calls = message.get("tool_calls") or []
        messages.append(message)
        if not tool_calls:
            if printed:
                print()
            return str(message.get("content") or "")
        if printed:
            print()
        for call in tool_calls:
            function = call.get("function", {})
            name = str(function.get("name", ""))
            owner = tool_owners.get(name)
            if owner is None:
                result_text = json.dumps({"error": f"Unknown MCP tool: {name}"}, ensure_ascii=False)
            else:
                raw_arguments = function.get("arguments") or "{}"
                try:
                    arguments = json.loads(raw_arguments) if isinstance(raw_arguments, str) else raw_arguments
                    result_text = tool_result_text(owner.call(name, arguments))
                except Exception as exc:  # noqa: BLE001 - return tool errors to the model loop.
                    result_text = json.dumps({"error": str(exc)}, ensure_ascii=False)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.get("id", name),
                    "content": result_text,
                }
            )
    raise RuntimeError(f"Model exceeded the MCP tool-call limit ({max_rounds})")


def verify_install(tool_name: str, owners: dict, definitions: list, model: str, endpoint: str, timeout: float) -> dict:
    """A fixed installer diagnostic, not a chat/planning loop or prose success check."""
    if tool_name not in {"unreal_rag_health", "unity_status"} or tool_name not in owners:
        raise McpError("Required read-only health tool is unavailable")
    result = owners[tool_name].call(tool_name, {})
    if result.get("isError"):
        raise McpError("MCP health call returned an error")
    health = result.get("structuredContent")
    if not isinstance(health, dict):
        blocks = [item.get("text", "") for item in result.get("content", []) if item.get("type") == "text"]
        try:
            health = json.loads("".join(blocks))
        except (ValueError, TypeError) as exc:
            raise McpError("Health response was not a structured JSON object") from exc
    ready = isinstance(health, dict) and (health.get("connection") == "connected" if tool_name == "unity_status" else health.get("ready") is True and health.get("okForChat") is not False)
    if not ready:
        raise McpError("MCP health is not ready; installation connection verification failed")
    selected = [d for d in definitions if d["function"]["name"] == tool_name]
    response = post_chat(endpoint, {"model": model, "messages": [{"role": "user", "content": f"Call {tool_name} with no arguments to verify the connection."}],
        "tools": selected, "tool_choice": {"type": "function", "function": {"name": tool_name}}, "stream": False}, timeout)
    calls = response.get("choices", [{}])[0].get("message", {}).get("tool_calls", [])
    if len(calls) != 1 or calls[0].get("function", {}).get("name") != tool_name or json.loads(calls[0]["function"].get("arguments") or "{}") != {}:
        raise McpError("Model did not issue the required health tool call; plain-text replies are not proof")
    return {"ready": True, "mcpHealthVerified": True, "modelToolRequestVerified": True, "tool": tool_name, "model": model}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prompt", nargs="*", help="One-shot prompt; omit for interactive chat")
    parser.add_argument("--model", default=os.environ.get("LMSTUDIO_MODEL", "qwen/qwen3.8-27b"))
    parser.add_argument(
        "--endpoint",
        default=os.environ.get("LMSTUDIO_CHAT_ENDPOINT", "http://127.0.0.1:1234/v1/chat/completions"),
    )
    parser.add_argument("--mcp-config", type=Path, default=Path.home() / ".lmstudio" / "mcp.json")
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--max-tool-rounds", type=int, default=12)
    parser.add_argument("--context-length", type=int, default=int(os.environ.get("LMSTUDIO_CONTEXT_LENGTH", "65536")))
    parser.add_argument("--soft-remaining-tokens", type=int, default=14000)
    parser.add_argument("--compact-above-messages", type=int, default=24)
    parser.add_argument("--list-tools", action="store_true")
    parser.add_argument("--verify-install", choices=["unreal_rag_health", "unity_status"], help="Verify structured MCP health and a real model tool request; never accept a prose success reply")
    parser.add_argument("--no-stream", action="store_true", help="Wait for the complete answer before printing")
    args = parser.parse_args()

    config = json.loads(args.mcp_config.expanduser().read_text(encoding="utf-8"))
    processes: list[McpProcess] = []
    tool_owners: dict[str, McpProcess] = {}
    tool_definitions: list[dict[str, Any]] = []
    compactor_adapter = Path(__file__).resolve().with_name("headless_compact.js")
    compactor_node = shutil.which("node")
    try:
        for label, spec in config.get("mcpServers", {}).items():
            process = McpProcess(label=label, spec=spec, timeout=min(args.timeout, float(spec.get("timeout", 30000)) / 1000))
            process.start()
            processes.append(process)
            for tool in process.tools():
                name = str(tool["name"])
                if name in tool_owners:
                    raise McpError(f"Duplicate MCP tool name: {name}")
                tool_owners[name] = process
                tool_definitions.append(
                    {
                        "type": "function",
                        "function": {
                            "name": name,
                            "description": tool.get("description", ""),
                            "parameters": tool.get("inputSchema", {"type": "object", "properties": {}}),
                        },
                    }
                )
            if compactor_node is None and Path(str(spec.get("command", ""))).name == "node":
                compactor_node = str(spec["command"])
        if not tool_definitions:
            raise McpError(f"No MCP tools found in {args.mcp_config}")
        if args.list_tools:
            for name, owner in sorted(tool_owners.items()):
                print(f"{owner.label}\t{name}")
            return 0
        if args.verify_install:
            print(json.dumps(verify_install(args.verify_install, tool_owners, tool_definitions, args.model, args.endpoint, args.timeout)))
            return 0
        if compactor_node is None or not compactor_adapter.is_file():
            raise RuntimeError("Headless context compactor runtime is unavailable")

        messages: list[dict[str, Any]] = []
        if args.prompt:
            run_turn(
                messages=messages,
                prompt=" ".join(args.prompt),
                model=args.model,
                endpoint=args.endpoint,
                tool_definitions=tool_definitions,
                tool_owners=tool_owners,
                timeout=args.timeout,
                max_rounds=args.max_tool_rounds,
                compactor_node=compactor_node,
                compactor_adapter=compactor_adapter,
                context_length=args.context_length,
                soft_remaining_tokens=args.soft_remaining_tokens,
                compact_above_messages=args.compact_above_messages,
                stream=not args.no_stream,
            )
            return 0

        print(f"MCP tools enabled: {len(tool_definitions)} (type /exit to quit)")
        while True:
            try:
                prompt = input("> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                return 0
            if not prompt:
                continue
            if prompt in {"/exit", "/quit"}:
                return 0
            run_turn(
                messages=messages,
                prompt=prompt,
                model=args.model,
                endpoint=args.endpoint,
                tool_definitions=tool_definitions,
                tool_owners=tool_owners,
                timeout=args.timeout,
                max_rounds=args.max_tool_rounds,
                compactor_node=compactor_node,
                compactor_adapter=compactor_adapter,
                context_length=args.context_length,
                soft_remaining_tokens=args.soft_remaining_tokens,
                compact_above_messages=args.compact_above_messages,
                stream=not args.no_stream,
            )
    finally:
        for process in reversed(processes):
            process.close()


if __name__ == "__main__":
    raise SystemExit(main())
