"""Request-owned cooperative cancellation and bounded collector subprocess lifetime."""
from __future__ import annotations

import contextlib
import contextvars
import os
import signal
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class RagOperationStopped(RuntimeError):
    def __init__(self, code: str, *, termination_confirmed: bool = True) -> None:
        super().__init__(code)
        self.code = code
        self.termination_confirmed = termination_confirmed
        self.preserved_scratch: list[str] = []


@dataclass
class RagOperation:
    cancelled: threading.Event = field(default_factory=threading.Event)
    started: bool = False
    deadline: float | None = None
    publication_started: bool = False
    mutating: bool = False

    def check(self) -> None:
        if self.publication_started:
            return
        if self.cancelled.is_set():
            raise RagOperationStopped("RAG_OPERATION_CANCELLED")
        if self.deadline is not None and time.monotonic() >= self.deadline:
            raise RagOperationStopped("RAG_OPERATION_TIMEOUT")


_CURRENT: contextvars.ContextVar[RagOperation | None] = contextvars.ContextVar("direct_rag_operation", default=None)
_PENDING: list[tuple[subprocess.Popen, Path | None]] = []


def collector_cleanup_safe(path: Path) -> bool:
    target = path.resolve()
    return not any(process.poll() is None and scratch is not None
                   and (scratch.is_relative_to(target) or target.is_relative_to(scratch))
                   for process, scratch in _PENDING)


@contextlib.contextmanager
def operation_scope(operation: RagOperation, *, timeout: float = 600.0):
    operation.started = True
    operation.deadline = time.monotonic() + timeout
    token = _CURRENT.set(operation)
    try:
        yield operation
    finally:
        operation.started = False
        _CURRENT.reset(token)


def check_operation() -> None:
    operation = _CURRENT.get()
    if operation is not None:
        operation.check()


def begin_publication() -> None:
    """Cancellation is accepted before publication, never halfway through swap."""
    operation = _CURRENT.get()
    if operation is not None:
        operation.check()
        operation.publication_started = True


def _terminate_tree(process: subprocess.Popen) -> bool:
    if process.poll() is not None:
        return True
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=2, check=False, creationflags=subprocess.CREATE_NO_WINDOW)
        else:
            os.killpg(process.pid, signal.SIGKILL)
    except (OSError, subprocess.TimeoutExpired):
        try:
            process.kill()
        except OSError:
            pass
    try:
        process.wait(timeout=2)
        return True
    except subprocess.TimeoutExpired:
        return False


def run_collector(command: list[str], *, cwd: Path, timeout: float = 600.0,
                  scratch: Path | None = None, max_output_bytes: int = 16 * 1024 * 1024) -> dict[str, Any]:
    """File-backed output avoids descendant pipe EOF holding shutdown forever."""
    check_operation()
    _PENDING[:] = [(process, path) for process, path in _PENDING if process.poll() is None]
    if _PENDING:
        stopped = RagOperationStopped("RAG_COLLECTOR_SHUTDOWN_PENDING", termination_confirmed=False)
        stopped.preserved_scratch = [str(path) for _, path in _PENDING if path is not None]
        raise stopped
    deadline = time.monotonic() + timeout
    # TemporaryFile closes/unlinks automatically; no model-controlled log path.
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        process = subprocess.Popen(
            command, cwd=str(cwd), stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
            env={**os.environ, "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1"},
            **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {"start_new_session": True}),
        )
        try:
            while process.poll() is None:
                check_operation()
                if sum(os.fstat(stream.fileno()).st_size for stream in (stdout, stderr)) > max_output_bytes:
                    raise RagOperationStopped("RAG_COLLECTOR_OUTPUT_LIMIT")
                if time.monotonic() >= deadline:
                    raise RagOperationStopped("RAG_COLLECTOR_TIMEOUT")
                time.sleep(0.05)
            check_operation()
            if sum(os.fstat(stream.fileno()).st_size for stream in (stdout, stderr)) > max_output_bytes:
                raise RagOperationStopped("RAG_COLLECTOR_OUTPUT_LIMIT")
        except BaseException as exc:
            confirmed = _terminate_tree(process)
            if isinstance(exc, RagOperationStopped):
                exc.termination_confirmed = confirmed
            if not confirmed:
                protected = scratch.resolve() if scratch is not None else None
                _PENDING.append((process, protected))
                if isinstance(exc, RagOperationStopped) and protected is not None:
                    exc.preserved_scratch = [str(protected)]
            raise
        tails = []
        for stream in (stdout, stderr):
            stream.seek(0, os.SEEK_END)
            stream.seek(max(0, stream.tell() - 4000))
            tails.append(stream.read().decode("utf-8", errors="replace"))
        return {"ok": process.returncode == 0, "returncode": process.returncode,
                "command": command, "outputTail": "".join(tails)[-4000:]}


__all__ = ["RagOperation", "RagOperationStopped", "operation_scope", "check_operation", "begin_publication", "run_collector", "collector_cleanup_safe"]
