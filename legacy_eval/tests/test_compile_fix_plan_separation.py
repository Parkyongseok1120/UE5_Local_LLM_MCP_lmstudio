from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from agent_orchestrator import build_agent_plan  # noqa: E402
from domain_planner import partition_plan_slices, build_domain_profile, build_domain_slice_dag  # noqa: E402


def test_compile_fix_has_no_executable_slices() -> None:
    request = (
        "compile fix: fatal error UBoxComponent CreateDefaultSubobject missing include "
        "UActorComponent component registration"
    )
    plan = build_agent_plan(request, "compile_fix")
    assert plan.executable_plan_slices == []
    assert plan.plan_slices == []
    assert plan.fix_evidence or plan.informational_plan_slices


def test_prototype_component_has_executable_slices() -> None:
    request = "prototype component UMyProbeComponent for actor"
    plan = build_agent_plan(request, "prototype_component")
    assert len(plan.executable_plan_slices) >= 1
    assert plan.plan_slices == plan.executable_plan_slices


def test_partition_moves_architecture_to_informational() -> None:
    profile = build_domain_profile("add replicated component with RPC", "edit")
    dag = build_domain_slice_dag(profile, "add replicated component with RPC")
    informational, executable = partition_plan_slices(dag, task_kind="compile_fix", mode="compile_fix")
    assert executable == []
    assert any(
        str(item.slice_kind) == "architecture" or item.slice_id == "ownership_decision"
        for item in informational
    )
