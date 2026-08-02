from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from task_api import (  # noqa: E402
    active_task_route_context,
    authorize_active_task_tool,
    task_list_active,
    task_resolve_active_session_id,
)


def _write_route_less_task(
    state_root: Path,
    workspace: Path,
    *,
    task_id: str,
    project_file: str,
    mode: str = "agent_edit",
) -> None:
    task_dir = state_root / "tasks" / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "workspace-root.txt").write_text(str(workspace.resolve()), encoding="utf-8")
    (task_dir / "route-scope.json").write_text(
        json.dumps(
            {
                "workspaceRoot": str(workspace.resolve()),
                "projectFile": project_file,
            }
        ),
        encoding="utf-8",
    )
    (task_dir / "state.json").write_text(
        json.dumps(
            {
                "taskSessionId": task_id,
                "status": "running",
                "mode": mode,
                "request": "orphan without toolRoute",
                "planId": "orphan-plan",
                "planRevision": "1",
                "projectFile": project_file,
                "workspaceRoot": str(workspace.resolve()),
                "routeScope": {
                    "workspaceRoot": str(workspace.resolve()),
                    "projectFile": project_file,
                },
                "writesAllowed": True,
            }
        ),
        encoding="utf-8",
    )


def test_route_less_orphan_does_not_block_route_context(tmp_path: Path, monkeypatch) -> None:
    state_root = tmp_path / "state"
    monkeypatch.setenv("AGENT_STATE_ROOT", str(state_root))
    project = str((tmp_path / "Demo.uproject").resolve())
    (tmp_path / "Demo.uproject").write_text("{}", encoding="utf-8")
    _write_route_less_task(
        state_root,
        tmp_path,
        task_id="orphanroute01",
        project_file=project,
        mode="plan_only",
    )

    context = active_task_route_context(
        tmp_path,
        active_project=project,
        require_owner_capability=True,
    )
    assert context["status"] == "none"

    for tool in ("list_directory", "unreal_agent_plan", "replace_in_file", "write_file"):
        auth = authorize_active_task_tool(
            tmp_path,
            tool_name=tool,
            active_project=project,
        )
        assert auth.get("ok") is True, tool
        assert auth.get("legacy") is True, tool


def test_cancel_resolve_targets_single_route_less_orphan(tmp_path: Path, monkeypatch) -> None:
    state_root = tmp_path / "state"
    monkeypatch.setenv("AGENT_STATE_ROOT", str(state_root))
    project = str((tmp_path / "Demo.uproject").resolve())
    (tmp_path / "Demo.uproject").write_text("{}", encoding="utf-8")
    _write_route_less_task(
        state_root,
        tmp_path,
        task_id="orphanroute02",
        project_file=project,
    )

    listed = task_list_active(tmp_path, active_project=project)
    assert listed["runningCount"] == 1
    assert listed["tasks"][0]["routeMissing"] is True

    resolved = task_resolve_active_session_id(tmp_path, active_project=project)
    assert resolved.get("ok") is True
    assert resolved.get("taskSessionId") == "orphanroute02"


def test_cancel_resolve_ambiguous_for_multiple_route_less_orphans(
    tmp_path: Path, monkeypatch
) -> None:
    state_root = tmp_path / "state"
    monkeypatch.setenv("AGENT_STATE_ROOT", str(state_root))
    project = str((tmp_path / "Demo.uproject").resolve())
    (tmp_path / "Demo.uproject").write_text("{}", encoding="utf-8")
    _write_route_less_task(
        state_root, tmp_path, task_id="orphanroute03", project_file=project
    )
    _write_route_less_task(
        state_root, tmp_path, task_id="orphanroute04", project_file=project
    )

    resolved = task_resolve_active_session_id(tmp_path, active_project=project)
    assert resolved.get("ok") is False
    assert resolved.get("errorCode") == "TASK_AMBIGUOUS_ACTIVE"
    assert len(resolved.get("tasks") or []) == 2
