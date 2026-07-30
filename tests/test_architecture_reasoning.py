from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from architecture_reasoning import analyze_architecture  # noqa: E402
from build_symbol_graph import build_symbol_graph  # noqa: E402


def _fixture(root: Path) -> None:
    public = root / "Source" / "Core" / "Public"
    private = root / "Source" / "Game" / "Private"
    public.mkdir(parents=True)
    private.mkdir(parents=True)
    (public / "Shared.h").write_text("void Finish();\n", encoding="utf-8")
    (private / "Worker.cpp").write_text(
        '#include "../../Core/Public/Shared.h"\n'
        'void Run() { CurrentState = EState::Running; Value = ReadInput(); Finish(); return Value; }\n'
        'int32 ReadInput() { return 1; }\n'
        'void Finish() {}\n',
        encoding="utf-8",
    )


def test_architecture_analysis_has_boundaries_data_flow_and_state_candidates(tmp_path: Path) -> None:
    _fixture(tmp_path)

    result = analyze_architecture(tmp_path, symbols=["Run"])

    assert result["ok"] is True
    dependencies = result["topology"]["boundaryDependencies"]
    assert dependencies and dependencies[0]["from"] == "module:Game"
    assert dependencies[0]["to"] == "module:Core"
    assert any(item["kind"] == "assignment_candidate" for item in result["dataFlow"]["flows"])
    assert any(item["kind"] == "state_assignment_candidate" for item in result["stateTransitions"]["transitions"])
    assert not any(
        item["kind"] == "call_argument_boundary_candidate" and item["to"] == "Run"
        for item in result["dataFlow"]["flows"]
    )
    assert "not prove" in result["dataFlow"]["proofBoundary"]


def test_architecture_proposal_requires_design_contract_and_blocks_cycle(tmp_path: Path) -> None:
    _fixture(tmp_path)
    incomplete = analyze_architecture(tmp_path, proposal={"decision": "add service"})
    complete = analyze_architecture(
        tmp_path,
        proposal={
            "decision": "keep shared API in Core",
            "invariants": ["Game depends on Core, never reverse"],
            "impactedSurfaces": ["Source/Core/Public/Shared.h", "Source/Game/Private/Worker.cpp"],
            "validationPlan": ["build", "worker regression"],
            "alternatives": ["move implementation to Game"],
            "implementationFiles": ["Source/Game/Private/Worker.cpp"],
        },
    )

    assert incomplete["proposalValidation"]["ok"] is False
    assert "invariants" in incomplete["proposalValidation"]["issues"][0]
    assert complete["proposalValidation"]["ok"] is True
    assert complete["proposalValidation"]["implementationGate"]["writesAllowed"] is True


def test_architecture_reasoning_supports_python_source_candidates(tmp_path: Path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    (source / "worker.py").write_text(
        "def run(value):\n    self_state = 'running'\n    result = value\n    return result\n",
        encoding="utf-8",
    )

    result = analyze_architecture(tmp_path, symbols=["run"])

    assert any(item["kind"] == "assignment_candidate" for item in result["dataFlow"]["flows"])
    assert any(item["kind"] == "state_assignment_candidate" for item in result["stateTransitions"]["transitions"])


def test_architecture_proposal_rejects_wrong_field_types(tmp_path: Path) -> None:
    _fixture(tmp_path)

    result = analyze_architecture(
        tmp_path,
        proposal={
            "decision": ["not a string"],
            "invariants": "not an array",
            "impactedSurfaces": "not an array",
            "validationPlan": "not an array",
            "alternatives": "not an array",
        },
    )

    validation = result["proposalValidation"]
    assert validation["ok"] is False
    assert validation["implementationGate"]["writesAllowed"] is False


def test_architecture_cycle_is_reported_once_and_closes_implementation_gate(tmp_path: Path) -> None:
    module_a = tmp_path / "Source" / "A"
    module_b = tmp_path / "Source" / "B"
    module_a.mkdir(parents=True)
    module_b.mkdir(parents=True)
    (module_a / "A.h").write_text('#include "../B/B.h"\n', encoding="utf-8")
    (module_b / "B.h").write_text('#include "../A/A.h"\n', encoding="utf-8")

    result = analyze_architecture(
        tmp_path,
        proposal={
            "decision": "preserve module direction",
            "invariants": ["no unresolved dependency cycle"],
            "impactedSurfaces": ["Source/A/A.h"],
            "validationPlan": ["compile"],
            "alternatives": ["extract a third module"],
        },
    )

    assert result["topology"]["sourceDependencyCycles"] == [["module:A", "module:B", "module:A"]]
    assert result["proposalValidation"]["ok"] is True
    assert result["proposalValidation"]["implementationGate"]["writesAllowed"] is False


def test_architecture_analysis_requires_an_explicit_project_root() -> None:
    result = analyze_architecture("")

    assert result["ok"] is False
    assert "project root not found" in result["error"]


def test_architecture_analysis_rebuilds_a_stale_supplied_graph(tmp_path: Path) -> None:
    source = tmp_path / "Source"
    source.mkdir()
    worker = source / "Worker.cpp"
    worker.write_text("void Run() {}\n", encoding="utf-8")
    stale = build_symbol_graph(tmp_path)
    worker.write_text("void Run() { CurrentState = 1; }\n", encoding="utf-8")

    result = analyze_architecture(tmp_path, symbols=["Run"], graph=stale)

    assert result["graphEvidence"]["suppliedGraphAccepted"] is False
    assert result["graphEvidence"]["suppliedGraphRebuilt"] is True
    assert result["stateTransitions"]["transitions"]


def test_architecture_analysis_fails_closed_for_unmatched_focus_symbol(tmp_path: Path) -> None:
    _fixture(tmp_path)

    result = analyze_architecture(
        tmp_path,
        symbols=["MissingWorker"],
        proposal={
            "decision": "change worker",
            "invariants": ["preserve behavior"],
            "impactedSurfaces": ["Source/Game/Private/Worker.cpp"],
            "validationPlan": ["compile"],
            "alternatives": ["no change"],
        },
    )

    assert result["ok"] is False
    assert result["focus"]["unmatchedSymbols"] == ["MissingWorker"]
    assert result["proposalValidation"]["ok"] is False
    assert result["proposalValidation"]["implementationGate"]["writesAllowed"] is False
