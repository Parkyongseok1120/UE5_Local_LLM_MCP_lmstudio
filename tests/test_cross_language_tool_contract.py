from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from plan_consistency import RAG_ESSENTIAL_TOOLS  # noqa: E402
from tool_policy import exposure_inventory  # noqa: E402


def _node_essential_tools() -> set[str]:
    manifest = json.loads((ROOT / "config" / "stable_tool_manifest.json").read_text(encoding="utf-8-sig"))
    return set(manifest.get("agentEssential") or [])


def test_essential_tools_subset_of_inventory() -> None:
    inventory = exposure_inventory()
    essential = set(inventory["essentialProfile"])
    assert essential.issubset(set(RAG_ESSENTIAL_TOOLS))


def test_python_exposure_inventory_includes_hidden_tools() -> None:
    inventory = exposure_inventory()
    names = set(inventory.get("ragMcpTools") or [])
    for tool in (
        "unreal_task_start",
        "unreal_project_status",
        "unreal_job_log_read",
        "unreal_review_claim_validate",
        "unreal_project_architecture",
    ):
        assert tool in names


def test_node_syntax_check() -> None:
    for rel in ("src/server.js", "src/context-ux.js", "src/build-proof.js"):
        subprocess.run(
            ["node", "--check", str(ROOT / "lmstudio-unreal-agent-mcp" / rel)],
            check=True,
            cwd=ROOT,
        )


def test_node_build_proof_unit() -> None:
    result = subprocess.run(
        ["npm", "test"],
        cwd=ROOT / "lmstudio-unreal-agent-mcp",
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        shell=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_manifest_matches_node_essential_tools() -> None:
    manifest = json.loads((ROOT / "config" / "stable_tool_manifest.json").read_text(encoding="utf-8-sig"))
    exposure = (ROOT / "lmstudio-unreal-agent-mcp" / "src" / "tool-exposure.js").read_text(encoding="utf-8")
    assert "stable_tool_manifest.json" in exposure
    assert "agentEssential" in exposure
    assert set(manifest["agentEssential"]) == _node_essential_tools()
