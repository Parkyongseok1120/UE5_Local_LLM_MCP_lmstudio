"""One finite transport reader, bounded queue and request cancellation registry."""
from __future__ import annotations
import json
import queue
import threading
from typing import Any, TextIO
from direct_rag_operation import RagOperation


def normalize_line(raw: str) -> str:
    return raw.strip().removeprefix("\ufeff").lstrip()


class RequestInbox:
    def __init__(self, stream: TextIO, reject, *, is_mutating=None) -> None:
        self.stream, self.reject = stream, reject
        self.is_mutating = is_mutating or (lambda name: True)
        self.queue = queue.Queue(maxsize=64)
        self.operations: dict[Any, RagOperation] = {}
        self.lock = threading.Lock()
        self.closed = threading.Event()
        self.reader = threading.Thread(target=self._read, name="direct-rag-input", daemon=True)

    def _read(self) -> None:
        try:
            for raw in self.stream:
                if self.closed.is_set():
                    break
                try:
                    request = json.loads(normalize_line(raw))
                except (ValueError, TypeError):
                    request = None
                operation = None
                if isinstance(request, dict):
                    if request.get("id") is not None and not isinstance(request["id"], (str, int)):
                        self.reject(request["id"], -32600, "Invalid request id")
                        continue
                    if request.get("method") == "notifications/cancelled":
                        params = request.get("params") or {}
                        target = params.get("requestId") if isinstance(params, dict) else None
                        with self.lock:
                            operation = self.operations.get(target) if isinstance(target, (str, int)) else None
                            if operation is not None:
                                operation.cancelled.set()
                        continue
                    if request.get("method") == "tools/call" and request.get("id") is not None:
                        with self.lock:
                            if request["id"] in self.operations:
                                self.reject(request["id"], -32600, "Duplicate in-flight request id")
                                continue
                            name = str(request["params"].get("name") or "") if isinstance(request.get("params"), dict) else ""
                            operation = RagOperation(mutating=self.is_mutating(name))
                            self.operations[request["id"]] = operation
                try:
                    self.queue.put_nowait((raw, operation))
                except queue.Full:
                    if isinstance(request, dict) and request.get("id") is not None:
                        self.finish(request["id"])
                        self.reject(request["id"], -32000, "Direct RAG request queue is full")
        finally:
            # EOF cancels running/queued mutation; buffered read-only requests
            # may finish finite work (including a piped CLI request batch).
            with self.lock:
                for operation in self.operations.values():
                    if operation.mutating:
                        operation.cancelled.set()
            self.closed.set()

    def finish(self, request_id: Any) -> None:
        if not isinstance(request_id, (str, int)):
            return
        with self.lock:
            self.operations.pop(request_id, None)

    def __iter__(self):
        self.reader.start()
        try:
            while not self.closed.is_set() or not self.queue.empty():
                try:
                    yield self.queue.get(timeout=0.05)
                except queue.Empty:
                    continue
        finally:
            self.closed.set()
            with self.lock:
                for operation in self.operations.values():
                    operation.cancelled.set()
                self.operations.clear()
            self.reader.join(timeout=0.1)


__all__ = ["RequestInbox"]
