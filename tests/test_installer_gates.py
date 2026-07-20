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

    helper = INSTALLER_SUPPORT / "Install-PathHelpers.ps1"
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
