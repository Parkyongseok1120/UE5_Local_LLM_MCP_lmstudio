#!/usr/bin/env python3
"""Run a deterministic stdio smoke test against evidence_first_mcp.py."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


def smoke(server: Path, python_exe: Path) -> dict[str, Any]:
    messages: list[dict[str, Any]] = [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "evidence-first-smoke", "version": "1.0"},
                },
            },
            {
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
                "params": {},
            },
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "evidence_first_status", "arguments": {}},
            },
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {"name": "evidence_first_contract", "arguments": {"mode": "codegen"}},
            },
            {
                "jsonrpc": "2.0",
                "id": 5,
                "method": "tools/call",
                "params": {"name": "evidence_first_validate", "arguments": {"packet": {"mode": "감사"}}},
            },
            {
                "jsonrpc": "2.0",
                "id": 6,
                "method": "tools/call",
                "params": {"name": "does_not_exist", "arguments": {}},
            },
        ]
    payload = "".join(json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n" for message in messages)
    completed = subprocess.run(
        [str(python_exe), str(server)],
        input=payload,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=15,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr or f"MCP exited with {completed.returncode}")
    responses = [json.loads(line) for line in completed.stdout.splitlines() if line.strip()]
    by_id = {response.get("id"): response for response in responses}
    initialized = by_id[1]
    tools = by_id[2]
    status = by_id[3]
    invalid_packet = by_id[5]
    unknown_tool = by_id[6]
    names = [tool["name"] for tool in tools["result"]["tools"]]
    status_payload = status["result"]["structuredContent"]
    annotations_safe = all(
        tool.get("annotations", {}).get("readOnlyHint") is True
        and tool.get("annotations", {}).get("destructiveHint") is False
        for tool in tools["result"]["tools"]
    )
    ok = (
        initialized["result"]["serverInfo"]["name"] == "evidence-first-code-audit"
        and initialized["result"]["protocolVersion"] == "2025-11-25"
        and {
            "evidence_first_contract",
            "evidence_first_validate",
            "evidence_first_status",
        }.issubset(names)
        and annotations_safe
        and status_payload.get("ok") is True
        and status_payload.get("readOnly") is True
        and invalid_packet["result"].get("isError") is True
        and unknown_tool.get("error", {}).get("code") == -32602
    )
    return {"ok": ok, "tools": names, "status": status_payload, "gracefulExit": True}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    default_server = Path(__file__).resolve().with_name("evidence_first_mcp.py")
    parser.add_argument("--server", type=Path, default=default_server)
    parser.add_argument("--python", type=Path, default=Path(sys.executable))
    args = parser.parse_args()
    result = smoke(args.server.resolve(), args.python.resolve())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
