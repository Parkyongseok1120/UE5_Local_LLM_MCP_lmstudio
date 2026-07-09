from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from lmstudio_unreal_wrapper import build_static_autofix_steps  # noqa: E402
from unreal_static_validate import validate_unreal_readiness  # noqa: E402


def test_holdout_matrix_static_scan_runs_for_each_case() -> None:
    fixture_root = ROOT / "data" / "baseline" / "local_holdout"
    if not fixture_root.is_dir():
        return
    audited = 0
    for fixture_dir in sorted(fixture_root.iterdir()):
        if not fixture_dir.is_dir():
            continue
        project_files = list(fixture_dir.rglob("*.uproject"))
        if not project_files:
            continue
        project_root = project_files[0].parent
        validate_unreal_readiness(project_root)
        steps = build_static_autofix_steps("compile_fix")
        assert steps
        audited += 1
    assert audited >= 30
