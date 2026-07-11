from __future__ import annotations

import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "lmstudio-unreal-agent-mcp" / "src" / "bootstrap-cache.js"


def run_node(expression: str) -> object:
    script = (
        f"const cache = require({json.dumps(str(MODULE))});"
        f"const result = ({expression});"
        "process.stdout.write(JSON.stringify(result));"
    )
    proc = subprocess.run(
        ["node", "-e", script],
        cwd=ROOT,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
    return json.loads(proc.stdout)


def test_evaluate_without_active_project_never_skips() -> None:
    payload = run_node(
        "cache.evaluateBootstrapCache({"
        "projectPath:'C:/A/A.uproject',"
        "stepsCompleted:['unreal_get_active_project','unreal_rag_health','get_workspace_info'],"
        "ragHealthOk:true"
        "}, null)"
    )
    assert payload["canSkipSteps"] is False


def test_project_switch_resets_steps_and_health() -> None:
    existing = {
        "projectPath": "C:/A/A.uproject",
        "ragHealthOk": True,
        "workspaceHash": "hash-a",
        "stepsCompleted": ["unreal_get_active_project", "unreal_rag_health", "get_workspace_info"],
    }
    merged = run_node(
        f"cache.mergeBootstrapCache({json.dumps(existing)}, "
        "{projectPath:'C:/B/B.uproject', stepsCompleted:['get_workspace_info']})"
    )
    assert merged["projectPath"] == "C:/B/B.uproject"
    assert merged["stepsCompleted"] == ["get_workspace_info"]
    assert merged["ragHealthOk"] is False


def test_same_project_merges_steps() -> None:
    existing = {
        "projectPath": "C:/A/A.uproject",
        "ragHealthOk": False,
        "workspaceHash": "hash-a",
        "stepsCompleted": ["get_workspace_info"],
    }
    merged = run_node(
        f"cache.mergeBootstrapCache({json.dumps(existing)}, "
        "{projectPath:'C:/A/A.uproject', stepsCompleted:['unreal_rag_health'], ragHealthOk:true})"
    )
    assert set(merged["stepsCompleted"]) == {"get_workspace_info", "unreal_rag_health"}
    assert merged["ragHealthOk"] is True
