from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from direct_rag_contract import direct_rag_tool_definitions  # noqa: E402


def _manifest() -> dict:
    return json.loads(
        (ROOT / "config" / "stable_tool_manifest.json").read_text(encoding="utf-8-sig")
    )


def test_default_rag_catalog_matches_direct_manifest(tmp_path: Path) -> None:
    del tmp_path
    definitions = direct_rag_tool_definitions()
    names = {item["name"] for item in definitions}
    assert names == set(_manifest()["ragEssential"])
    assert len(names) == 8
    assert not any(name.startswith("unreal_task_") for name in names)
    assert all("taskAuthorization" not in json.dumps(item) for item in definitions)


def test_default_node_catalog_matches_direct_manifest() -> None:
    node = shutil.which("node")
    assert node is not None
    script = (
        "const {toolDefinitions}=require('./src/direct-server.js');"
        "process.stdout.write(JSON.stringify(toolDefinitions().map(x=>x.name)));"
    )
    result = subprocess.run(
        [node, "-e", script],
        cwd=ROOT / "lmstudio-unreal-agent-mcp",
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        shell=False,
        check=True,
    )
    names = set(json.loads(result.stdout))
    assert names == set(_manifest()["agentEssential"])
    assert len(names) == 20
    assert not names.intersection(
        {"list_active_tasks", "cancel_active_task", "write_session_handoff"}
    )


def test_node_syntax_check() -> None:
    for rel in (
        "src/direct-server.js",
        "src/direct-tool-catalog.js",
        "src/direct-runtime-shared.js",
        "src/direct-runtime-context.js",
        "src/direct-file-snapshot.js",
        "src/direct-project-capabilities.js",
        "src/direct-read-capabilities.js",
        "src/direct-log-capabilities.js",
        "src/direct-mutation-capabilities.js",
        "src/direct-diagnostic-capabilities.js",
        "src/direct-response.js",
        "src/direct-repeat-cache.js",
        "src/direct-build-response.js",
        "src/strict-server.js",
        "src/strict-lifecycle.js",
        "src/build-proof.js",
    ):
        subprocess.run(
            ["node", "--check", str(ROOT / "lmstudio-unreal-agent-mcp" / rel)],
            check=True,
            cwd=ROOT,
        )


def test_node_direct_and_safety_units() -> None:
    node = shutil.which("node")
    assert node is not None
    command = [node, "test/run-tests.js"]
    result = subprocess.run(
        command,
        cwd=ROOT / "lmstudio-unreal-agent-mcp",
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        shell=False,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
