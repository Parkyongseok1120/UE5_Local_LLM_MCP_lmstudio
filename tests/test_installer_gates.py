from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from conftest import powershell_prefix

ROOT = Path(__file__).resolve().parents[1]
INSTALLER_SUPPORT = ROOT / "scripts" / "installer_support"


def _run_ps1(script: str, *args: str) -> subprocess.CompletedProcess[str]:
    cmd = [
        *powershell_prefix(),
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


def _run_activation_status(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            *powershell_prefix(),
            "-File",
            str(ROOT / "scripts" / "Test-ContextCompactorActivation.ps1"),
            *args,
        ],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        timeout=120,
    )


def test_context_compactor_status_verifies_direct_source_layout() -> None:
    status = _run_activation_status()
    assert status.returncode == 0, status.stderr or status.stdout
    assert "Transparent context-compactor source layout verified" in status.stdout
    assert "top-level chat-plugin switch" in status.stdout
    assert "OFF" in status.stdout


def test_context_compactor_status_does_not_fabricate_runtime_activation() -> None:
    status = _run_activation_status("-Json", "-RequireRuntime")
    assert status.returncode == 3, status.stderr or status.stdout
    payload = json.loads(status.stdout)
    assert payload["sourceLayoutVerified"] is True
    assert payload["runtimeActivationProven"] is False
    assert payload["modelOwner"] == "lmstudio_selected_model"


def test_unreal_verifier_requires_and_hashes_the_direct_compactor_surface() -> None:
    verifier = (ROOT / "scripts" / "installer_support" / "Verify-UnrealMcp.ps1").read_text(
        encoding="utf-8"
    )
    for relative in (
        r"src\index.ts",
        r"src\prediction-loop.ts",
        r"src\direct-compaction-core.js",
        r"src\direct-config.ts",
    ):
        assert relative in verifier
    assert r"dist\prediction-loop.js" in verifier
    assert r"src\generator.ts" not in verifier
    assert r"src\compaction-core.js" not in verifier
    assert r"dist\generator.js" not in verifier
    assert "Select unreal-context-compactor as the chat model" not in verifier


def test_unreal_verifier_checks_current_direct_atomic_owners() -> None:
    verifier = (ROOT / "scripts" / "installer_support" / "Verify-UnrealMcp.ps1").read_text(
        encoding="utf-8"
    )
    for relative in (
        r"src\runtime-state-root.js",
        "direct-edit-bundle.js",
        "direct-transaction-recovery.js",
        "direct-transaction-store.js",
        "direct-static-validation.js",
    ):
        assert relative in verifier
    assert r"src\state-root.js" not in verifier
    assert r"src\validate-write.js" not in verifier


def test_unreal_verifier_uses_a_real_request_variable_and_no_archived_python_state_root() -> None:
    verifier = (ROOT / "scripts" / "installer_support" / "Verify-UnrealMcp.ps1").read_text(
        encoding="utf-8"
    )

    assert "$requests =" in verifier
    assert "$input =" not in verifier
    assert "scripts\\state_root.py" not in verifier


def test_unreal_verifier_requires_independent_direct_and_agent_state_roots() -> None:
    verifier = (ROOT / "scripts" / "installer_support" / "Verify-UnrealMcp.ps1").read_text(
        encoding="utf-8"
    )

    assert 'Check "mcp.json Direct RAG state root"' in verifier
    assert (
        '$cfg.mcpServers."unreal-rag".env.DIRECT_RAG_STATE_ROOT'
        in verifier
    )
    assert "unreal-rag missing DIRECT_RAG_STATE_ROOT" in verifier
    assert 'Check "mcp.json agent state root"' in verifier
    assert '$cfg.mcpServers."unreal-agent".env.AGENT_STATE_ROOT' in verifier
    assert "unreal-agent missing AGENT_STATE_ROOT" in verifier
    assert "mcp.json AGENT_STATE_ROOT parity" not in verifier
    assert '$cfg.mcpServers."unreal-rag".env.AGENT_STATE_ROOT' not in verifier
    assert "AGENT_STATE_ROOT mismatch" not in verifier


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
        [*powershell_prefix(), "-Command", command],
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
