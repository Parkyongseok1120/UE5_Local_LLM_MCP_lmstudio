from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from architecture_reasoning import analyze_architecture, validate_architecture_proposal  # noqa: E402
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


def test_architecture_analysis_reports_guarded_state_owners_and_lifecycle_boundaries(
    tmp_path: Path,
) -> None:
    source = tmp_path / "Source" / "Demo"
    source.mkdir(parents=True)
    (source / "Worker.cpp").write_text(
        """
void UWorker::BeginPlay()
{
    if (bReady)
    {
        CurrentState = EWorkerState::Running;
    }
    SetTimer(Handle, this, &UWorker::Tick, 1.0f, true);
}

void UWorker::Reset()
{
    CurrentState = EWorkerState::Idle;
}

void UWorker::EndPlay()
{
    ClearTimer(Handle);
}
""",
        encoding="utf-8",
    )

    result = analyze_architecture(tmp_path)

    transitions = result["stateTransitions"]["transitions"]
    guarded = next(item for item in transitions if item["toState"] == "EWorkerState::Running")
    assert guarded["guardCandidate"]["condition"] == "bReady"
    ownership = result["stateTransitions"]["stateOwnershipCandidates"]
    assert any(
        item["ownerCandidate"] == "UWorker"
        and item["stateField"] == "CurrentState"
        and item["multipleWriters"] is True
        for item in ownership
    )
    lifecycle = result["lifecycle"]
    assert {item["phase"] for item in lifecycle["callbacks"]} >= {
        "runtime_start",
        "runtime_stop",
    }
    assert {item["kind"] for item in lifecycle["asyncEventBoundaries"]} >= {
        "timer_schedule",
        "timer_cleanup",
    }
    assert lifecycle["pairingGaps"] == []


def test_lifecycle_boundary_analysis_reads_qualified_destructor_cleanup(
    tmp_path: Path,
) -> None:
    source = tmp_path / "Source"
    source.mkdir()
    (source / "Worker.cpp").write_text(
        """
FWorker::FWorker()
{
}

FWorker::~FWorker()
{
    ClearTimer();
}
""",
        encoding="utf-8",
    )

    lifecycle = analyze_architecture(tmp_path)["lifecycle"]

    cleanup = next(
        item
        for item in lifecycle["asyncEventBoundaries"]
        if item["kind"] == "timer_cleanup"
    )
    assert cleanup["function"] == "FWorker::~FWorker"


def test_staged_architecture_proposal_requires_traceable_design_contract(
    tmp_path: Path,
) -> None:
    _fixture(tmp_path)
    proposal = {
        "decision": "stage shared API and worker migration",
        "invariants": ["Game depends on Core, never reverse"],
        "impactedSurfaces": [
            "Source/Core/Public/Shared.h",
            "Source/Game/Private/Worker.cpp",
        ],
        "validationPlan": ["compile", "worker regression"],
        "alternatives": ["edit in place"],
        "implementationFiles": [
            "Source/Core/Public/Shared.h",
            "Source/Game/Private/Worker.cpp",
        ],
    }

    result = analyze_architecture(tmp_path, proposal=proposal)
    validation = result["proposalValidation"]

    assert validation["ok"] is False
    assert validation["designContract"]["stagedImplementation"] is True
    assert any("at least two alternatives" in item for item in validation["issues"])
    assert any("ownership is missing" in item for item in validation["issues"])
    assert any("implementationSlices" in item for item in validation["issues"])
    assert validation["implementationGate"]["writesAllowed"] is False


def test_staged_architecture_proposal_accepts_covered_acyclic_slices(
    tmp_path: Path,
) -> None:
    _fixture(tmp_path)
    invariant = "Game depends on Core, never reverse"
    proposal = {
        "decision": "stage shared API and worker migration",
        "invariants": [invariant],
        "impactedSurfaces": [
            "Source/Core/Public/Shared.h",
            "Source/Game/Private/Worker.cpp",
        ],
        "validationPlan": ["compile", "worker regression"],
        "alternatives": [
            {
                "name": "extend Core API",
                "rationale": "preserves dependency direction",
                "scores": {
                    "complexity": 2,
                    "maintainability": 5,
                    "performance": 4,
                    "risk": 2,
                },
            },
            {
                "name": "duplicate the API in Game",
                "rationale": "kept only as a rejected comparison candidate",
                "scores": {
                    "complexity": 1,
                    "maintainability": 1,
                    "performance": 4,
                    "risk": 5,
                },
            },
        ],
        "selectedAlternative": "extend Core API",
        "implementationFiles": [
            "Source/Core/Public/Shared.h",
            "Source/Game/Private/Worker.cpp",
        ],
        "ownership": {
            "stateOwner": "module:Game",
            "dataOwner": "module:Core",
            "lifecycleOwner": "module:Game",
            "failurePolicy": "leave the old call path active",
            "recoveryPolicy": "roll back the active slice",
        },
        "migrationPlan": ["add compatible Core API", "move Game callsite"],
        "validationMatrix": [
            {"invariant": invariant, "checks": ["dependency graph", "compile"]}
        ],
        "implementationSlices": [
            {
                "sliceId": "core-api",
                "files": ["Source/Core/Public/Shared.h"],
                "dependsOn": [],
                "invariants": [invariant],
                "validation": ["compile Core"],
            },
            {
                "sliceId": "game-callsite",
                "files": ["Source/Game/Private/Worker.cpp"],
                "dependsOn": ["core-api"],
                "invariants": [invariant],
                "validation": ["worker regression"],
            },
        ],
    }

    result = analyze_architecture(tmp_path, proposal=proposal)
    validation = result["proposalValidation"]

    assert validation["ok"] is True
    assert validation["designContract"]["implementationFilesCovered"] is True
    assert validation["designContract"]["invariantCoverageCount"] == 1
    assert validation["designContract"]["sliceDependencyCycle"] == []
    assert validation["implementationGate"]["writesAllowed"] is True
    assert validation["implementationGate"]["nextAction"] == "implement_next_slice"
    comparison = validation["designContract"]["alternativeComparison"]
    assert comparison["recommendedAlternative"] == "extend Core API"
    assert comparison["selectionValid"] is True


def test_architecture_analysis_generates_bounded_candidate_portfolio(
    tmp_path: Path,
) -> None:
    _fixture(tmp_path)

    result = analyze_architecture(tmp_path, symbols=["Run"])
    portfolio = result["candidatePortfolio"]

    assert portfolio["candidateCount"] == 3
    assert portfolio["implementationReady"] is False
    assert portfolio["nextAction"] == "score_source_backed_alternatives_and_select"
    assert {item["strategy"] for item in portfolio["candidates"]} == {
        "extend_existing_owner",
        "introduce_boundary_adapter",
        "extract_dedicated_owner",
    }
    assert all(item["proofLevel"] == "Proposed" for item in portfolio["candidates"])


def test_staged_architecture_proposal_rejects_unscored_or_ambiguous_selection(
    tmp_path: Path,
) -> None:
    _fixture(tmp_path)
    invariant = "preserve worker behavior"
    shared = "Source/Core/Public/Shared.h"
    worker = "Source/Game/Private/Worker.cpp"
    proposal = {
        "decision": "stage worker migration",
        "invariants": [invariant],
        "impactedSurfaces": [shared, worker],
        "validationPlan": ["compile"],
        "alternatives": [
            {
                "name": "a",
                "scores": {
                    "complexity": 3,
                    "maintainability": 4,
                    "performance": 4,
                    "risk": 3,
                },
            },
            {
                "name": "b",
                "scores": {
                    "complexity": 3,
                    "maintainability": 4,
                    "performance": 4,
                    "risk": 3,
                },
            },
        ],
        "selectedAlternative": "a",
        "implementationFiles": [shared, worker],
        "ownership": {
            "stateOwner": "module:Game",
            "dataOwner": "module:Core",
            "lifecycleOwner": "module:Game",
            "failurePolicy": "stop",
            "recoveryPolicy": "rollback",
        },
        "migrationPlan": ["stage both files"],
        "validationMatrix": [{"invariant": invariant, "checks": ["compile"]}],
        "implementationSlices": [
            {
                "sliceId": "core",
                "files": [shared],
                "dependsOn": [],
                "invariants": [invariant],
                "validation": ["compile"],
            },
            {
                "sliceId": "game",
                "files": [worker],
                "dependsOn": ["core"],
                "invariants": [invariant],
                "validation": ["compile"],
            },
        ],
    }

    validation = analyze_architecture(tmp_path, proposal=proposal)[
        "proposalValidation"
    ]

    assert validation["ok"] is False
    comparison = validation["designContract"]["alternativeComparison"]
    assert comparison["ambiguous"] is True
    assert comparison["selectionValid"] is False
    assert any("scores are ambiguous" in issue for issue in validation["issues"])


def test_staged_architecture_proposal_rejects_slice_dependency_cycle(
    tmp_path: Path,
) -> None:
    _fixture(tmp_path)
    invariant = "preserve worker behavior"
    proposal = {
        "decision": "split worker implementation",
        "invariants": [invariant],
        "impactedSurfaces": ["Source/Game/Private/Worker.cpp"],
        "validationPlan": ["compile"],
        "alternatives": ["split", "keep together"],
        "implementationFiles": [
            "Source/Core/Public/Shared.h",
            "Source/Game/Private/Worker.cpp",
        ],
        "ownership": {
            "stateOwner": "module:Game",
            "dataOwner": "module:Game",
            "lifecycleOwner": "module:Game",
            "failurePolicy": "stop",
            "recoveryPolicy": "rollback",
        },
        "migrationPlan": ["stage both files"],
        "validationMatrix": [{"invariant": invariant, "checks": ["compile"]}],
        "implementationSlices": [
            {
                "sliceId": "a",
                "files": ["Source/Core/Public/Shared.h"],
                "dependsOn": ["b"],
                "invariants": [invariant],
                "validation": ["compile"],
            },
            {
                "sliceId": "b",
                "files": ["Source/Game/Private/Worker.cpp"],
                "dependsOn": ["a"],
                "invariants": [invariant],
                "validation": ["compile"],
            },
        ],
    }

    validation = analyze_architecture(tmp_path, proposal=proposal)["proposalValidation"]

    assert validation["ok"] is False
    assert any("slice dependency cycle" in item for item in validation["issues"])
    assert validation["implementationGate"]["writesAllowed"] is False


def test_architecture_proposal_rejects_absolute_and_traversing_implementation_paths() -> None:
    analysis = {
        "topology": {"owners": [], "sourceDependencyCycles": []},
        "graphEvidence": {"complete": True, "sourceFileCount": 1},
        "focus": {"unmatchedSymbols": []},
    }
    for bad_path in (
        "../Outside.cpp",
        "Source/Game/../Outside.cpp",
        "C:/Outside.cpp",
        "/tmp/Outside.cpp",
    ):
        proposal = {
            "decision": "edit one implementation file",
            "invariants": ["preserve behavior"],
            "impactedSurfaces": [bad_path],
            "validationPlan": ["compile"],
            "alternatives": ["edit", "do nothing"],
            "implementationFiles": [bad_path],
            "implementationSlices": [
                {
                    "sliceId": "edit",
                    "files": [bad_path],
                    "dependsOn": [],
                    "invariants": ["preserve behavior"],
                    "validation": ["compile"],
                }
            ],
        }

        validation = validate_architecture_proposal(proposal, analysis)

        assert validation["ok"] is False
        assert validation["implementationGate"]["writesAllowed"] is False
        assert any(
            "project-relative" in issue or "parent traversal" in issue
            for issue in validation["issues"]
        )


def test_architecture_proposal_rejects_duplicate_slice_file_owner_and_rogue_invariant() -> None:
    invariant = "preserve behavior"
    shared = "Source/Core/Public/Shared.h"
    worker = "Source/Game/Private/Worker.cpp"
    analysis = {
        "topology": {
            "owners": [{"files": [shared, worker]}],
            "sourceDependencyCycles": [],
        },
        "graphEvidence": {"complete": True, "sourceFileCount": 2},
        "focus": {"unmatchedSymbols": []},
    }
    proposal = {
        "decision": "stage shared and worker changes",
        "invariants": [invariant],
        "impactedSurfaces": [shared, worker],
        "validationPlan": ["compile"],
        "alternatives": ["stage", "edit together"],
        "implementationFiles": [shared, worker],
        "ownership": {
            "stateOwner": "module:Game",
            "dataOwner": "module:Core",
            "lifecycleOwner": "module:Game",
            "failurePolicy": "stop",
            "recoveryPolicy": "rollback",
        },
        "migrationPlan": ["stage both files"],
        "validationMatrix": [{"invariant": invariant, "checks": ["compile"]}],
        "implementationSlices": [
            {
                "sliceId": "core",
                "files": [shared],
                "dependsOn": [],
                "invariants": [invariant],
                "validation": ["compile"],
            },
            {
                "sliceId": "game",
                "files": [shared, worker],
                "dependsOn": ["core"],
                "invariants": ["undeclared invariant"],
                "validation": ["compile"],
            },
        ],
    }

    validation = validate_architecture_proposal(proposal, analysis)

    assert validation["ok"] is False
    assert any("assigned to multiple slices" in issue for issue in validation["issues"])
    assert any("not declared by proposal" in issue for issue in validation["issues"])
    assert validation["implementationGate"]["writesAllowed"] is False


def test_game_asset_surface_requires_migration_contract(tmp_path: Path) -> None:
    source = tmp_path / "Source" / "Demo"
    source.mkdir(parents=True)
    (source / "Demo.cpp").write_text("void Demo() {}\n", encoding="utf-8")
    analysis = analyze_architecture(
        tmp_path,
        proposal={
            "decision": "Rename the lobby widget",
            "invariants": ["Lobby still loads"],
            "impactedSurfaces": ["/Game/UI/WBP_Lobby"],
            "validationPlan": ["cook", "load Lobby"],
            "alternatives": ["keep current name"],
        },
    )

    validation = analysis["proposalValidation"]
    assert validation["ok"] is False
    assert any("assetMigration is required" in issue for issue in validation["issues"])
