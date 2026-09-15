from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_intel_mac_installer_dry_run(tmp_path: Path) -> None:
    project = tmp_path / "My Game"
    engine = tmp_path / "UE 5.7"
    project.mkdir()
    engine.mkdir()
    uproject = project / "MyGame.uproject"
    uproject.write_text("{}\n", encoding="utf-8")
    env = os.environ.copy()
    env["INTEL_MAC_INSTALLER_TEST"] = "1"

    completed = subprocess.run(
        [
            "bash",
            str(ROOT / "install-intel-mac.sh"),
            "--project",
            str(uproject),
            "--engine-root",
            str(engine),
            "--dry-run",
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert str(uproject) in completed.stdout
    assert str(engine) in completed.stdout
    assert "Lima -> VM -> llmster/login -> LM Link -> MCP/RAG -> server -> checks" in completed.stdout


def test_intel_mac_installer_preserves_headless_and_safety_contracts() -> None:
    installer = (ROOT / "install-intel-mac.sh").read_text(encoding="utf-8")
    launcher = (ROOT / "scripts" / "lmstudio-headless-cli.sh").read_text(encoding="utf-8")
    client = (ROOT / "scripts" / "headless_mcp_chat.py").read_text(encoding="utf-8")
    compactor = (ROOT / "scripts" / "headless_compact.js").read_text(encoding="utf-8")

    assert "--headless-lmlink" in installer
    assert "--enable-agent-mode" not in installer
    assert "writable: false" in installer
    assert "lms\" link enable" in installer
    assert "unreal_rag_health" in installer
    assert "--list-tools" in launcher
    assert '"tool_choice": "auto"' in client
    assert '"stream": stream' in client
    assert "post_chat_stream" in client
    assert "tools/call" in client
    assert "compact_in_background" in client
    assert "direct-compaction-core.js" in compactor


def test_headless_compactor_runs_silently_and_keeps_recent_turns() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is unavailable")
    messages: list[dict[str, str]] = []
    for index in range(15):
        messages.extend(
            [
                {"role": "user", "content": f"request {index}"},
                {"role": "assistant", "content": f"answer {index}"},
            ]
        )
    completed = subprocess.run(
        [node, str(ROOT / "scripts" / "headless_compact.js")],
        input=json.dumps(
            {
                "messages": messages,
                "measurement": {"exact": False, "messageCount": len(messages), "remainingTokens": 5000},
                "options": {"compactAboveMessageCount": 24},
            }
        ),
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    result = json.loads(completed.stdout)

    assert completed.returncode == 0, completed.stderr
    assert result["compacted"] is True
    assert result["omittedMessageCount"] > 0
    assert result["messages"][-1]["content"] == "answer 14"
    assert completed.stderr == ""
