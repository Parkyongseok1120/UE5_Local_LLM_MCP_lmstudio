"""Removal regressions for server-owned synthesis readiness and finality.

The selected model owns stopping and the final response in Direct Model Mode.
These tests intentionally prove absence of the historical Node controller rather
than preserving its readiness/latch behavior.
"""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_default_node_server_has_no_synthesis_readiness_owner() -> None:
    removed_owner = (
        ROOT
        / "lmstudio-unreal-agent-mcp"
        / "src"
        / "synthesis-readiness.js"
    )
    direct_entry = (
        ROOT / "lmstudio-unreal-agent-mcp" / "src" / "direct-server.js"
    ).read_text(encoding="utf-8")

    assert removed_owner.exists() is False
    assert "synthesis-readiness" not in direct_entry
    assert "commitSynthesis" not in direct_entry
    assert "synthesisLatch" not in direct_entry


def test_direct_catalog_exposes_no_synthesis_lifecycle_tools() -> None:
    manifest = json.loads(
        (ROOT / "config" / "stable_tool_manifest.json").read_text(
            encoding="utf-8-sig"
        )
    )
    direct_tools = manifest["ragEssential"] + manifest["agentEssential"]

    assert not any("synthesis" in name.casefold() for name in direct_tools)
    assert "unreal_task_commit_synthesis" not in direct_tools
    assert "unreal_task_ack_synthesis_delivery" not in direct_tools


def test_compactor_forwards_to_selected_model_without_finality_controller() -> None:
    plugin_root = ROOT / "lmstudio-context-compactor-plugin"
    entry = (plugin_root / ".lmstudio" / "entry.ts").read_text(encoding="utf-8")
    orchestration = (plugin_root / "src" / "prediction-loop.ts").read_text(
        encoding="utf-8"
    )
    round_loop = (plugin_root / "src" / "round-loop.ts").read_text(
        encoding="utf-8"
    )
    direct_runtime = f"{orchestration}\n{round_loop}"

    assert "tokenSource.act(" in round_loop
    assert "startToolUseSession()" in orchestration
    assert "withGenerator() { throw" in entry
    for removed_owner in (
        "synthesis-readiness",
        "task-auth",
        "route-recovery-policy",
        "requiredNextTool",
    ):
        assert removed_owner not in direct_runtime
