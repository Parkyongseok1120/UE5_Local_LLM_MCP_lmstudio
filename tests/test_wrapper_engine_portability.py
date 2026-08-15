"""Focused portability coverage for the LM Studio Unreal wrapper."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import lmstudio_unreal_wrapper as wrapper


def _scratch_args(tmp_path: Path, *, association: str = "") -> SimpleNamespace:
    return SimpleNamespace(
        project_file="",
        project_name="PortableScratch",
        run_dir="",
        scratch_root=str(tmp_path / "runs"),
        target="",
        allow_direct_project_write=False,
        engine_association=association,
        ubt_path="",
    )


def _engine_with_ubt(tmp_path: Path) -> tuple[Path, Path]:
    engine = tmp_path / "SourceBuild"
    ubt_dir = engine / "Engine" / "Binaries" / "DotNET" / "UnrealBuildTool"
    ubt_dir.mkdir(parents=True)
    name = "UnrealBuildTool.exe" if sys.platform == "win32" else "UnrealBuildTool.dll"
    ubt = ubt_dir / name
    ubt.write_bytes(b"fixture")
    return engine, ubt


def test_minimal_scratch_project_omits_default_engine_binding_and_ue5_only_dependencies(
    tmp_path: Path,
) -> None:
    project_file = wrapper.create_minimal_unreal_project(tmp_path / "Scratch", "PortableScratch")

    descriptor = json.loads(project_file.read_text(encoding="utf-8"))
    game_target = project_file.parent / "Source" / "PortableScratch.Target.cs"
    editor_target = project_file.parent / "Source" / "PortableScratchEditor.Target.cs"
    build_cs = project_file.parent / "Source" / "PortableScratch" / "PortableScratch.Build.cs"

    assert "EngineAssociation" not in descriptor
    assert "EngineIncludeOrderVersion" not in game_target.read_text(encoding="utf-8")
    assert "EngineIncludeOrderVersion" not in editor_target.read_text(encoding="utf-8")
    assert "EnhancedInput" not in build_cs.read_text(encoding="utf-8")


def test_scratch_project_binds_explicit_custom_association_to_exact_resolved_ubt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    engine, ubt = _engine_with_ubt(tmp_path)
    args = _scratch_args(tmp_path, association="{source-build-guid}")
    calls: dict[str, object] = {}

    def resolve_association(association: str, start: Path) -> dict[str, object]:
        calls["association"] = association
        calls["workspace"] = start
        return {
            "ok": True,
            "engineRoot": str(engine),
            "source": "config.engineRootsByAssociation",
            "requestedEngineAssociation": association,
            "errorCode": "",
            "error": "",
        }

    monkeypatch.setattr(wrapper, "find_workspace_root", lambda: workspace)
    monkeypatch.setattr(wrapper, "resolve_engine_root_for_association", resolve_association)

    prepared = wrapper.prepare_run(args, "portable request")
    descriptor = json.loads(prepared.project_file.read_text(encoding="utf-8"))
    metadata = json.loads((prepared.run_dir / "run_metadata.json").read_text(encoding="utf-8"))

    assert descriptor["EngineAssociation"] == "{source-build-guid}"
    assert calls == {
        "association": "{source-build-guid}",
        "workspace": workspace,
    }
    assert args.ubt_path == str(ubt)
    assert metadata["engine_root"] == str(engine)
    assert metadata["ubt_path"] == str(ubt)


def test_unresolved_scratch_custom_association_fails_before_creating_tree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    args = _scratch_args(tmp_path, association="custom-source-build")
    monkeypatch.setattr(
        wrapper,
        "resolve_engine_root_for_association",
        lambda association, start: {
            "ok": False,
            "engineRoot": "",
            "source": "",
            "requestedEngineAssociation": association,
            "errorCode": "ENGINE_ASSOCIATION_UNRESOLVED",
            "error": "ENGINE_ASSOCIATION_UNRESOLVED: custom source build is not mapped",
        },
    )

    with pytest.raises(SystemExit, match="ENGINE_ASSOCIATION_UNRESOLVED"):
        wrapper.prepare_run(args, "portable request")

    assert not Path(args.scratch_root).exists()


def test_existing_project_custom_association_uses_exact_engine_for_default_ubt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine, ubt = _engine_with_ubt(tmp_path)
    project = tmp_path / "Project" / "Project.uproject"
    project.parent.mkdir()
    project.write_text(
        json.dumps({"FileVersion": 3, "EngineAssociation": "source-guid"}),
        encoding="utf-8",
    )
    args = SimpleNamespace(
        project_file=str(project),
        project_name="ignored",
        run_dir=str(tmp_path / "run"),
        scratch_root=str(tmp_path / "runs"),
        target="",
        allow_direct_project_write=True,
        engine_association="",
        ubt_path="",
    )
    monkeypatch.setattr(
        wrapper,
        "resolve_engine_root_for_association",
        lambda association, start: {
            "ok": True,
            "engineRoot": str(engine),
            "source": "config.engineRootsByAssociation",
            "requestedEngineAssociation": association,
            "errorCode": "",
            "error": "",
        },
    )

    wrapper.prepare_run(args, "existing project request")

    assert args.ubt_path == str(ubt)


def test_wrapper_runs_managed_ubt_through_dotnet(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ubt = tmp_path / "UnrealBuildTool.dll"
    project = tmp_path / "Portable.uproject"
    log_path = tmp_path / "build.log"
    ubt.write_bytes(b"fixture")
    project.write_text("{}", encoding="utf-8")
    captured: dict[str, object] = {}

    def run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return SimpleNamespace(returncode=0, stdout="BUILD SUCCEEDED")

    monkeypatch.setattr(wrapper.subprocess, "run", run)

    result = wrapper.run_ubt(
        ubt,
        project,
        "PortableEditor",
        "Linux",
        "Development",
        log_path,
        10,
    )

    assert result.ok is True
    assert captured["command"][:2] == ["dotnet", str(ubt)]
    assert log_path.read_text(encoding="utf-8") == "BUILD SUCCEEDED"
