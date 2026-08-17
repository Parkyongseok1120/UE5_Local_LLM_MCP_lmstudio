from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from process_probe import (  # noqa: E402
    probe_process_alive,
    probe_process_start_identity,
)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission semantics")
def test_process_probe_treats_permission_denied_as_unknown(monkeypatch) -> None:
    def deny(_pid: int, _signal: int) -> None:
        raise PermissionError("cross-user process")

    monkeypatch.setattr(os, "kill", deny)
    assert probe_process_alive(12345) == "unknown"


def test_current_process_has_a_birth_identity() -> None:
    assert probe_process_start_identity(os.getpid())
