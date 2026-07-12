from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from process_identity import command_fingerprint, verify_job_process  # noqa: E402


def test_verify_job_process_matches_stored_command(monkeypatch) -> None:
    command = [sys.executable, str(SCRIPTS / "rag_refresh.py"), "--scope", "all"]
    job = {
        "pid": 4242,
        "command": command,
        "commandFingerprint": command_fingerprint(command),
        "pidStartedAt": "2026-01-01T00:00:00+00:00",
    }
    monkeypatch.setattr("process_identity._process_alive", lambda pid: pid == 4242)
    monkeypatch.setattr(
        "process_identity._probe_process",
        lambda pid: (f"{sys.executable} {SCRIPTS / 'rag_refresh.py'} --scope all", None),
    )
    assert verify_job_process(job) is True


def test_verify_job_process_rejects_command_mismatch(monkeypatch) -> None:
    command = [sys.executable, str(SCRIPTS / "rag_refresh.py"), "--scope", "all"]
    job = {
        "pid": 4242,
        "command": command,
        "commandFingerprint": command_fingerprint(command),
    }
    monkeypatch.setattr("process_identity._process_alive", lambda pid: pid == 4242)
    monkeypatch.setattr("process_identity._probe_process", lambda pid: ("other.exe totally different", None))
    assert verify_job_process(job) is False
