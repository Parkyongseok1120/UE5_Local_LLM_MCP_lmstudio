from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from workspace_paths import (  # noqa: E402
    discover_engine_roots,
    registered_engine_installations,
    resolve_engine_root_for_association,
)


def _workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    workspace = tmp_path / "workspace"
    workspace.joinpath("config").mkdir(parents=True)
    workspace.joinpath("config", "workspace.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("SHARED_UNREAL_CONFIG", str(tmp_path / "missing-shared.json"))
    return workspace


def _engine(root: Path, version: str) -> Path:
    build = root / "Engine" / "Build"
    build.mkdir(parents=True)
    major, minor = (int(part) for part in version.split(".", 1))
    build.joinpath("Build.version").write_text(
        json.dumps({"MajorVersion": major, "MinorVersion": minor}),
        encoding="utf-8",
    )
    return root


def test_windows_launcher_manifest_resolves_numeric_association_off_default_drive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace(tmp_path, monkeypatch)
    program_data = tmp_path / "Program Data"
    engine = _engine(tmp_path / "다른 드라이브" / "Epic 설치 5.6", "5.6")
    manifest = program_data / "Epic" / "UnrealEngineLauncher" / "LauncherInstalled.dat"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        json.dumps(
            {
                "InstallationList": [
                    {"AppName": "UE_5.6", "InstallLocation": str(engine)},
                    {"AppName": "UE_5.8", "InstallLocation": "../outside"},
                    {"AppName": "../../bad", "InstallLocation": str(engine)},
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = resolve_engine_root_for_association(
        "5.6",
        workspace,
        host_platform="win32",
        environ={"PROGRAMDATA": str(program_data)},
        home=tmp_path / "home",
        registry_installations={},
    )

    assert result["ok"] is True
    assert result["engineRoot"] == str(engine.resolve())
    assert result["source"] == "registered.launcher-manifest"


def test_windows_hkcu_mapping_resolves_guid_and_rejects_relative_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace(tmp_path, monkeypatch)
    association = "{01234567-89AB-CDEF-0123-456789ABCDEF}"
    engine = _engine(tmp_path / "소스 빌드 With Spaces", "5.7")
    mappings = {
        association: str(engine),
        "{BAD-RELATIVE}": "../outside",
    }

    result = resolve_engine_root_for_association(
        association,
        workspace,
        host_platform="win32",
        environ={},
        home=tmp_path / "home",
        launcher_manifest_paths=[],
        registry_installations=mappings,
    )
    rejected = resolve_engine_root_for_association(
        "{BAD-RELATIVE}",
        workspace,
        host_platform="win32",
        environ={},
        home=tmp_path / "home",
        launcher_manifest_paths=[],
        registry_installations=mappings,
    )

    assert result["ok"] is True
    assert result["engineRoot"] == str(engine.resolve())
    assert result["source"] == "registered.windows-registry"
    assert rejected["ok"] is False
    assert rejected["errorCode"] == "ENGINE_ASSOCIATION_UNRESOLVED"


def test_exact_guid_registration_wins_over_stale_environment_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace(tmp_path, monkeypatch)
    association = "{BBBBBBBB-1111-2222-3333-CCCCCCCCCCCC}"
    stale = _engine(tmp_path / "previous project engine", "5.6")
    registered = _engine(tmp_path / "현재 프로젝트 엔진", "5.7")

    result = resolve_engine_root_for_association(
        association,
        workspace,
        host_platform="win32",
        environ={"UNREAL_ENGINE_ROOT": str(stale)},
        home=tmp_path / "home",
        launcher_manifest_paths=[],
        registry_installations={association: str(registered)},
    )

    assert result["ok"] is True
    assert result["engineRoot"] == str(registered.resolve())
    assert result["source"] == "registered.windows-registry"


def test_exact_config_mapping_wins_over_stale_environment_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace(tmp_path, monkeypatch)
    association = "custom-project-build"
    stale = _engine(tmp_path / "previous engine", "5.6")
    mapped = _engine(tmp_path / "mapped engine", "5.7")
    workspace.joinpath("config", "workspace.json").write_text(
        json.dumps({"engineRootsByAssociation": {association: str(mapped)}}),
        encoding="utf-8",
    )

    result = resolve_engine_root_for_association(
        association,
        workspace,
        host_platform="linux",
        environ={"UNREAL_ENGINE_ROOT": str(stale)},
        home=tmp_path / "home",
        install_ini_paths=[],
    )

    assert result["ok"] is True
    assert result["engineRoot"] == str(mapped.resolve())
    assert result["source"] == "config.engineRootsByAssociation"


def test_numeric_association_rejects_environment_with_unknown_engine_version(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace(tmp_path, monkeypatch)
    unknown = tmp_path / "Source Engine Without Version"
    unknown.joinpath("Engine", "Build").mkdir(parents=True)
    home_with_exact = tmp_path / "home with exact"
    exact = _engine(home_with_exact / "UnrealEngine" / "UE_5.8", "5.8")

    result = resolve_engine_root_for_association(
        "5.8",
        workspace,
        host_platform="linux",
        environ={"UNREAL_ENGINE_ROOT": str(unknown)},
        home=home_with_exact,
        install_ini_paths=[],
    )
    unresolved = resolve_engine_root_for_association(
        "5.8",
        workspace,
        host_platform="linux",
        environ={"UNREAL_ENGINE_ROOT": str(unknown)},
        home=tmp_path / "empty home",
        install_ini_paths=[],
    )
    explicit = resolve_engine_root_for_association(
        "5.8",
        workspace,
        explicit_engine_root=unknown,
        host_platform="linux",
        environ={},
        home=tmp_path / "home",
        install_ini_paths=[],
    )

    assert result["ok"] is True
    assert result["engineRoot"] == str(exact.resolve())
    assert result["source"] == "EngineAssociation"
    assert unresolved["ok"] is False
    assert unresolved["errorCode"] == "ENGINE_ASSOCIATION_UNRESOLVED"
    assert explicit["ok"] is True
    assert explicit["engineRoot"] == str(unknown.resolve())


def test_installer_managed_environment_cannot_retarget_another_custom_guid(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace(tmp_path, monkeypatch)
    installed_for = "{AAAAAAAA-1111-2222-3333-AAAAAAAAAAAA}"
    requested = "{BBBBBBBB-1111-2222-3333-BBBBBBBBBBBB}"
    installed_engine = _engine(tmp_path / "installed project engine", "5.7")

    managed_results = [
        resolve_engine_root_for_association(
            requested,
            workspace,
            host_platform="linux",
            environ={
                "UNREAL_ENGINE_ROOT": str(installed_engine),
                "UNREAL_ENGINE_ROOT_ASSOCIATION": metadata,
            },
            home=tmp_path / "home",
            install_ini_paths=[],
        )
        for metadata in (installed_for, "")
    ]
    matching_managed = resolve_engine_root_for_association(
        requested,
        workspace,
        host_platform="linux",
        environ={
            "UNREAL_ENGINE_ROOT": str(installed_engine),
            "UNREAL_ENGINE_ROOT_ASSOCIATION": requested,
        },
        home=tmp_path / "home",
        install_ini_paths=[],
    )
    intentional_shell_override = resolve_engine_root_for_association(
        requested,
        workspace,
        host_platform="linux",
        environ={"UNREAL_ENGINE_ROOT": str(installed_engine)},
        home=tmp_path / "home",
        install_ini_paths=[],
    )

    assert all(result["ok"] is False for result in managed_results)
    assert all(
        result["errorCode"] == "ENGINE_ASSOCIATION_UNRESOLVED"
        for result in managed_results
    )
    assert matching_managed["ok"] is True
    assert matching_managed["source"] == "environment"
    assert intentional_shell_override["ok"] is True
    assert intentional_shell_override["engineRoot"] == str(installed_engine.resolve())
    assert intentional_shell_override["source"] == "environment"


@pytest.mark.parametrize(
    ("host_platform", "association", "registered_key", "relative_ini"),
    [
        (
            "linux",
            "{AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE}",
            "{AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE}",
            Path(".config") / "Epic" / "UnrealEngine" / "Install.ini",
        ),
        (
            "darwin",
            "5.7",
            "UE_5.7",
            Path("Library") / "Application Support" / "Epic" / "UnrealEngine" / "Install.ini",
        ),
    ],
)
def test_install_ini_exact_mapping_supports_linux_and_macos(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    host_platform: str,
    association: str,
    registered_key: str,
    relative_ini: Path,
) -> None:
    workspace = _workspace(tmp_path, monkeypatch)
    home = tmp_path / f"{host_platform} home"
    engine = _engine(tmp_path / host_platform / "엔진 루트 With Spaces", "5.7")
    outside = _engine(tmp_path / host_platform / "outside section", "5.8")
    install_ini = home / relative_ini
    install_ini.parent.mkdir(parents=True)
    install_ini.write_text(
        "\n".join(
            [
                "[Outside]",
                f"OUTSIDE={outside}",
                "[Installations]",
                f"{registered_key}={engine}",
                "RELATIVE=../../outside",
            ]
        ),
        encoding="utf-8",
    )

    result = resolve_engine_root_for_association(
        association,
        workspace,
        host_platform=host_platform,
        environ={},
        home=home,
        install_ini_paths=[install_ini],
    )
    outside_result = resolve_engine_root_for_association(
        "OUTSIDE",
        workspace,
        host_platform=host_platform,
        environ={},
        home=home,
        install_ini_paths=[install_ini],
    )
    relative_result = resolve_engine_root_for_association(
        "RELATIVE",
        workspace,
        host_platform=host_platform,
        environ={},
        home=home,
        install_ini_paths=[install_ini],
    )

    assert result["ok"] is True
    assert result["engineRoot"] == str(engine.resolve())
    assert result["source"] == "registered.install-ini"
    assert outside_result["ok"] is False
    assert relative_result["ok"] is False


def test_conflicting_registered_roots_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace(tmp_path, monkeypatch)
    first = _engine(tmp_path / "first" / "Engine56", "5.6")
    second = _engine(tmp_path / "second" / "Engine56", "5.6")
    manifest = tmp_path / "LauncherInstalled.dat"
    manifest.write_text(
        json.dumps(
            {
                "InstallationList": [
                    {"AppName": "UE_5.6", "InstallLocation": str(first)},
                    {"AppName": "UE_5.6", "InstallLocation": str(second)},
                ]
            }
        ),
        encoding="utf-8",
    )

    result = resolve_engine_root_for_association(
        "5.6",
        workspace,
        host_platform="win32",
        environ={},
        home=tmp_path / "home",
        launcher_manifest_paths=[manifest],
        registry_installations={},
    )

    assert result["ok"] is False
    assert result["errorCode"] == "ENGINE_ASSOCIATION_UNRESOLVED"
    assert "multiple conflicting registered engine roots" in str(result["error"])


def test_registration_enumerator_never_accepts_relative_or_non_engine_paths(tmp_path: Path) -> None:
    valid = _engine(tmp_path / "valid", "5.6")
    rows = registered_engine_installations(
        "win32",
        {},
        tmp_path / "home",
        launcher_manifest_paths=[],
        registry_installations={
            "VALID": str(valid),
            "RELATIVE": "../../outside",
            "NOT_ENGINE": str(tmp_path / "plain directory"),
            "../BAD_ASSOCIATION": str(valid),
        },
    )

    assert [(row["association"], row["engineRoot"]) for row in rows] == [
        ("VALID", str(valid.resolve()))
    ]


def test_registered_engine_is_not_duplicated_by_common_location_discovery(
    tmp_path: Path,
) -> None:
    program_files = tmp_path / "Program Files"
    engine = _engine(program_files / "Epic Games" / "UE_5.6", "5.6")
    manifest = tmp_path / "LauncherInstalled.dat"
    manifest.write_text(
        json.dumps(
            {
                "InstallationList": [
                    {"AppName": "UE_5.6", "InstallLocation": str(engine)}
                ]
            }
        ),
        encoding="utf-8",
    )

    roots = discover_engine_roots(
        host_platform="win32",
        environ={"ProgramFiles": str(program_files)},
        home=tmp_path / "home",
        launcher_manifest_paths=[manifest],
        registry_installations={},
    )

    assert roots == [engine.resolve()]
