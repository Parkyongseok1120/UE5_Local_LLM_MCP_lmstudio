from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
AGENT = ROOT / "lmstudio-unreal-agent-mcp"


def test_python_node_state_root_match_via_env(tmp_path: Path, monkeypatch) -> None:
    shared = tmp_path / "config" / "unreal-workspace.json"
    shared.parent.mkdir(parents=True)
    shared.write_text("{}", encoding="utf-8")
    state_root = tmp_path / "state" / "unreal-agent"
    monkeypatch.setenv("SHARED_UNREAL_CONFIG", str(shared))
    monkeypatch.setenv("AGENT_STATE_ROOT", str(state_root))

    sys.path.insert(0, str(SCRIPTS))
    from state_root import resolve_agent_state_root  # noqa: E402

    py_root = resolve_agent_state_root(Path("/ignored/workspace"))

    proc = subprocess.run(
        [
            "node",
            "-e",
            "const { resolveAgentStateRoot } = require('./src/state-root');"
            "console.log(resolveAgentStateRoot('/ignored/workspace'));",
        ],
        cwd=str(AGENT),
        env={**os.environ, "SHARED_UNREAL_CONFIG": str(shared), "AGENT_STATE_ROOT": str(state_root)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    node_root = Path(proc.stdout.strip()).resolve()
    assert py_root.resolve() == node_root


def test_python_state_root_ignores_workspace_without_env(tmp_path: Path, monkeypatch) -> None:
    shared = tmp_path / "config" / "unreal-workspace.json"
    shared.parent.mkdir(parents=True)
    shared.write_text("{}", encoding="utf-8")
    monkeypatch.delenv("AGENT_STATE_ROOT", raising=False)
    monkeypatch.setenv("SHARED_UNREAL_CONFIG", str(shared))

    sys.path.insert(0, str(SCRIPTS))
    from state_root import resolve_agent_state_root  # noqa: E402

    expected = (tmp_path / "state" / "unreal-agent").resolve()
    assert resolve_agent_state_root(tmp_path / "repo").resolve() == expected


def test_build_cline_mcp_config_includes_agent_state_root(tmp_path: Path) -> None:
    ps = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            (
                    f". '{ROOT / 'scripts' / 'installer_support' / 'Install-PathHelpers.ps1'}'; "
                f"$cfg = Build-ClineMcpConfig "
                f"-RagRoot '{tmp_path / 'rag'}' "
                f"-AgentRoot '{tmp_path / 'agent'}' "
                f"-DocumentsRoot '{tmp_path / 'docs'}' "
                f"-SharedConfigPath '{tmp_path / 'lm' / 'config' / 'unreal-workspace.json'}' "
                f"-AgentConfigPath '{tmp_path / 'agent' / 'config' / 'agent-mcp.json'}' "
                f"-PythonExe 'python' -NodeExe 'node' -PortableRoot '{tmp_path}' "
                f"-AgentStateRoot '{tmp_path / 'lm' / 'state' / 'unreal-agent'}'; "
                f"$cfg | ConvertTo-Json -Depth 20"
            ),
        ],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        timeout=60,
    )
    if ps.returncode != 0:
        pytest.skip(f"PowerShell unavailable: {ps.stderr or ps.stdout}")
    payload = json.loads(ps.stdout)
    rag_env = payload["mcpServers"]["unreal-rag"]["env"]
    agent_env = payload["mcpServers"]["unreal-agent"]["env"]
    assert rag_env["AGENT_STATE_ROOT"] == agent_env["AGENT_STATE_ROOT"]
