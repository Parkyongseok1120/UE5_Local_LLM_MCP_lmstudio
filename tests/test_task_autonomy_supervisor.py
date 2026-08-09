from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import task_api  # noqa: E402
from task_api import (  # noqa: E402
    task_checkpoint,
    task_record_gate,
    task_start,
    task_status,
)


def _authorization(started: dict) -> dict[str, str]:
    state = started["state"]
    return {
        "taskSessionId": started["taskSessionId"],
        "authToken": started["authToken"],
        "planId": state["planId"],
        "planRevision": state["planRevision"],
        "activeSliceId": state["activeSliceId"],
    }


def _project(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "Demo"
    root.mkdir()
    uproject = root / "Demo.uproject"
    uproject.write_text("{}", encoding="utf-8")
    return root, uproject


def _write(root: Path, relative: str, text: str = "baseline") -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _initialize_git(root: Path) -> None:
    assert _git(root, "init").returncode == 0
    assert _git(root, "add", ".").returncode == 0
    committed = _git(
        root,
        "-c",
        "user.email=checkpoint@example.invalid",
        "-c",
        "user.name=Checkpoint Test",
        "commit",
        "-m",
        "baseline",
    )
    assert committed.returncode == 0, committed.stderr


def test_checkpoint_unions_caller_prior_git_slice_and_impact_contract_files(
    tmp_path: Path,
) -> None:
    project, uproject = _project(tmp_path)
    for relative in (
        "Source/Demo/Caller.cpp",
        "Source/Demo/GitChanged.cpp",
        "Source/Demo/Slice.cpp",
        "Source/Demo/Direct.h",
        "Source/Demo/Implementation.cpp",
    ):
        _write(project, relative)
    _initialize_git(project)
    _write(project, "Source/Demo/GitChanged.cpp", "modified")
    _write(project, "Source/Demo/Untracked.cpp", "untracked")
    plan = {
        "writeGate": {"writesAllowed": True},
        "executablePlanSlices": [
            {
                "slice_id": "implementation",
                "files": [
                    "Source/Demo/Slice.cpp",
                    "/Game/UI/WBP_LogicalOnly",
                    "<generated>.h",
                ],
            }
        ],
        "impactContract": {
            "directSurfaces": [
                {"path": "Source/Demo/Direct.h"},
                "/Game/Maps/Lobby",
            ],
            "implementationFiles": ["Source/Demo/Implementation.cpp"],
        },
    }
    started = task_start(
        tmp_path,
        request="Implement all declared surfaces",
        project_file=str(uproject),
        plan_payload=plan,
    )

    recorded = task_checkpoint(
        tmp_path,
        task_authorization=_authorization(started),
        action="record",
        modified_files=["Source/Demo/Caller.cpp"],
        required_next_action="build",
    )

    assert recorded["ok"] is True
    checkpoint = recorded["continuity"]["checkpoint"]
    paths = set(checkpoint["modifiedFiles"])
    assert {
        "Source/Demo/Caller.cpp",
        "Source/Demo/GitChanged.cpp",
        "Source/Demo/Untracked.cpp",
        "Source/Demo/Slice.cpp",
        "Source/Demo/Direct.h",
        "Source/Demo/Implementation.cpp",
    } <= paths
    assert not any(path.startswith("/Game") for path in paths)
    assert any("logical or placeholder" in item for item in checkpoint["discoveryWarnings"])
    assert set(checkpoint["gitChangedFiles"]) == {
        "Source/Demo/GitChanged.cpp",
        "Source/Demo/Untracked.cpp",
    }
    assert len(checkpoint["fileSnapshots"]) == len(checkpoint["modifiedFiles"])


def test_checkpoint_without_git_discovery_preserves_baseline_and_task_mutations(
    tmp_path: Path,
) -> None:
    project, uproject = _project(tmp_path)
    first = _write(project, "Source/Demo/First.cpp")
    second = _write(project, "Source/Demo/Second.cpp")
    _initialize_git(project)
    first.write_text("first task edit", encoding="utf-8")
    started = task_start(
        tmp_path,
        request="Continue bounded task edits",
        project_file=str(uproject),
        plan_payload={"writeGate": {"writesAllowed": True}},
    )
    authorization = _authorization(started)

    baseline = task_checkpoint(
        tmp_path,
        task_authorization=authorization,
        action="record",
        modified_files=["Source/Demo/First.cpp"],
        include_git_changes=True,
    )
    assert baseline["ok"] is True
    assert set(baseline["continuity"]["checkpoint"]["gitChangedFiles"]) == {
        "Source/Demo/First.cpp",
    }

    second.write_text("second task edit", encoding="utf-8")
    automatic = task_checkpoint(
        tmp_path,
        task_authorization=authorization,
        action="record",
        modified_files=["Source/Demo/Second.cpp"],
        include_git_changes=False,
    )
    assert automatic["ok"] is True
    assert set(automatic["continuity"]["checkpoint"]["gitChangedFiles"]) == {
        "Source/Demo/First.cpp",
        "Source/Demo/Second.cpp",
    }

    recovered = task_checkpoint(
        tmp_path,
        task_authorization=authorization,
        action="recover",
    )
    assert recovered["ok"] is True
    assert recovered["continuity"]["recovery"]["conflicts"] == []


def test_checkpoint_preserves_prior_files_and_non_git_projects_warn(
    tmp_path: Path,
) -> None:
    project, uproject = _project(tmp_path)
    _write(project, "Source/Demo/First.cpp")
    _write(project, "Source/Demo/Second.cpp")
    started = task_start(
        tmp_path,
        request="Non-Git task",
        project_file=str(uproject),
        plan_payload={"writeGate": {"writesAllowed": True}},
    )
    authorization = _authorization(started)
    first = task_checkpoint(
        tmp_path,
        task_authorization=authorization,
        action="record",
        modified_files=["Source/Demo/First.cpp"],
    )
    second = task_checkpoint(
        tmp_path,
        task_authorization=authorization,
        action="record",
        modified_files=["Source/Demo/Second.cpp"],
    )

    assert first["ok"] is True
    assert second["ok"] is True
    checkpoint = second["continuity"]["checkpoint"]
    assert set(checkpoint["modifiedFiles"]) == {
        "Source/Demo/First.cpp",
        "Source/Demo/Second.cpp",
    }
    assert any("not a Git work tree" in item for item in checkpoint["discoveryWarnings"])


def test_recovery_detects_newly_appearing_git_change(tmp_path: Path) -> None:
    project, uproject = _project(tmp_path)
    _write(project, "Source/Demo/Tracked.cpp")
    _initialize_git(project)
    started = task_start(
        tmp_path,
        request="Recover Git task",
        project_file=str(uproject),
        plan_payload={"writeGate": {"writesAllowed": True}},
    )
    authorization = _authorization(started)
    recorded = task_checkpoint(
        tmp_path,
        task_authorization=authorization,
        action="record",
    )
    assert recorded["ok"] is True
    _write(project, "Source/Demo/NewlyAppeared.cpp")

    recovered = task_checkpoint(
        tmp_path,
        task_authorization=authorization,
        action="recover",
    )

    assert recovered["ok"] is False
    assert recovered["errorCode"] == "TASK_CHECKPOINT_CONFLICT"
    assert {
        (item["relativePath"], item["reason"]) for item in recovered["conflicts"]
    } >= {("Source/Demo/NewlyAppeared.cpp", "new_git_change")}


def test_recovery_unions_new_caller_paths_and_requires_rebase(
    tmp_path: Path,
) -> None:
    project, uproject = _project(tmp_path)
    _write(project, "Source/Demo/Initial.cpp")
    _write(project, "Source/Demo/DiscoveredLater.cpp")
    started = task_start(
        tmp_path,
        request="Recover caller discovery",
        project_file=str(uproject),
        plan_payload={"writeGate": {"writesAllowed": True}},
    )
    authorization = _authorization(started)
    recorded = task_checkpoint(
        tmp_path,
        task_authorization=authorization,
        action="record",
        modified_files=["Source/Demo/Initial.cpp"],
    )
    assert recorded["ok"] is True

    recovered = task_checkpoint(
        tmp_path,
        task_authorization=authorization,
        action="recover",
        modified_files=["Source/Demo/DiscoveredLater.cpp"],
    )

    assert recovered["ok"] is False
    assert {
        (item["relativePath"], item["reason"]) for item in recovered["conflicts"]
    } >= {("Source/Demo/DiscoveredLater.cpp", "new_checkpoint_path")}


def test_checkpoint_file_overflow_is_explicit_not_truncated(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project, uproject = _project(tmp_path)
    paths = [
        str(_write(project, f"Source/Demo/File{index}.cpp").relative_to(project))
        for index in range(3)
    ]
    started = task_start(
        tmp_path,
        request="Bound checkpoint",
        project_file=str(uproject),
        plan_payload={"writeGate": {"writesAllowed": True}},
    )
    monkeypatch.setattr(task_api, "MAX_CHECKPOINT_FILES", 2)

    result = task_checkpoint(
        tmp_path,
        task_authorization=_authorization(started),
        action="record",
        modified_files=paths,
    )

    assert result["ok"] is False
    assert result["errorCode"] == "CHECKPOINT_FILE_SET_OVERFLOW"
    assert "3 > 2" in result["error"]


def test_supervisor_blocks_repeated_no_progress_action_and_phase_surfaces_replan(
    tmp_path: Path,
) -> None:
    project, uproject = _project(tmp_path)
    _write(project, "Source/Demo/Thing.cpp")
    started = task_start(
        tmp_path,
        request="Avoid autonomous loop",
        project_file=str(uproject),
        plan_payload={
            "writeGate": {"writesAllowed": True},
            "autonomySupervisor": {
                "sameActionNoProgress": 2,
                "sameErrorNoProgress": 10,
                "totalNoProgress": 10,
            },
        },
    )
    authorization = _authorization(started)
    initial_supervisor = started["state"]["autonomySupervisor"]
    assert initial_supervisor["lastObservation"]["checkpointSequence"] == 0
    assert initial_supervisor["lastObservation"]["completedSlices"] == []
    assert initial_supervisor["lastObservation"]["completedGates"] == []
    assert initial_supervisor["lastObservation"]["validationArtifacts"] == []

    for _ in range(3):
        result = task_checkpoint(
            tmp_path,
            task_authorization=authorization,
            action="record",
            modified_files=["Source/Demo/Thing.cpp"],
            required_next_action="retry identical patch",
        )
        assert result["ok"] is True

    current = task_status(tmp_path, started["taskSessionId"])
    assert current["autonomySupervisor"]["status"] == "blocked"
    assert {
        item["code"] for item in current["autonomySupervisor"]["blockers"]
    } >= {"repeated_action_no_progress"}
    assert current["writeReadiness"]["ready"] is False
    assert "autonomy:repeated_action_no_progress" in current["writeReadiness"][
        "blockedReasons"
    ]
    assert current["nextAction"] == "replan_autonomous_strategy"


def test_supervisor_detects_repeated_same_error_with_independent_budget(
    tmp_path: Path,
) -> None:
    project, uproject = _project(tmp_path)
    _write(project, "Source/Demo/Thing.cpp")
    started = task_start(
        tmp_path,
        request="Bound compile retries",
        project_file=str(uproject),
        plan_payload={
            "writeGate": {"writesAllowed": True},
            "autonomySupervisor": {
                "sameActionNoProgress": 10,
                "sameErrorNoProgress": 2,
                "totalNoProgress": 10,
            },
        },
    )
    authorization = _authorization(started)
    for _ in range(3):
        result = task_checkpoint(
            tmp_path,
            task_authorization=authorization,
            action="record",
            modified_files=["Source/Demo/Thing.cpp"],
            required_next_action="compile",
            validation={"error": "C2039: missing member"},
        )
        assert result["ok"] is True

    current = task_status(tmp_path, started["taskSessionId"])
    assert {
        item["code"] for item in current["autonomySupervisor"]["blockers"]
    } >= {"repeated_error_no_progress"}


def test_target_change_invalidates_validation_and_rebase_resets_strategy(
    tmp_path: Path,
) -> None:
    project, uproject = _project(tmp_path)
    target = _write(project, "Source/Demo/Thing.cpp", "before")
    plan = {
        "writeGate": {"writesAllowed": True},
        "orchestration": {"requiredBeforeWrite": ["architecture"]},
        "autonomySupervisor": {"sameActionNoProgress": 1},
    }
    started = task_start(
        tmp_path,
        request="Validate then change",
        project_file=str(uproject),
        plan_payload=plan,
    )
    authorization = _authorization(started)
    gate = task_record_gate(
        tmp_path,
        gate_name="architecture",
        task_authorization=authorization,
        input_payload={"scope": "Demo"},
        evidence={"ok": True},
    )
    assert gate["ok"] is True
    validated = task_checkpoint(
        tmp_path,
        task_authorization=authorization,
        action="record",
        phase="validation",
        completed_slices=["implementation"],
        modified_files=["Source/Demo/Thing.cpp"],
        required_next_action="verify",
        validation={"build": {"artifactHash": "abc", "ok": True}},
    )
    assert validated["continuity"]["checkpoint"]["sequence"] == 1
    supervisor = task_status(tmp_path, started["taskSessionId"])[
        "autonomySupervisor"
    ]
    assert supervisor["validation"]["status"] == "current"
    assert supervisor["progress"]["completedSlices"] == ["implementation"]
    assert supervisor["progress"]["completedGates"] == ["architecture"]
    assert supervisor["progress"]["validationArtifacts"]

    target.write_text("after", encoding="utf-8")
    changed = task_checkpoint(
        tmp_path,
        task_authorization=authorization,
        action="record",
        modified_files=["Source/Demo/Thing.cpp"],
        required_next_action="verify",
    )
    assert changed["ok"] is True
    invalidated = task_status(tmp_path, started["taskSessionId"])[
        "autonomySupervisor"
    ]
    assert invalidated["validation"]["status"] == "invalidated"
    assert invalidated["validation"]["invalidationReason"] == "target_hash_changed"

    rebased = task_checkpoint(
        tmp_path,
        task_authorization=authorization,
        action="rebase",
        accept_current_files=True,
    )
    assert rebased["ok"] is True
    current = task_status(tmp_path, started["taskSessionId"])
    assert current["state"]["autonomySupervisor"]["strategyEpoch"] == 2
    assert current["state"]["autonomySupervisor"]["status"] == "active"
    assert current["state"]["autonomySupervisor"]["retryState"] == {
        "sameActionNoProgress": 0,
        "sameErrorNoProgress": 0,
        "totalNoProgress": 0,
    }
    assert current["state"]["autonomySupervisor"]["validation"]["status"] == "invalidated"
    assert (
        current["state"]["autonomySupervisor"]["validation"]["invalidationReason"]
        == "checkpoint_rebase"
    )
    assert current["state"]["completedGates"] == {}
