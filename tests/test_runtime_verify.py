from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from runtime_verify import (
    build_runtime_verify_plan,
    run_runtime_verify_plan,
)
from unreal_rag_mcp import McpServer
from workspace_paths import resolve_engine_root_for_association


def _engine(
    tmp_path: Path,
    version: str,
    platform: str,
    *,
    name: str = "",
) -> tuple[Path, Path]:
    root = tmp_path / (name or f"UE_{version}")
    build = root / "Engine" / "Build"
    build.mkdir(parents=True)
    major, minor = (int(part) for part in version.split(".", 1))
    build.joinpath("Build.version").write_text(
        json.dumps({"MajorVersion": major, "MinorVersion": minor}),
        encoding="utf-8",
    )
    if platform == "win32":
        editor = root / "Engine" / "Binaries" / "Win64" / "UnrealEditor-Cmd.exe"
    elif platform == "darwin":
        editor = root / "Engine" / "Binaries" / "Mac" / "UnrealEditor-Cmd"
    else:
        editor = root / "Engine" / "Binaries" / "Linux" / "UnrealEditor-Cmd"
    editor.parent.mkdir(parents=True)
    editor.write_bytes(b"fixture")
    return root, editor


def _project(tmp_path: Path, version: str) -> Path:
    path = tmp_path / "Demo" / "Demo.uproject"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"FileVersion": 3, "EngineAssociation": version}),
        encoding="utf-8",
    )
    return path


def _manifest() -> dict:
    return {
        "scenario": "network_replication",
        "clients": 2,
        "netMode": "listen_server",
        "topologyOwner": "automation_test",
        "automationFilter": "Demo.Network",
        "assertions": [
            {"id": "rpc_owner", "automationTest": "Demo.Network.RpcOwner"},
            {
                "id": "replicated_state",
                "automationTest": "Demo.Network.ReplicatedState",
            },
        ],
    }


@pytest.mark.parametrize("version", ["4.27", "5.4", "5.8", "5.10", "6.0"])
@pytest.mark.parametrize("platform", ["win32", "linux", "darwin"])
def test_plan_binds_exact_engine_across_supported_versions_and_hosts(
    tmp_path: Path,
    version: str,
    platform: str,
) -> None:
    engine, editor = _engine(tmp_path, version, platform)
    plan = build_runtime_verify_plan(
        _manifest(),
        project_file=_project(tmp_path, version),
        engine_root=engine,
        host_platform=platform,
    )

    assert plan["ok"] is True, plan["issues"]
    assert plan["environment"]["engineVersion"] == version
    assert plan["environment"]["editorCmd"] == str(editor.resolve())
    assert plan["environment"]["exactEngineBinding"] is True


def test_custom_engine_association_fails_closed_without_mapping_or_override(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.joinpath("config").mkdir(parents=True)
    fallback, _editor = _engine(tmp_path, "5.10", "linux", name="UE_5.10")
    workspace.joinpath("config", "workspace.json").write_text(
        json.dumps({"defaultEngineRoot": str(fallback)}),
        encoding="utf-8",
    )
    monkeypatch.setenv("SHARED_UNREAL_CONFIG", str(tmp_path / "missing-shared.json"))

    resolution = resolve_engine_root_for_association(
        "{SOURCE-BUILD-GUID}",
        workspace,
        host_platform="linux",
        environ={},
        home=tmp_path / "home",
    )

    assert resolution["ok"] is False
    assert resolution["errorCode"] == "ENGINE_ASSOCIATION_UNRESOLVED"
    assert resolution["engineRoot"] == ""


def test_custom_engine_association_uses_exact_workspace_mapping(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.joinpath("config").mkdir(parents=True)
    source_build, _editor = _engine(tmp_path, "5.10", "linux", name="SourceBuild")
    association = "source-build-guid"
    workspace.joinpath("config", "workspace.json").write_text(
        json.dumps({"engineRootsByAssociation": {association: str(source_build)}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("SHARED_UNREAL_CONFIG", str(tmp_path / "missing-shared.json"))

    resolution = resolve_engine_root_for_association(
        association,
        workspace,
        host_platform="linux",
        environ={},
        home=tmp_path / "home",
    )

    assert resolution["ok"] is True
    assert resolution["engineRoot"] == str(source_build.resolve())
    assert resolution["source"] == "config.engineRootsByAssociation"

    explicit = resolve_engine_root_for_association(
        association,
        workspace,
        explicit_engine_root=source_build,
        host_platform="linux",
        environ={},
        home=tmp_path / "home",
    )
    assert explicit["ok"] is True
    assert explicit["source"] == "argument"

    environment = resolve_engine_root_for_association(
        association,
        workspace,
        host_platform="linux",
        environ={"UNREAL_ENGINE_ROOT": str(source_build)},
        home=tmp_path / "home",
    )
    assert environment["ok"] is True
    assert environment["source"] == "environment"


def test_numeric_engine_association_discovers_ue4_without_version_cap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.joinpath("config").mkdir(parents=True)
    workspace.joinpath("config", "workspace.json").write_text("{}", encoding="utf-8")
    home = tmp_path / "home"
    engine, _editor = _engine(home / "Epic Games", "4.27", "linux")
    _engine(home / "Epic Games", "5.10", "linux")
    monkeypatch.setenv("SHARED_UNREAL_CONFIG", str(tmp_path / "missing-shared.json"))

    resolution = resolve_engine_root_for_association(
        "UE_4.27",
        workspace,
        host_platform="linux",
        environ={},
        home=home,
    )

    assert resolution["ok"] is True
    assert resolution["engineRoot"] == str(engine.resolve())
    assert resolution["source"] == "EngineAssociation"

    association_free = resolve_engine_root_for_association(
        "",
        workspace,
        explicit_engine_root=engine,
        host_platform="linux",
        environ={},
        home=home,
    )
    assert association_free["ok"] is True
    assert association_free["source"] == "argument"


def test_numeric_association_ignores_stale_install_time_environment_pin(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.joinpath("config").mkdir(parents=True)
    workspace.joinpath("config", "workspace.json").write_text("{}", encoding="utf-8")
    home = tmp_path / "home"
    expected, _editor = _engine(home / "Epic Games", "5.6", "linux")
    stale, _editor = _engine(home / "Epic Games", "5.8", "linux")
    monkeypatch.setenv("SHARED_UNREAL_CONFIG", str(tmp_path / "missing-shared.json"))

    resolution = resolve_engine_root_for_association(
        "5.6",
        workspace,
        host_platform="linux",
        environ={"UNREAL_ENGINE_ROOT": str(stale)},
        home=home,
    )

    assert resolution["ok"] is True
    assert resolution["engineRoot"] == str(expected.resolve())
    assert resolution["source"] == "EngineAssociation"


def test_runtime_verify_surfaces_unresolved_custom_association(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _project(tmp_path, "custom-source-build")
    monkeypatch.setenv("SHARED_UNREAL_CONFIG", str(tmp_path / "missing-shared.json"))
    monkeypatch.delenv("UNREAL_ENGINE_ROOT", raising=False)

    plan = build_runtime_verify_plan(
        _manifest(),
        project_file=project,
        host_platform="linux",
    )

    assert plan["ok"] is False
    assert plan["errorCode"] == "ENGINE_ASSOCIATION_UNRESOLVED"
    assert plan["environment"]["engineResolutionErrorCode"] == "ENGINE_ASSOCIATION_UNRESOLVED"
    assert any("ENGINE_ASSOCIATION_UNRESOLVED" in issue for issue in plan["issues"])


def test_plan_fails_closed_on_engine_mismatch_and_incomplete_network_contract(
    tmp_path: Path,
) -> None:
    engine, _editor = _engine(tmp_path, "5.8", "win32")
    manifest = _manifest()
    manifest["clients"] = 1
    manifest["topologyOwner"] = ""
    manifest["assertions"] = [
        {"id": "replicated_state", "automationTest": "Demo.Network.State"}
    ]
    plan = build_runtime_verify_plan(
        manifest,
        project_file=_project(tmp_path, "5.4"),
        engine_root=engine,
        host_platform="win32",
    )

    assert plan["ok"] is False
    assert any("exact engine binding" in issue for issue in plan["issues"])
    assert any("clients >= 2" in issue for issue in plan["issues"])
    assert any("topology" in issue for issue in plan["issues"])
    assert any("rpc_owner" in issue for issue in plan["issues"])


def test_execution_proves_each_exact_assertion_from_every_fresh_report(
    tmp_path: Path,
) -> None:
    engine, _editor = _engine(tmp_path, "5.8", "win32")
    plan = build_runtime_verify_plan(
        {**_manifest(), "soakIterations": 2},
        project_file=_project(tmp_path, "5.8"),
        engine_root=engine,
        host_platform="win32",
    )

    def runner(argv: list[str], **_kwargs):
        report_arg = next(item for item in argv if item.startswith("-ReportExportPath="))
        report_dir = Path(report_arg.split("=", 1)[1])
        report_dir.joinpath("index.json").write_text(
            json.dumps(
                {
                    "succeeded": 2,
                    "succeededWithWarnings": 0,
                    "failed": 0,
                    "notRun": 0,
                    "inProcess": 0,
                    "tests": [
                        {"fullTestPath": "Demo.Network.RpcOwner", "state": "Success"},
                        {
                            "fullTestPath": "Demo.Network.ReplicatedState",
                            "state": "Success",
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(argv, 0, stdout="queue complete", stderr="")

    result = run_runtime_verify_plan(plan, runner=runner)

    assert result["ok"] is True
    assert result["proofLevel"] == "RuntimeVerified"
    assert result["completedIterations"] == 2
    assert all(item["passed"] for item in result["assertionProof"])


def test_execution_does_not_equate_filter_success_with_missing_exact_assertion(
    tmp_path: Path,
) -> None:
    engine, _editor = _engine(tmp_path, "5.8", "win32")
    plan = build_runtime_verify_plan(
        _manifest(),
        project_file=_project(tmp_path, "5.8"),
        engine_root=engine,
        host_platform="win32",
    )

    def runner(argv: list[str], **_kwargs):
        report_arg = next(item for item in argv if item.startswith("-ReportExportPath="))
        report_dir = Path(report_arg.split("=", 1)[1])
        report_dir.joinpath("index.json").write_text(
            json.dumps(
                {
                    "succeeded": 1,
                    "succeededWithWarnings": 0,
                    "failed": 0,
                    "notRun": 0,
                    "inProcess": 0,
                    "tests": [
                        {"fullTestPath": "Demo.Network.RpcOwner", "state": "Success"}
                    ],
                }
            ),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(argv, 0, stdout="queue complete", stderr="")

    result = run_runtime_verify_plan(plan, runner=runner)

    assert result["ok"] is False
    assert result["errorCode"] == "RUNTIME_ASSERTION_FAILED"
    missing = next(
        item for item in result["assertionProof"] if item["id"] == "replicated_state"
    )
    assert missing["passed"] is False
    assert missing["iterations"][0]["executed"] is False


def test_public_runtime_verify_plan_preserves_exact_execute_binding(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("MCP_ESSENTIAL_TOOLS", "1")
    engine, editor = _engine(tmp_path, "5.8", "win32")
    project = _project(tmp_path, "5.8")
    server = McpServer(tmp_path / "missing.sqlite")
    sent: list[dict] = []
    server.send = sent.append

    server.handle_tool_call(
        700,
        {
            "name": "unreal_runtime_verify",
            "arguments": {
                "action": "plan",
                "manifest": _manifest(),
                "projectFile": str(project),
                "engineRoot": str(engine),
                "editorCmd": str(editor),
            },
        },
    )

    payload = sent[-1]["result"]["structuredContent"]
    assert payload["ok"] is True
    assert payload["nextAction"] == "unreal_runtime_verify"
    assert payload["nextActionArgs"]["projectFile"] == str(project)
    assert payload["nextActionArgs"]["engineRoot"] == str(engine)
    assert payload["nextActionArgs"]["editorCmd"] == str(editor)


def test_runtime_verify_execute_preserves_unresolved_engine_association_code(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MCP_ESSENTIAL_TOOLS", "1")
    monkeypatch.setenv("SHARED_UNREAL_CONFIG", str(tmp_path / "missing-shared.json"))
    monkeypatch.delenv("UNREAL_ENGINE_ROOT", raising=False)
    project = _project(tmp_path, "custom-source-build")
    server = McpServer(tmp_path / "missing.sqlite")
    sent: list[dict] = []
    server.send = sent.append

    server.handle_tool_call(
        701,
        {
            "name": "unreal_runtime_verify",
            "arguments": {
                "action": "execute",
                "manifest": _manifest(),
                "projectFile": str(project),
            },
        },
    )

    payload = sent[-1]["result"]["structuredContent"]
    assert payload["ok"] is False
    assert payload["errorCode"] == "ENGINE_ASSOCIATION_UNRESOLVED"


def test_project_status_does_not_probe_capabilities_with_another_engine(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _project(tmp_path, "custom-source-build")
    shared = tmp_path / "unreal-workspace.json"
    shared.write_text(json.dumps({"activeProject": str(project)}), encoding="utf-8")
    monkeypatch.setenv("SHARED_UNREAL_CONFIG", str(shared))
    monkeypatch.delenv("UNREAL_ENGINE_ROOT", raising=False)
    server = McpServer(tmp_path / "missing.sqlite")
    sent: list[dict] = []
    server.send = sent.append

    server.handle_tool_call(
        702,
        {"name": "unreal_project_status", "arguments": {}},
    )

    payload = sent[-1]["result"]["structuredContent"]
    assert payload["engineResolution"]["errorCode"] == "ENGINE_ASSOCIATION_UNRESOLVED"
    assert payload["capabilities"]["engineRoot"] == ""
    assert "ENGINE_ASSOCIATION_UNRESOLVED" in payload["blockingReasons"]
