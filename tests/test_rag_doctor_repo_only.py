from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_rag_doctor_repo_only_passes_without_ue() -> None:
    proc = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "rag_doctor.py"),
            "--rag-root",
            str(ROOT),
            "--repo-only",
        ],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
    assert "Repo-only doctor checks passed" in proc.stdout
    assert "[FAIL]" not in proc.stdout
