"""Portable-engine regression coverage for the PowerShell prototype scaffold."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "scaffold_prototype.ps1"
POWERSHELL = shutil.which("pwsh") or shutil.which("powershell")


def test_scaffold_source_has_no_default_unreal_version_or_ue5_only_scratch_dependency() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert "Get-ScaffoldEngineAssociation" in source
    assert '"EngineAssociation": "5.8"' not in source
    assert "Unreal5_8" not in source
    assert "BuildSettingsVersion.V7" not in source
    assert "EnhancedInput" not in source


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell is not installed")
def test_scaffold_uses_active_project_association_and_explicit_override(tmp_path: Path) -> None:
    active = tmp_path / "Active" / "Active.uproject"
    active.parent.mkdir()
    active.write_text(
        json.dumps({"FileVersion": 3, "EngineAssociation": "{active-source-guid}"}),
        encoding="utf-8",
    )
    shared = tmp_path / "unreal-workspace.json"
    shared.write_text(json.dumps({"activeProject": str(active)}), encoding="utf-8")
    env = dict(os.environ)
    env["SHARED_UNREAL_CONFIG"] = str(shared)

    selected_output = tmp_path / "selected"
    selected = subprocess.run(
        [
            str(POWERSHELL),
            "-NoProfile",
            "-File",
            str(SCRIPT),
            "-OutputRoot",
            str(selected_output),
            "-ModuleName",
            "SelectedPrototype",
        ],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    assert selected.returncode == 0, selected.stderr
    selected_descriptor = json.loads(
        (selected_output / "SelectedPrototype.uproject").read_text(encoding="utf-8-sig")
    )
    assert selected_descriptor["EngineAssociation"] == "{active-source-guid}"

    omitted_output = tmp_path / "omitted"
    omitted_env = dict(env)
    omitted_env["SHARED_UNREAL_CONFIG"] = str(tmp_path / "missing-unreal-workspace.json")
    omitted = subprocess.run(
        [
            str(POWERSHELL),
            "-NoProfile",
            "-File",
            str(SCRIPT),
            "-OutputRoot",
            str(omitted_output),
            "-ModuleName",
            "OmittedPrototype",
        ],
        check=False,
        capture_output=True,
        text=True,
        env=omitted_env,
    )
    assert omitted.returncode == 0, omitted.stderr
    omitted_descriptor = json.loads(
        (omitted_output / "OmittedPrototype.uproject").read_text(encoding="utf-8-sig")
    )
    assert "EngineAssociation" not in omitted_descriptor

    explicit_output = tmp_path / "explicit"
    explicit = subprocess.run(
        [
            str(POWERSHELL),
            "-NoProfile",
            "-File",
            str(SCRIPT),
            "-OutputRoot",
            str(explicit_output),
            "-ModuleName",
            "ExplicitPrototype",
            "-EngineAssociation",
            "5.4",
        ],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    assert explicit.returncode == 0, explicit.stderr
    explicit_descriptor = json.loads(
        (explicit_output / "ExplicitPrototype.uproject").read_text(encoding="utf-8-sig")
    )
    assert explicit_descriptor["EngineAssociation"] == "5.4"
