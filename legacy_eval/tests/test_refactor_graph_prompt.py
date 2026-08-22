from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from agent_orchestrator import AgentPlan, EvidencePlan, format_plan_for_prompt  # noqa: E402


def test_refactor_prompt_keeps_graph_impact_and_regression_scope_visible() -> None:
    plan = AgentPlan(
        request="Update Worker",
        task_kind="refactor",
        evidence=EvidencePlan(task_kind="refactor"),
        edit_strategy="staged_refactor",
        refactor_manager={
            "scope": {"scope": "small_multifile_refactor"},
            "nextAction": "execute_next_staged_refactor_patch",
            "writePolicy": {"writesAllowedNow": True},
            "impact": {"missingRequiredRoles": []},
            "changeImpact": {
                "directImpacts": [{"path": "Source/Demo/Public/Worker.h"}],
                "candidateImpacts": [{"path": "Source/Demo/Private/Worker.cpp"}],
                "regressionPlan": [{"kind": "targeted_regression", "required": True}],
            },
        },
    )

    prompt = format_plan_for_prompt(plan)

    assert "directImpactPaths" in prompt
    assert "Worker.h" in prompt
    assert "targeted_regression" in prompt
