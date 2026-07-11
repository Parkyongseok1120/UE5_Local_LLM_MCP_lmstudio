#!/usr/bin/env python
"""Minimal MCP runtime soak helpers."""

from __future__ import annotations

import json
import queue
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


class StdioJsonRpc:
    def __init__(self, cmd: list[str], *, env: dict[str, str] | None = None, cwd: Path | None = None) -> None:
        self.proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            env=env,
            cwd=str(cwd or ROOT),
            bufsize=1,
        )
        assert self.proc.stdin and self.proc.stdout
        self._queue: queue.Queue[str] = queue.Queue()
        self._reader = threading.Thread(target=self._read_stdout, daemon=True)
        self._reader.start()

    def _read_stdout(self) -> None:
        assert self.proc.stdout
        for line in self.proc.stdout:
            self._queue.put(line)

    def send(self, payload: dict[str, Any]) -> None:
        assert self.proc.stdin
        self.proc.stdin.write(json.dumps(payload) + "\n")
        self.proc.stdin.flush()

    def read_response(self, req_id: int, *, timeout_sec: float = 15.0) -> dict[str, Any]:
        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            try:
                line = self._queue.get(timeout=max(0.05, deadline - time.time()))
            except queue.Empty:
                continue
            line = line.strip()
            if not line:
                continue
            message = json.loads(line)
            if message.get("id") == req_id:
                return message
        if self.proc.poll() is None:
            self.proc.terminate()
        raise TimeoutError(f"Timed out waiting for response id={req_id}")

    def close(self) -> None:
        if self.proc.poll() is None:
            self.proc.terminate()
