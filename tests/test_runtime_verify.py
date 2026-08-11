from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from runtime_verify import build_runtime_verify_plan, run_runtime_verify_plan  # noqa: E402
from unreal_rag_mcp import McpServer  # noqa: E402


def _engine(tmp_path: Path, version: str, platform: str) -> tuple[Path, Path]:
    root = tmp_path / f"UE_{version}"
    build = root / "Engine" / "Build"
    build.mkdir(parents=True)
    build.joinpath("Build.version").write_text(
        json.dumps({"MajorVersion": 5, "MinorVersion": int(version.split(".")[1])}),
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


@pytest.mark.parametrize("version", ["5.4", "5.5", "5.6", "5.7", "5.8"])
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
