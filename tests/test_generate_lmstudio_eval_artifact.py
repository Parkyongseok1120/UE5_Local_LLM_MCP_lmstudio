from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_generate_eval_single_model_only(tmp_path: Path) -> None:
    out = tmp_path / "evidence"
    proc = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "generate_lmstudio_eval_artifact.py"),
            "--model",
            "audit-only",
            "--out-dir",
            str(out),
            "--executed",
            "--pass-rate",
            "0.5",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
    files = list(out.glob("*_lmstudio_eval.json"))
    assert len(files) == 1
    payload = json.loads(files[0].read_text(encoding="utf-8"))
    assert payload["status"] == "FAIL"
