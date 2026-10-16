#!/usr/bin/env python
"""Bounded overlap for read-only Direct RAG calls; mutations stay serialized.

Read-only capabilities open their own read-only SQLite connections per call
(direct_rag_readonly_db), so they may run on a small pool. A mutating or
unknown tool call first waits for every in-flight read and then runs on the
transport thread, so no new read starts until it finishes.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor, wait
from typing import Any

READ_WORKERS = 4


class ReadDispatcher:
    def __init__(self, concurrent_tools: frozenset[str], *, workers: int = READ_WORKERS) -> None:
        self._concurrent_tools = concurrent_tools
        self._executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="direct-rag-read")
        self._in_flight: set[Future] = set()
        self._lock = threading.Lock()

    def runs_concurrently(self, request: dict[str, Any]) -> bool:
        if request.get("method") != "tools/call" or request.get("id") is None:
            return False
        params = request.get("params") if isinstance(request.get("params"), dict) else {}
        return str(params.get("name") or "") in self._concurrent_tools

    def dispatch(self, request: dict[str, Any], process: Callable[[], None]) -> None:
        if self.runs_concurrently(request):
            future = self._executor.submit(process)
            with self._lock:
                self._in_flight.add(future)
            # Registered after tracking, so an already finished call is discarded at once.
            future.add_done_callback(self._discard)
            return
        if request.get("method") == "tools/call":
            self.drain()
        process()

    def drain(self) -> None:
        with self._lock:
            pending = set(self._in_flight)
        wait(pending)

    def _discard(self, future: Future) -> None:
        with self._lock:
            self._in_flight.discard(future)

    def close(self) -> None:
        self.drain()
        self._executor.shutdown(wait=True)

    def __enter__(self) -> "ReadDispatcher":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


__all__ = ["READ_WORKERS", "ReadDispatcher"]
