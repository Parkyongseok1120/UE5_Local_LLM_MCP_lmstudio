from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from validate_project_sources import resolve_project_root  # noqa: E402


def test_resolve_project_root_from_uproject(tmp_path: Path) -> None:
    uproject = tmp_path / "Demo.uproject"
    uproject.write_text("{}", encoding="utf-8")
    assert resolve_project_root(uproject) == tmp_path


def test_resolve_project_root_from_source_dir(tmp_path: Path) -> None:
    source = tmp_path / "Source"
    source.mkdir()
    assert resolve_project_root(source) == tmp_path


def test_cli_missing_source_returns_exit_code_2(tmp_path: Path) -> None:
    script = SCRIPTS / "validate_project_sources.py"
    result = subprocess.run(
        [sys.executable, str(script), "--project-root", str(tmp_path), "--json"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2


def test_cli_json_output_on_clean_fixture(tmp_path: Path) -> None:
    project = tmp_path / "Demo"
    (project / "Source" / "Demo").mkdir(parents=True)
    (project / "Source" / "Demo" / "Demo.Build.cs").write_text(
        'using UnrealBuildTool;\npublic class Demo : ModuleRules { public Demo(ReadOnlyTargetRules Target) : base(Target) {} }\n',
        encoding="utf-8",
    )
    script = SCRIPTS / "validate_project_sources.py"
    result = subprocess.run(
        [sys.executable, str(script), "--project-root", str(project), "--json"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    payload = json.loads(result.stdout)
    assert "findingCount" in payload
    assert "hasErrors" in payload
