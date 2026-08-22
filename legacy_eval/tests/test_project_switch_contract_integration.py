"""Integration contract tests for name-based project switching and task resume."""

from __future__ import annotations

import copy
import importlib.util
import json
import sys
import time
from pathlib import Path
from typing import Any

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))


class _FakePlan:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def to_dict(self) -> dict[str, Any]:
        return copy.deepcopy(self._payload)


def _load_rag_mcp_module() -> Any:
    path = SCRIPTS / "unreal_rag_mcp.py"
    spec = importlib.util.spec_from_file_location(
        "unreal_rag_mcp_project_switch_contract",
        path,
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class _ContractHarness:
    def __init__(
        self,
        *,
        server: Any,
        current_project: Path,
        target_project: Path,
        other_project: Path,
        config: dict[str, Any],
        resolution: dict[str, Any],
        calls: dict[str, list[Any]],
    ) -> None:
        self.server = server
        self.current_project = current_project
        self.target_project = target_project
        self.other_project = other_project
        self.config = config
        self.resolution = resolution
        self.calls = calls
        self.sent: list[dict[str, Any]] = []
        self._message_id = 8000
        self.server.send = self.sent.append
        self.server.notify_tools_list_changed = lambda: None

    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self._message_id += 1
        message_id = self._message_id
        start = len(self.sent)
        self.server.handle_tool_call(
            message_id,
            {"name": name, "arguments": copy.deepcopy(arguments)},
        )
        replies = [
            item
            for item in self.sent[start:]
            if item.get("id") == message_id
        ]
        assert len(replies) == 1, self.sent[start:]
        result = replies[0].get("result") or {}
        assert isinstance(result.get("structuredContent"), dict), result
        return result["structuredContent"]


@pytest.fixture
def contract_harness(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> _ContractHarness:
    monkeypatch.setenv("MCP_EXECUTION_MODE", "strict")
    monkeypatch.setenv("MCP_ESSENTIAL_TOOLS", "1")
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    monkeypatch.delenv("MCP_REQUIRE_CONTEXT_COMPACTOR_ACTIVE", raising=False)
    monkeypatch.delenv("MCP_CONTEXT_COMPACTOR_ADVISORY", raising=False)

    current_project = tmp_path / "current owner" / "Current_Project.uproject"
    target_project = tmp_path / "target owner" / "Target_Game.uproject"
    other_project = tmp_path / "other owner" / "Other_Game.uproject"
    for project in (current_project, target_project, other_project):
        project.parent.mkdir(parents=True, exist_ok=True)
        project.write_text("{}", encoding="utf-8")

    shared_config = tmp_path / "unreal-workspace.json"
    config: dict[str, Any] = {
        "activeProject": str(current_project),
        "projectSearchRoots": [str(tmp_path)],
    }

    def persist_config() -> None:
        shared_config.write_text(json.dumps(config), encoding="utf-8")

    persist_config()
    monkeypatch.setenv("SHARED_UNREAL_CONFIG", str(shared_config))

    module = _load_rag_mcp_module()

    import agent_orchestrator
    import project_controller
    import project_name_resolver
    import project_switch_invalidate
    import task_api

    calls: dict[str, list[Any]] = {
        "resolver": [],
        "set": [],
        "planner": [],
        "task": [],
    }
    resolution: dict[str, Any] = {
        "ok": False,
        "errorCode": "PROJECT_NAME_NOT_FOUND",
        "error": "No exact project match.",
        "suggestions": [],
    }

    monkeypatch.setattr(module, "load_shared_config", lambda: dict(config))
    monkeypatch.setattr(
        module,
        "resolve_active_project_context",
        lambda: {
            "ok": bool(config.get("activeProject")),
            "activeProject": config.get("activeProject"),
            "projectName": (
                Path(str(config["activeProject"])).stem
                if config.get("activeProject")
                else None
            ),
        },
    )
    monkeypatch.setattr(
        module,
        "active_project_names",
        lambda: (
            [
                Path(str(config["activeProject"])).stem,
                Path(str(config["activeProject"])).parent.name,
            ]
            if config.get("activeProject")
            else []
        ),
    )

    def resolve_project_name(workspace: Path, target: str, **kwargs: Any) -> dict[str, Any]:
        calls["resolver"].append(
            {"workspace": Path(workspace), "target": target, "kwargs": kwargs}
        )
        return copy.deepcopy(resolution)

    def build_agent_plan(
        request: str,
        mode: str,
        **kwargs: Any,
    ) -> _FakePlan:
        calls["planner"].append(
            {"request": request, "mode": mode, "kwargs": copy.deepcopy(kwargs)}
        )
        return _FakePlan(
            {
                "ok": True,
                "request": request,
                "taskKind": "inspect_only",
                "editStrategy": "no_edit",
                "writeGate": {"writesAllowed": False},
                "orchestration": {
                    "strategy": "bounded_inspection",
                    "requiredBeforeWrite": [],
                    "taskSessionRequired": True,
                },
            }
        )

    def task_start(workspace: Path, **kwargs: Any) -> dict[str, Any]:
        calls["task"].append(
            {"kind": "start", "workspace": Path(workspace), "kwargs": copy.deepcopy(kwargs)}
        )
        return {
            "ok": True,
            "state": {},
            "taskAuthorization": {},
            "toolRoute": {"activeTools": [], "pendingGates": [], "maxFilesPerSlice": 2},
        }

    def task_replan(workspace: Path, **kwargs: Any) -> dict[str, Any]:
        calls["task"].append(
            {"kind": "replan", "workspace": Path(workspace), "kwargs": copy.deepcopy(kwargs)}
        )
        return {
            "ok": True,
            "state": {},
            "taskAuthorization": {},
            "toolRoute": {"activeTools": [], "pendingGates": [], "maxFilesPerSlice": 2},
        }

    def switch_active_project(
        workspace: Path,
        *,
        project_path: str = "",
        clear: bool = False,
        **kwargs: Any,
    ) -> dict[str, Any]:
        calls["set"].append(
            {
                "workspace": Path(workspace),
                "projectPath": project_path,
                "clear": clear,
                "kwargs": copy.deepcopy(kwargs),
            }
        )
        resolved_project = None if clear else str(Path(project_path).resolve())
        already_active = bool(
            not clear
            and config.get("activeProject")
            and str(Path(str(config["activeProject"])).resolve()) == resolved_project
        )
        config["activeProject"] = resolved_project
        persist_config()
        return {
            "ok": True,
            "activeProject": config["activeProject"],
            "switchResult": (
                "cleared" if clear else "already_active" if already_active else "switched"
            ),
            "changed": not already_active,
            "cacheInvalidation": {"cacheGeneration": 7},
            "cacheRefreshRequired": False,
        }

    monkeypatch.setattr(project_name_resolver, "resolve_project_name", resolve_project_name)
    monkeypatch.setattr(agent_orchestrator, "build_agent_plan", build_agent_plan)
    monkeypatch.setattr(project_controller, "switch_active_project", switch_active_project)
    monkeypatch.setattr(task_api, "task_start", task_start)
    monkeypatch.setattr(task_api, "task_replan", task_replan)
    monkeypatch.setattr(
        task_api,
        "release_expired_idle_active_task_route",
        lambda *args, **kwargs: {"ok": True},
    )
    monkeypatch.setattr(project_switch_invalidate, "read_cache_generation", lambda workspace: 7)

    server = module.McpServer(tmp_path / "missing.sqlite")
    server.workspace = tmp_path
    return _ContractHarness(
        server=server,
        current_project=current_project,
        target_project=target_project,
        other_project=other_project,
        config=config,
        resolution=resolution,
        calls=calls,
    )


def _plan(harness: _ContractHarness, request: str, **arguments: Any) -> dict[str, Any]:
    return harness.call(
        "unreal_agent_plan",
        {"request": request, **arguments},
    )


def _set_unique_resolution(harness: _ContractHarness) -> None:
    harness.resolution.clear()
    harness.resolution.update(
        {
            "ok": True,
            "selected": {
                "projectName": harness.target_project.stem,
                "projectFile": harness.target_project.name,
                "projectPath": str(harness.target_project.resolve()),
            },
            "suggestions": [],
        }
    )


def _begin_mixed_switch(harness: _ContractHarness) -> tuple[str, dict[str, Any]]:
    _set_unique_resolution(harness)
    original = (
        f"프로젝트 {harness.target_project.stem}로 바꾸고 "
        "Player AnimInstance 분석해"
    )
    payload = _plan(harness, original)
    return original, dict(payload["requiredNextToolArgs"])


def test_name_already_active_is_a_taskless_noop(
    contract_harness: _ContractHarness,
) -> None:
    harness = contract_harness
    payload = _plan(
        harness,
        f"{harness.current_project.stem}로 프로젝트 바꿔",
    )

    assert payload["switchResult"] == "already_active"
    assert payload["changed"] is False
    assert payload["taskSessionStarted"] is False
    assert payload["nextActionIsTool"] is False
    assert {key: len(value) for key, value in harness.calls.items()} == {
        "resolver": 0,
        "set": 0,
        "planner": 0,
        "task": 0,
    }


def test_parent_folder_alias_cannot_false_positive_as_already_active(
    contract_harness: _ContractHarness,
) -> None:
    harness = contract_harness
    parent_alias = harness.current_project.parent.name
    harness.resolution.clear()
    harness.resolution.update(
        {
            "ok": True,
            "selected": {
                "projectName": parent_alias,
                "projectFile": f"{parent_alias}.uproject",
                "projectPath": str(harness.target_project.resolve()),
            },
            "suggestions": [],
        }
    )
    payload = _plan(harness, f'"{parent_alias}"로 프로젝트 바꿔')

    assert payload.get("switchResult") != "already_active"
    assert payload["requiredNextTool"] == "unreal_set_active_project"
    assert len(harness.calls["resolver"]) == 1


def test_unique_exact_name_returns_only_the_server_owned_project_path(
    contract_harness: _ContractHarness,
) -> None:
    harness = contract_harness
    _set_unique_resolution(harness)

    payload = _plan(
        harness,
        f"{harness.target_project.stem}로 프로젝트 바꿔",
    )

    expected = str(harness.target_project.resolve())
    assert payload["requiredNextTool"] == "unreal_set_active_project"
    assert payload["requiredNextToolArgs"] == {"projectPath": expected}
    assert payload["nextActionArgs"] == {"projectPath": expected}
    assert payload["resolvedProject"]["projectPath"] == expected
    assert [call["target"] for call in harness.calls["resolver"]] == [
        harness.target_project.stem
    ]
    assert harness.calls["set"] == []
    assert harness.calls["planner"] == []
    assert harness.calls["task"] == []


@pytest.mark.parametrize(
    ("error_code", "suggestions"),
    [
        (
            "PROJECT_NAME_AMBIGUOUS",
            [
                {"projectName": "Shared", "projectPath": "candidate-one"},
                {"projectName": "Shared", "projectPath": "candidate-two"},
            ],
        ),
        (
            "PROJECT_NAME_NOT_FOUND",
            [{"projectName": "Target_Game_Preview", "projectPath": "suggestion-only"}],
        ),
    ],
)
def test_ambiguous_or_missing_name_waits_for_user_without_starting_remaining_work(
    contract_harness: _ContractHarness,
    error_code: str,
    suggestions: list[dict[str, Any]],
) -> None:
    harness = contract_harness
    harness.resolution.clear()
    harness.resolution.update(
        {
            "ok": False,
            "errorCode": error_code,
            "error": "No unique exact project-name match.",
            "suggestions": suggestions,
        }
    )

    payload = _plan(
        harness,
        f"프로젝트 {harness.target_project.stem}로 바꾸고 소스 분석해",
    )

    assert payload["ok"] is False
    assert payload["status"] == "await_user"
    assert payload["errorCode"] == error_code
    assert payload["suggestions"] == suggestions
    assert payload["nextAction"] == "clarify_project_name"
    assert payload["nextActionIsTool"] is False
    assert len(harness.calls["resolver"]) == 1
    assert harness.calls["set"] == []
    assert harness.calls["planner"] == []
    assert harness.calls["task"] == []


@pytest.mark.parametrize(
    "control_text",
    [
        "현재 프로젝트 어디야",
        "Target_Game로 지정돼 있어?",
        "Target_Game로 지정하지 마",
    ],
)
def test_status_query_and_negated_control_never_switch_or_start_a_task(
    contract_harness: _ContractHarness,
    control_text: str,
) -> None:
    harness = contract_harness

    payload = _plan(harness, control_text)

    assert payload["taskSessionStarted"] is False
    assert payload["nextActionIsTool"] is False
    assert harness.calls["resolver"] == []
    assert harness.calls["set"] == []
    assert harness.calls["planner"] == []
    assert harness.calls["task"] == []


def test_mixed_already_active_request_plans_exact_remaining_substring_once(
    contract_harness: _ContractHarness,
) -> None:
    harness = contract_harness
    remaining = "Player AnimInstance 분석해"
    original = f"프로젝트 {harness.current_project.stem}로 바꾸고 {remaining}"

    payload = _plan(harness, original)

    assert payload["projectControl"]["switchResult"] == "already_active"
    assert payload["projectControl"]["changed"] is False
    assert harness.calls["resolver"] == []
    assert harness.calls["set"] == []
    assert len(harness.calls["planner"]) == 1
    assert harness.calls["planner"][0] == {
        "request": remaining,
        "mode": "auto",
        "kwargs": {
            "latest_user_message": remaining,
            "original_objective": original,
        },
    }
    assert len(harness.calls["task"]) == 1
    assert harness.calls["task"][0]["kwargs"]["request"] == remaining


@pytest.mark.parametrize(
    "original",
    [
        "현재 프로젝트 어디야 그리고 Player AnimInstance 분석해",
        "Target_Game로 지정하지 말고 Player AnimInstance 분석해",
    ],
)
def test_non_mutating_mixed_control_keeps_the_remaining_task_once(
    contract_harness: _ContractHarness,
    original: str,
) -> None:
    harness = contract_harness
    remaining = "Player AnimInstance 분석해"

    payload = _plan(harness, original)

    assert payload["projectControl"]["switchResult"] == "not_requested"
    assert payload["projectControl"]["changed"] is False
    assert harness.calls["resolver"] == []
    assert harness.calls["set"] == []
    assert len(harness.calls["planner"]) == 1
    assert harness.calls["planner"][0] == {
        "request": remaining,
        "mode": "auto",
        "kwargs": {
            "latest_user_message": remaining,
            "original_objective": original,
        },
    }
    assert len(harness.calls["task"]) == 1
    assert harness.calls["task"][0]["kwargs"]["request"] == remaining


def test_mixed_switch_starts_no_task_until_valid_switch_then_resumes_exactly_once(
    contract_harness: _ContractHarness,
) -> None:
    harness = contract_harness
    remaining = "Player AnimInstance 분석해"
    original, set_args = _begin_mixed_switch(harness)

    expected_project = str(harness.target_project.resolve())
    assert set_args["projectPath"] == expected_project
    assert isinstance(set_args.get("resumeToken"), str) and set_args["resumeToken"]
    assert harness.calls["set"] == []
    assert harness.calls["planner"] == []
    assert harness.calls["task"] == []

    switched = harness.call("unreal_set_active_project", set_args)

    assert len(harness.calls["set"]) == 1
    assert harness.calls["planner"] == []
    assert harness.calls["task"] == []
    plan_args = dict(switched["requiredNextToolArgs"])
    assert switched["requiredNextTool"] == "unreal_agent_plan"
    assert plan_args["request"] == remaining
    assert plan_args["latestUserMessage"] == remaining
    assert plan_args["projectSwitchResumeToken"] == set_args["resumeToken"]

    resumed = harness.call("unreal_agent_plan", plan_args)

    assert resumed["projectControl"] == {
        "operation": "select",
        "switchResult": "switched",
        "changed": True,
        "resumeAfter": "unreal_set_active_project",
    }
    assert len(harness.calls["planner"]) == 1
    assert harness.calls["planner"][0] == {
        "request": remaining,
        "mode": "auto",
        "kwargs": {
            "latest_user_message": remaining,
            "original_objective": original,
        },
    }
    assert len(harness.calls["task"]) == 1
    assert harness.calls["task"][0]["kwargs"]["request"] == remaining
    assert harness.calls["task"][0]["kwargs"]["project_file"] == expected_project


def test_mixed_switch_race_preserves_controller_already_active_result(
    contract_harness: _ContractHarness,
) -> None:
    harness = contract_harness
    _original, set_args = _begin_mixed_switch(harness)
    harness.config["activeProject"] = str(harness.target_project.resolve())

    switched = harness.call("unreal_set_active_project", set_args)
    assert switched["switchResult"] == "already_active"
    assert switched["changed"] is False

    resumed = harness.call(
        "unreal_agent_plan",
        dict(switched["requiredNextToolArgs"]),
    )
    assert resumed["projectControl"]["switchResult"] == "already_active"
    assert resumed["projectControl"]["changed"] is False
    assert len(harness.calls["planner"]) == 1
    assert len(harness.calls["task"]) == 1


def test_invented_switch_token_is_rejected_before_switch_or_task(
    contract_harness: _ContractHarness,
) -> None:
    harness = contract_harness

    payload = harness.call(
        "unreal_set_active_project",
        {
            "projectPath": str(harness.target_project.resolve()),
            "resumeToken": "invented-token",
        },
    )

    assert payload["ok"] is False
    assert payload["errorCode"] == "PROJECT_SWITCH_HANDOFF_INVALID"
    assert payload["stopCurrentWorkflow"] is True
    assert harness.calls["set"] == []
    assert harness.calls["planner"] == []
    assert harness.calls["task"] == []


def test_resume_token_is_bound_to_the_server_selected_project_path(
    contract_harness: _ContractHarness,
) -> None:
    harness = contract_harness
    _, set_args = _begin_mixed_switch(harness)
    set_args["projectPath"] = str(harness.other_project.resolve())

    payload = harness.call("unreal_set_active_project", set_args)

    assert payload["ok"] is False
    assert payload["errorCode"] == "PROJECT_SWITCH_HANDOFF_INVALID"
    assert harness.calls["set"] == []
    assert harness.calls["planner"] == []
    assert harness.calls["task"] == []


def test_stale_resume_token_is_rejected_without_reconstructing_work(
    contract_harness: _ContractHarness,
) -> None:
    harness = contract_harness
    _, set_args = _begin_mixed_switch(harness)
    token = set_args["resumeToken"]
    switched = harness.call("unreal_set_active_project", set_args)
    harness.server._pending_project_switch_handoffs[token]["expiresAt"] = time.time() - 1

    payload = harness.call(
        "unreal_agent_plan",
        dict(switched["requiredNextToolArgs"]),
    )

    assert payload["ok"] is False
    assert payload["errorCode"] == "PROJECT_SWITCH_RESUME_INVALID"
    assert payload["stopCurrentWorkflow"] is True
    assert len(harness.calls["set"]) == 1
    assert harness.calls["planner"] == []
    assert harness.calls["task"] == []


def test_resumed_objective_hash_mismatch_fails_closed_before_plan_or_task(
    contract_harness: _ContractHarness,
) -> None:
    harness = contract_harness
    _, set_args = _begin_mixed_switch(harness)
    switched = harness.call("unreal_set_active_project", set_args)
    plan_args = dict(switched["requiredNextToolArgs"])
    plan_args["objectiveHash"] = "mismatched-objective-hash"

    payload = harness.call("unreal_agent_plan", plan_args)

    assert payload["ok"] is False
    assert payload["errorCode"] == "PROJECT_SWITCH_OBJECTIVE_MISMATCH"
    assert len(harness.calls["set"]) == 1
    assert harness.calls["planner"] == []
    assert harness.calls["task"] == []

    # A mismatched caller cannot burn the opaque one-shot token. The original
    # server-owned arguments still resume exactly once.
    resumed = harness.call(
        "unreal_agent_plan",
        dict(switched["requiredNextToolArgs"]),
    )
    assert resumed["projectControl"]["switchResult"] == "switched"
    assert len(harness.calls["planner"]) == 1
    assert len(harness.calls["task"]) == 1


def test_resume_requires_objective_hash_and_successful_token_is_one_shot(
    contract_harness: _ContractHarness,
) -> None:
    harness = contract_harness
    _, set_args = _begin_mixed_switch(harness)
    switched = harness.call("unreal_set_active_project", set_args)
    exact_plan_args = dict(switched["requiredNextToolArgs"])
    missing_hash_args = dict(exact_plan_args)
    missing_hash_args.pop("objectiveHash")

    missing = harness.call("unreal_agent_plan", missing_hash_args)
    assert missing["ok"] is False
    assert missing["errorCode"] == "PROJECT_SWITCH_OBJECTIVE_MISMATCH"
    assert harness.calls["planner"] == []
    assert harness.calls["task"] == []

    resumed = harness.call("unreal_agent_plan", exact_plan_args)
    assert resumed["projectControl"]["switchResult"] == "switched"
    assert len(harness.calls["planner"]) == 1
    assert len(harness.calls["task"]) == 1

    replay = harness.call("unreal_agent_plan", exact_plan_args)
    assert replay["ok"] is False
    assert replay["errorCode"] == "PROJECT_SWITCH_RESUME_INVALID"
    assert len(harness.calls["planner"]) == 1
    assert len(harness.calls["task"]) == 1


def test_mixed_switch_resume_preserves_ecmascript_non_trim_unicode_objective(
    contract_harness: _ContractHarness,
) -> None:
    harness = contract_harness
    from agent_orchestrator import objective_hash

    _set_unique_resolution(harness)
    remaining = "Player AnimInstance 분석해"
    # ECMAScript String.trim intentionally preserves U+0085. The handoff,
    # resume planner, and task intent must therefore keep the same objective.
    original = (
        f"\u0085프로젝트 {harness.target_project.stem}로 바꾸고 "
        f"{remaining}\u0085"
    )
    initial = _plan(harness, original)
    set_args = dict(initial["requiredNextToolArgs"])

    switched = harness.call(
        "unreal_set_active_project",
        set_args,
    )
    plan_args = dict(switched["requiredNextToolArgs"])
    assert plan_args["objectiveHash"] == objective_hash(original)

    resumed = harness.call("unreal_agent_plan", plan_args)

    assert resumed["projectControl"]["switchResult"] == "switched"
    assert harness.calls["planner"][0]["kwargs"]["original_objective"] == original
    assert len(harness.calls["task"]) == 1


def test_active_project_change_after_switch_blocks_pending_task_before_plan(
    contract_harness: _ContractHarness,
) -> None:
    harness = contract_harness
    _, set_args = _begin_mixed_switch(harness)
    switched = harness.call("unreal_set_active_project", set_args)
    harness.config["activeProject"] = str(harness.other_project.resolve())

    payload = harness.call(
        "unreal_agent_plan",
        dict(switched["requiredNextToolArgs"]),
    )

    assert payload["ok"] is False
    assert payload["errorCode"] == "PROJECT_SWITCH_ACTIVE_PROJECT_MISMATCH"
    assert payload["stopCurrentWorkflow"] is True
    assert harness.calls["planner"] == []
    assert harness.calls["task"] == []


def test_controller_success_for_wrong_project_never_arms_resume(
    contract_harness: _ContractHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = contract_harness
    _, set_args = _begin_mixed_switch(harness)

    import project_controller

    def wrong_project_result(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {
            "ok": True,
            "activeProject": str(harness.other_project.resolve()),
            "switchResult": "switched",
            "changed": True,
            "cacheInvalidation": {"cacheGeneration": 7},
            "cacheRefreshRequired": False,
        }

    monkeypatch.setattr(
        project_controller,
        "switch_active_project",
        wrong_project_result,
    )
    payload = harness.call("unreal_set_active_project", set_args)

    assert payload["ok"] is False
    assert payload["errorCode"] == "PROJECT_SWITCH_ACTIVE_PROJECT_MISMATCH"
    assert payload["stopCurrentWorkflow"] is True
    assert harness.calls["planner"] == []
    assert harness.calls["task"] == []


def test_controller_switch_failure_cancels_handoff_without_starting_task(
    contract_harness: _ContractHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = contract_harness
    _, set_args = _begin_mixed_switch(harness)

    import project_controller

    monkeypatch.setattr(
        project_controller,
        "switch_active_project",
        lambda *args, **kwargs: {
            "ok": False,
            "errorCode": "PROJECT_PATH_UNAVAILABLE",
            "error": "The selected project disappeared before the switch.",
        },
    )
    failed = harness.call("unreal_set_active_project", set_args)

    assert failed["ok"] is False
    assert failed["errorCode"] == "PROJECT_PATH_UNAVAILABLE"
    assert failed["stopCurrentWorkflow"] is True
    assert harness.calls["planner"] == []
    assert harness.calls["task"] == []

    retry = harness.call("unreal_set_active_project", set_args)
    assert retry["ok"] is False
    assert retry["errorCode"] == "PROJECT_SWITCH_HANDOFF_INVALID"


def test_active_project_race_during_resumed_plan_blocks_task_start(
    contract_harness: _ContractHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = contract_harness
    _, set_args = _begin_mixed_switch(harness)
    switched = harness.call("unreal_set_active_project", set_args)

    import agent_orchestrator

    def racing_plan(request: str, mode: str, **kwargs: Any) -> _FakePlan:
        harness.calls["planner"].append(
            {"request": request, "mode": mode, "kwargs": copy.deepcopy(kwargs)}
        )
        harness.config["activeProject"] = str(harness.other_project.resolve())
        return _FakePlan(
            {
                "ok": True,
                "request": request,
                "taskKind": "inspect_only",
                "editStrategy": "no_edit",
                "writeGate": {"writesAllowed": False},
                "orchestration": {
                    "strategy": "bounded_inspection",
                    "requiredBeforeWrite": [],
                    "taskSessionRequired": True,
                },
            }
        )

    monkeypatch.setattr(agent_orchestrator, "build_agent_plan", racing_plan)
    payload = harness.call(
        "unreal_agent_plan",
        dict(switched["requiredNextToolArgs"]),
    )

    assert payload["ok"] is False
    assert payload["errorCode"] == "PROJECT_SWITCH_ACTIVE_PROJECT_MISMATCH"
    assert len(harness.calls["planner"]) == 1
    assert harness.calls["task"] == []
