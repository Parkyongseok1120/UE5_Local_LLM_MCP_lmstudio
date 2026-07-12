from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from task_api import task_approve, task_root, task_start  # noqa: E402


def test_task_approve_rejects_terminal_task(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    workspace = tmp_path
    started = task_start(workspace, request="demo", start_background_job=False)
    task_id = str(started["taskSessionId"])
    state_path = task_root(workspace, task_id) / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["status"] = "completed"
    state_path.write_text(json.dumps(state), encoding="utf-8")

    result = task_approve(workspace, task_id, note="too late")
    assert result["ok"] is False
