from __future__ import annotations

import json
from pathlib import Path

import pytest

from unreal_capability_detection import detect_unreal_capabilities


def _fixture(tmp_path: Path, host: str) -> tuple[Path, Path]:
    project = tmp_path / "GenericProject" / "GenericProject.uproject"
    project.parent.mkdir()
    project.write_text(
        json.dumps(
            {
                "EngineAssociation": "custom-install",
                "Modules": [{"Name": "GenericRuntime", "Type": "Runtime"}],
                "Plugins": [{"Name": "EnhancedInput", "Enabled": True}],
            }
        ),
        encoding="utf-8",
    )
    source = project.parent / "Source" / "GenericRuntime"
    source.mkdir(parents=True)
    (source / "GenericRuntime.Build.cs").write_text("public class GenericRuntime {}", encoding="utf-8")
    (source / "GenericTests.cpp").write_text(
        'IMPLEMENT_SIMPLE_AUTOMATION_TEST(FGenericTest, "Generic.Runtime.Contract", 0)\n',
        encoding="utf-8",
    )
    engine = tmp_path / "CustomEngine"
    (engine / "Engine" / "Build").mkdir(parents=True)
    (engine / "Engine" / "Build" / "Build.version").write_text(
        json.dumps({"MajorVersion": 5, "MinorVersion": 7}),
        encoding="utf-8",
    )
    (engine / "Engine" / "Plugins" / "FX" / "Niagara").mkdir(parents=True)
    if host == "win32":
        build = engine / "Engine" / "Binaries" / "DotNET" / "UnrealBuildTool" / "UnrealBuildTool.exe"
        editor = engine / "Engine" / "Binaries" / "Win64" / "UnrealEditor-Cmd.exe"
    elif host == "darwin":
        build = engine / "Engine" / "Build" / "BatchFiles" / "Mac" / "Build.sh"
        editor = engine / "Engine" / "Binaries" / "Mac" / "UnrealEditor-Cmd"
    else:
        build = engine / "Engine" / "Build" / "BatchFiles" / "Linux" / "Build.sh"
        editor = engine / "Engine" / "Binaries" / "Linux" / "UnrealEditor-Cmd"
    build.parent.mkdir(parents=True, exist_ok=True)
    editor.parent.mkdir(parents=True, exist_ok=True)
    build.write_text("", encoding="utf-8")
    editor.write_text("", encoding="utf-8")
    return project, engine


@pytest.mark.parametrize("host", ["win32", "linux", "darwin"])
def test_capabilities_follow_files_not_engine_version_guess(tmp_path: Path, host: str) -> None:
    project, engine = _fixture(tmp_path, host)
    result = detect_unreal_capabilities(project, engine_root=engine, host_platform=host)
    assert result["ok"] is True
    assert result["engineVersion"] == "5.7"
    assert result["engineVersionEvidence"].endswith("Build.version")
    assert result["execution"]["buildAvailable"] is True
    assert result["execution"]["editorCommandletAvailable"] is True
    assert result["execution"]["automationTests"][0]["name"] == "Generic.Runtime.Contract"
    assert result["features"]["enhancedInput"]["available"] is True
    assert result["features"]["niagara"]["available"] is True
    assert result["features"]["gameplayAbilities"]["available"] is False


def test_missing_executables_do_not_become_available_from_association(tmp_path: Path) -> None:
    project, engine = _fixture(tmp_path, "linux")
    for path in (engine / "Engine").rglob("UnrealEditor-Cmd"):
        path.unlink()
    for path in (engine / "Engine").rglob("Build.sh"):
        path.unlink()
    result = detect_unreal_capabilities(project, engine_root=engine, host_platform="linux")
    assert result["engineVersion"] == "5.7"
    assert result["execution"]["buildAvailable"] is False
    assert result["execution"]["editorCommandletAvailable"] is False


def test_missing_engine_root_never_resolves_to_process_working_directory(tmp_path: Path) -> None:
    project, _engine = _fixture(tmp_path, "linux")
    result = detect_unreal_capabilities(project, host_platform="linux")
    assert result["ok"] is True
    assert result["engineRoot"] == ""
    assert result["engineVersion"] == ""
    assert result["execution"]["buildAvailable"] is False


def test_invalid_explicit_engine_root_is_reported_without_cwd_fallback(tmp_path: Path) -> None:
    project, _engine = _fixture(tmp_path, "linux")
    missing = tmp_path / "not-an-engine"
    result = detect_unreal_capabilities(
        project,
        engine_root=missing,
        host_platform="linux",
    )
    assert result["ok"] is False
    assert result["engineRoot"] == ""
    assert any("engine root" in item for item in result["issues"])
