from __future__ import annotations

import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "installer"


def _run_ps1(script: str, *args: str) -> subprocess.CompletedProcess[str]:
    cmd = [
        "powershell",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(INSTALLER / script),
        *args,
    ]
    return subprocess.run(cmd, capture_output=True, text=True, cwd=str(ROOT), timeout=120)


def test_resolve_rag_index_path_unreal57(tmp_path: Path) -> None:
    rag_root = tmp_path / "rag"
    (rag_root / "config").mkdir(parents=True)
    (rag_root / "config" / "workspace.json").write_text(
        json.dumps({"indexNamespace": "unreal57", "indexPath": "data/unreal57/rag.sqlite"}),
        encoding="utf-8",
    )
    ps = _run_ps1(
        "Test-ResolveRagIndexPath.ps1",
        "-RagRoot",
        str(rag_root),
    )
    if ps.returncode == 2:
        pytest.skip("PowerShell helper script unavailable")
    assert ps.returncode == 0, ps.stderr or ps.stdout
    assert "unreal57" in ps.stdout


def test_main_install_whatif_zero_mutations(tmp_path: Path) -> None:
    lm_home = tmp_path / "lmstudio"
    lm_home.mkdir()
    marker = tmp_path / "probe.txt"
    marker.write_text("before", encoding="utf-8")
    before = {p.name for p in tmp_path.iterdir()}
    ps = _run_ps1(
        "Install-UnrealMcp.ps1",
        "-WhatIf",
        "-SkipNpm",
        "-SkipPythonDeps",
        "-LmStudioHome",
        str(lm_home),
        "-PortableRoot",
        str(ROOT),
    )
    assert ps.returncode == 0, ps.stderr or ps.stdout
    assert "Would install LM Studio plugin" in ps.stdout
    assert "Installation file available: Y" in ps.stdout
    after = {p.name for p in tmp_path.iterdir()}
    assert before == after
    assert marker.read_text(encoding="utf-8") == "before"


def test_cline_install_whatif_zero_mutations(tmp_path: Path) -> None:
    lm_home = tmp_path / "lmstudio"
    (lm_home / "config").mkdir(parents=True)
    (lm_home / "config" / "unreal-workspace.json").write_text("{}", encoding="utf-8")
    probe_dir = tmp_path / "cline-probe"
    probe_dir.mkdir()
    (probe_dir / "keep.txt").write_text("ok", encoding="utf-8")
    before_count = sum(1 for _ in probe_dir.rglob("*"))
    ps = _run_ps1(
        "Install-ClineUnrealMcp.ps1",
        "-WhatIf",
        "-PortableRoot",
        str(ROOT),
        "-LmStudioHome",
        str(lm_home),
    )
    assert ps.returncode == 0, ps.stderr or ps.stdout
    after_count = sum(1 for _ in probe_dir.rglob("*"))
    assert before_count == after_count


def test_assert_safe_package_path_rejects_repo_root() -> None:
    ps = _run_ps1(
        "Test-AssertSafePackagePath.ps1",
        "-Path",
        str(ROOT),
        "-ExpectFailure",
    )
    assert ps.returncode == 0, ps.stderr or ps.stdout
    assert "TEMP" in ps.stdout or "Unsafe" in ps.stdout or "source root" in ps.stdout


def test_portable_package_content_scan_passes_slim_build(tmp_path: Path) -> None:
    out_dir = tmp_path / "portable"
    zip_path = tmp_path / "portable.zip"
    build = _run_ps1(
        "Build-PortablePackage.ps1",
        "-OutputDir",
        str(out_dir),
        "-ZipPath",
        str(zip_path),
        "-SourceRoot",
        str(ROOT),
    )
    assert build.returncode == 0, build.stderr or build.stdout
    scan = _run_ps1("Test-PortablePackageContents.ps1", "-ZipPath", str(zip_path))
    assert scan.returncode == 0, scan.stderr or scan.stdout


def test_portable_package_scan_ignores_dependency_server_entries(tmp_path: Path) -> None:
    zip_path = tmp_path / "full-layout.zip"
    entries = {
        "package/lmstudio-unreal-agent-mcp/src/server.js": "module.exports = {};\n",
        "package/lmstudio-unreal-agent-mcp/node_modules/example/server.js": "module.exports = {};\n",
        "package/Unreal58-RAG/lmstudio-context-compactor-plugin/manifest.json": "{}\n",
        "package/Unreal58-RAG/lmstudio-context-compactor-plugin/package.json": "{}\n",
        "package/Unreal58-RAG/lmstudio-context-compactor-plugin/src/generator.ts": "export {};\n",
        "package/Unreal58-RAG/scripts/install_context_compactor.ps1": "Write-Host ok\n",
        "package/Unreal58-RAG/scripts/Test-ContextCompactorActivation.ps1": "Write-Host ok\n",
        "package/Unreal58-RAG/installer/INSTALL-AGENT-MODE.bat": "@echo off\n",
    }
    with zipfile.ZipFile(zip_path, "w") as archive:
        for name, content in entries.items():
            archive.writestr(name, content)

    scan = _run_ps1("Test-PortablePackageContents.ps1", "-ZipPath", str(zip_path))
    assert scan.returncode == 0, scan.stderr or scan.stdout


def _run_activation_status(state_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(ROOT / "scripts" / "Test-ContextCompactorActivation.ps1"),
            "-StateRoot",
            str(state_root),
            *args,
        ],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        timeout=120,
    )


def test_context_compactor_status_rejects_install_only_state(tmp_path: Path) -> None:
    status = _run_activation_status(tmp_path / "missing")
    assert status.returncode == 2
    assert "No context compactor proxy activation evidence" in status.stdout
    assert "underlying Qwen/GPT model directly bypasses" in status.stdout


def test_context_compactor_status_reports_runtime_route_and_compaction(tmp_path: Path) -> None:
    session = tmp_path / "session-a"
    session.mkdir()
    events = [
        {
            "type": "context_measurement",
            "at": "2026-07-17T03:00:00.000Z",
            "proxyActive": True,
            "targetModel": "test-qwen",
            "inputTokens": 65000,
            "contextLength": 70656,
            "decision": {"action": "hard_compact"},
        },
        {
            "type": "compaction_decision",
            "at": "2026-07-17T03:00:01.000Z",
            "applied": True,
            "postRemainingTokens": 21000,
        },
    ]
    (session / "events.jsonl").write_text(
        "".join(json.dumps(event) + "\n" for event in events),
        encoding="utf-8",
    )

    status = _run_activation_status(tmp_path, "-Json", "-RequireCompaction")
    assert status.returncode == 0, status.stderr or status.stdout
    payload = json.loads(status.stdout)
    assert payload["active"] is True
    assert payload["targetModel"] == "test-qwen"
    assert payload["compactionApplied"] is True
    assert payload["postRemainingTokens"] == 21000


def test_sync_shared_workspace_drops_paths_from_another_pc(tmp_path: Path) -> None:
    valid_root = tmp_path / "current-pc-projects"
    valid_root.mkdir()
    stale_root = tmp_path / "old-pc-projects"
    stale_project = stale_root / "OldMachineGame.uproject"
    shared_config = tmp_path / "unreal-workspace.json"
    shared_config.write_text(
        json.dumps(
            {
                "activeProject": str(stale_project),
                "projectSearchRoots": [str(stale_root), str(valid_root)],
                "defaultEngineRoot": "Z:/OldPc/UE_Custom",
            }
        ),
        encoding="utf-8",
    )

    helper = INSTALLER / "Install-PathHelpers.ps1"
    command = (
        f". '{helper}'; "
        f"Sync-SharedWorkspaceEngine -SharedConfigPath '{shared_config}' "
        "-EngineRoot '' | Out-Null"
    )
    ps = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        timeout=120,
    )
    assert ps.returncode == 0, ps.stderr or ps.stdout

    synced = json.loads(shared_config.read_text(encoding="utf-8-sig"))
    assert synced["activeProject"] is None
    assert synced["defaultEngineRoot"] == ""
    assert synced["projectSearchRoots"] == [str(valid_root.resolve())]
