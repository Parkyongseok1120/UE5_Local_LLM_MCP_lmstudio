from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_rag_ps1_doctor_parses_and_runs() -> None:
    ps = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(ROOT / "rag.ps1"),
            "doctor",
            "-RepoOnly",
        ],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        timeout=60,
    )
    assert ps.returncode == 0, ps.stderr or ps.stdout
    assert "build" in ps.stdout.lower() or "doctor" in ps.stdout.lower()
