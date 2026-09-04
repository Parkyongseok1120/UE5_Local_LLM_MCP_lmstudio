from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CLINE_SURFACES = (
    ".clinerules",
    "prompts/cline_unreal_agent_system.md",
    "docs/Cline_Rider_Unreal_Agent_Setup.md",
    "docs/Rider_Cline_Smoke_Checklist.md",
)
REMOVED_WORKFLOW_TERMS = (
    "unreal_agent_plan",
    "unreal_agent_session",
    "unreal_project_status",
    "taskAuthorization",
    "ownerCapability",
    "requiredNextTool",
    "writeGate",
    "TASK_TOOL_NOT_ACTIVE",
)


def test_all_advertised_cline_surfaces_use_the_direct_contract() -> None:
    for relative in CLINE_SURFACES:
        text = (ROOT / relative).read_text(encoding="utf-8")
        assert "Direct" in text, relative
        assert "모델" in text, relative
        for removed in REMOVED_WORKFLOW_TERMS:
            assert removed not in text, (relative, removed)


def test_cline_surfaces_do_not_restore_a_fixed_tool_sequence_or_build_gate() -> None:
    combined = "\n".join(
        (ROOT / relative).read_text(encoding="utf-8")
        for relative in CLINE_SURFACES
    ).casefold()

    assert "essential tools only" not in combined
    assert "tool order" not in combined
    assert "validation dirty" not in combined
    assert "static_validate_project clears" not in combined
    assert "all checked items" not in combined
