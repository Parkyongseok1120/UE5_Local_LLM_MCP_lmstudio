from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from runtime_experiment_runner import (  # noqa: E402
    build_unreal_experiment_plan,
    run_unreal_experiment_plan,
)


def test_experiment_plan_uses_argv_and_bounded_soak() -> None:
    plan = build_unreal_experiment_plan(
        editor_cmd="UnrealEditor-Cmd",
        project_file="Demo.uproject",
        automation_filter="Demo.Runtime.Reconnect",
        trace_channels=["cpu", "frame", "cpu"],
        trace_output="Saved/Profiling/reconnect.utrace",
        soak_iterations=500,
        map_name="Lobby",
        dedicated_server=True,
    )

    assert plan["ok"] is True
    assert plan["soakIterations"] == 100
    assert plan["argv"][0] == "UnrealEditor-Cmd"
    assert plan["argv"][2] == "Lobby"
    assert any("Automation RunTest Demo.Runtime.Reconnect" in arg for arg in plan["argv"])
    assert "-trace=cpu,frame" in plan["argv"]
    assert "-server" in plan["argv"]


def test_experiment_runner_stops_on_first_failed_iteration() -> None:
    calls: list[list[str]] = []

    def fake_runner(argv, **_kwargs):
        calls.append(argv)
        code = 0 if len(calls) == 1 else 1
        return subprocess.CompletedProcess(argv, code, stdout="run", stderr="")

    plan = build_unreal_experiment_plan(
        editor_cmd="UnrealEditor-Cmd",
        project_file="Demo.uproject",
        automation_filter="Demo.Runtime",
        soak_iterations=3,
    )
    result = run_unreal_experiment_plan(plan, runner=fake_runner)

    assert result["ok"] is False
    assert result["completedIterations"] == 2
    assert result["proofLevel"] == "NeedsRuntimeProof"
