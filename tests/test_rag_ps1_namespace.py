from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
INSTALLER_SUPPORT = ROOT / "scripts" / "installer_support"


def _run_ps1(script: str, *args: str) -> subprocess.CompletedProcess[str]:
    cmd = [
        "powershell",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(INSTALLER_SUPPORT / script),
        *args,
    ]
    return subprocess.run(cmd, capture_output=True, text=True, cwd=str(ROOT), timeout=180)


def test_get_rag_data_paths_unreal57(tmp_path: Path) -> None:
    rag_root = tmp_path / "rag"
    (rag_root / "config").mkdir(parents=True)
    (rag_root / "config" / "workspace.json").write_text(
        json.dumps({"indexNamespace": "unreal57", "indexPath": "data/unreal57/rag.sqlite"}),
        encoding="utf-8",
    )
    ps = _run_ps1(
        "Test-RagDataPaths.ps1",
        "-RagRoot",
        str(rag_root),
        "-IndexNamespace",
        "unreal57",
    )
    assert ps.returncode == 0, ps.stderr or ps.stdout
    assert "unreal57" in ps.stdout
    assert "data\\unreal57" in ps.stdout or "data/unreal57" in ps.stdout


def test_get_rag_data_paths_unreal59(tmp_path: Path) -> None:
    rag_root = tmp_path / "rag"
    (rag_root / "config").mkdir(parents=True)
    (rag_root / "config" / "workspace.json").write_text(
        json.dumps({"indexNamespace": "unreal59"}),
        encoding="utf-8",
    )
    ps = _run_ps1(
        "Test-RagDataPaths.ps1",
        "-RagRoot",
        str(rag_root),
        "-IndexNamespace",
        "unreal59",
    )
    assert ps.returncode == 0, ps.stderr or ps.stdout
    assert "unreal59" in ps.stdout
