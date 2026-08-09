from __future__ import annotations

import copy
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

    assert portfolio["version"] == 2
    assert 3 <= portfolio["candidateCount"] <= 5
    assert portfolio["implementationReady"] is False
    assert portfolio["nextAction"] in {
        "collect_source_evidence_for_owner_choice",
        "resolve_ambiguous_candidates_with_rationale",
        "review_ranked_candidates_and_select",
    }
    assert all(item["patternIds"] for item in portfolio["candidates"])
    assert all(
        set(item["scores"])
        == {"fit", "testability", "migration", "complexity", "risk", "performance"}
        for item in portfolio["candidates"]
    )
    assert all(item["ownerEvidence"]["required"] for item in portfolio["candidates"])
    assert all(item["proofLevel"] == "Proposed" for item in portfolio["candidates"])


def _unreal_lobby_fixture(root: Path) -> None:
    source = root / "Source" / "Lobby"
    source.mkdir(parents=True)
    (source / "LobbyGameState.h").write_text(
        """
#include "GameFramework/GameStateBase.h"
class ALobbyGameState : public AGameStateBase {};
""",
        encoding="utf-8",
    )
    (source / "LobbyGameMode.h").write_text(
        """
#include "GameFramework/GameModeBase.h"
class ALobbyGameMode : public AGameModeBase
{
    void SetReady();
};
""",
        encoding="utf-8",
    )
    (source / "LobbyGameMode.cpp").write_text(
        '#include "LobbyGameMode.h"\nvoid ALobbyGameMode::SetReady() {}\n',
        encoding="utf-8",
    )
    (source / "LobbyPlayerController.h").write_text(
        """
#include "GameFramework/PlayerController.h"
class ALobbyPlayerController : public APlayerController {};
""",
        encoding="utf-8",
    )
    (source / "LobbyPlayerController.cpp").write_text(
        '#include "LobbyPlayerController.h"\n',
        encoding="utf-8",
    )


def test_networked_proposal_rejects_vague_rpc_and_missing_lifecycle_contract(
    tmp_path: Path,
) -> None:
    _fixture(tmp_path)
    proposal = {
        "decision": "Use an authoritative multiplayer lobby with replicated ready state",
        "invariants": ["Only the server changes ready state"],
        "impactedSurfaces": ["Source/Game/Private/Worker.cpp", "AOwnedController"],
        "validationPlan": ["static validation", "build", "automation"],
        "alternatives": ["reuse current owners", "add a manager"],
        "ownership": {
            "stateOwner": "server rules owner",
            "dataOwner": "replicated state owner",
            "lifecycleOwner": "server lifecycle owner",
            "failurePolicy": "reject invalid requests",
            "recoveryPolicy": "reset on disconnect",
        },
        "networking": {
            "authorityOwner": "server rules owner",
            "clientInitiated": True,
            "requestPath": ["client", "RPC or local call", "server"],
            "replicatedState": ["ready"],
        },
    }

    validation = analyze_architecture(tmp_path, proposal=proposal)["proposalValidation"]

    assert validation["ok"] is False
    assert validation["designContract"]["networkedProposal"] is True
    assert validation["designContract"]["rpcPathConcrete"] is False
    assert any("callable RPC ownership contract" in item for item in validation["issues"])
    assert any("stateInventory" in item for item in validation["issues"])
    assert any("lifecycleTransitions" in item for item in validation["issues"])


def test_networked_proposal_accepts_concrete_rpc_state_and_lifecycle_contract(
    tmp_path: Path,
) -> None:
    _fixture(tmp_path)
    proposal = {
        "decision": "Use a server-authoritative network flow with one replicated state owner",
        "invariants": ["Only the authority commits state"],
        "impactedSurfaces": ["Source/Game/Private/Worker.cpp", "AOwnedController"],
        "validationPlan": [
            "static validation",
            "build/compile",
            "automation regression",
            "RPC ownership and owning connection callability",
        ],
        "alternatives": ["reuse current owner", "add a service"],
        "ownership": {
            "stateOwner": "AReplicatedState",
            "dataOwner": "AReplicatedState",
            "lifecycleOwner": "AServerRules",
            "failurePolicy": "reject before commit",
            "recoveryPolicy": "restore the pre-event state",
        },
        "networking": {
            "authorityOwner": "AServerRules",
            "clientInitiated": True,
            "requestPath": [
                "owning client input",
                "AOwnedController::Server_RequestAction",
                "AServerRules::ValidateAndCommit",
            ],
            "rpcOwner": "AOwnedController",
            "owningConnection": "the invoking client's owning connection",
            "serverValidation": "validate identity and current phase before commit",
            "replicatedState": ["AReplicatedState::Phase"],
        },
        "stateInventory": [
            {
                "state": "Phase",
                "owner": "AReplicatedState",
                "lifetime": "world",
                "authority": "server authoritative",
                "source": "existing",
                "cleanup": "reset during world restart",
            }
        ],
        "lifecycleTransitions": [
            {
                "event": "client action",
                "owner": "AServerRules",
                "preconditions": ["valid owning connection"],
                "commitPoint": "after server validation",
                "failureRecovery": "leave state unchanged",
                "cleanup": "clear pending request",
            }
        ],
    }

    validation = analyze_architecture(tmp_path, proposal=proposal)["proposalValidation"]

    assert validation["ok"] is True, validation["issues"]
    contract = validation["designContract"]
    assert contract["networkingComplete"] is True
    assert contract["rpcPathConcrete"] is True
    assert contract["duplicateTruthSources"] == []

    server_owned_rpc = copy.deepcopy(proposal)
    server_owned_rpc["networking"]["rpcOwner"] = "AGomokuGameMode::Server_SetReady"
    rejected_rpc = analyze_architecture(
        tmp_path, proposal=server_owned_rpc
    )["proposalValidation"]
    assert rejected_rpc["ok"] is False
    assert any("server-only/server-owned" in item for item in rejected_rpc["issues"])
    assert any(
        row["jsonPath"] == "networking.rpcOwner"
        for row in rejected_rpc["repairRequirements"]
    )

    bad_connection = copy.deepcopy(proposal)
    bad_connection["networking"]["owningConnection"] = (
        "APlayerController->GetWorld()->GetAuthGameMode()"
    )
    rejected_connection = analyze_architecture(
        tmp_path, proposal=bad_connection
    )["proposalValidation"]
    assert any("does not prove" in item for item in rejected_connection["issues"])

    missing_rpc_surface = copy.deepcopy(proposal)
    missing_rpc_surface["impactedSurfaces"] = ["Source/Game/Private/Worker.cpp"]
    rejected_surface = analyze_architecture(
        tmp_path, proposal=missing_rpc_surface
    )["proposalValidation"]
    assert any("absent from impacted surfaces" in item for item in rejected_surface["issues"])

    ambiguous_owner = copy.deepcopy(proposal)
    ambiguous_owner["stateInventory"][0]["owner"] = "AServerRules + AReplicatedState"
    rejected_owner = analyze_architecture(
        tmp_path, proposal=ambiguous_owner
    )["proposalValidation"]
    assert any("multiple/ambiguous owners" in item for item in rejected_owner["issues"])

    ungrounded_roster = copy.deepcopy(proposal)
    ungrounded_roster["stateInventory"][0]["state"] = "Lobby membership"
    rejected_roster = analyze_architecture(
        tmp_path, proposal=ungrounded_roster
    )["proposalValidation"]
    assert any("sourceEvidence" in item for item in rejected_roster["issues"])

    hidden_tracking = copy.deepcopy(proposal)
    hidden_tracking["stateInventory"][0].update({
        "state": "Lobby membership",
        "sourceEvidence": "AGameStateBase::PlayerArray",
    })
    hidden_tracking["lifecycleTransitions"][0]["commitPoint"] = (
        "add player to lobby tracking after validation"
    )
    rejected_tracking = analyze_architecture(
        tmp_path, proposal=hidden_tracking
    )["proposalValidation"]
    assert any("separate mutable participant" in item for item in rejected_tracking["issues"])

    missing_identity_state = copy.deepcopy(proposal)
    missing_identity_state["invariants"].append(
        "Participant identity and identifier must remain unique"
    )
    rejected_identity = analyze_architecture(
        tmp_path, proposal=missing_identity_state
    )["proposalValidation"]
    assert any("absent from stateInventory" in item for item in rejected_identity["issues"])


def test_network_lifecycle_event_detection_handles_inflections_and_not_bare_map(
    tmp_path: Path,
) -> None:
    _fixture(tmp_path)
    transition = {
        "owner": "AServerRules",
        "preconditions": ["authority"],
        "commitPoint": "after validation",
        "failureRecovery": "leave state unchanged",
        "cleanup": "clear pending state",
    }
    proposal = {
        "decision": "Support players who join, leave, and restart in authoritative multiplayer",
        "invariants": ["Players join, leave, and restart safely"],
        "impactedSurfaces": ["Source/Game/Private/Worker.cpp", "AOwnedController"],
        "validationPlan": ["build", "RPC ownership and owning connection callability"],
        "alternatives": ["reuse", "replace"],
        "networking": {
            "authorityOwner": "AServerRules",
            "clientInitiated": True,
            "requestPath": ["client input", "AOwnedController RPC", "AServerRules commit"],
            "rpcOwner": "AOwnedController",
            "owningConnection": "invoking client's owning connection",
            "serverValidation": "validate authority and identity",
            "replicatedState": ["phase"],
        },
        "stateInventory": [{
            "state": "phase", "owner": "AReplicatedState", "lifetime": "world",
            "authority": "server authoritative", "source": "new", "cleanup": "reset",
        }],
        "lifecycleTransitions": [
            {"event": "Player joins lobby", **transition},
            {"event": "Player leaves lobby", **transition},
            {"event": "RestartGame", **transition},
        ],
    }
    accepted = analyze_architecture(tmp_path, proposal=proposal)["proposalValidation"]
    assert not any("lifecycleTransitions missing" in item for item in accepted["issues"])

    travel = copy.deepcopy(proposal)
    travel["decision"] += " and performs game transition travel"
    travel["lifecycleTransitions"] = [{"event": "Player joins lobby map", **transition}]
    rejected = analyze_architecture(tmp_path, proposal=travel)["proposalValidation"]
    assert any("travel" in item for item in rejected["issues"])
    assert any(
        row["jsonPath"] == "lifecycleTransitions"
        for row in rejected["repairRequirements"]
    )

    unsafe_commit = copy.deepcopy(proposal)
    unsafe_commit["decision"] += " and performs ServerTravel"
    unsafe_commit["lifecycleTransitions"].append({
        "event": "ServerTravel",
        "owner": "AServerRules",
        "preconditions": ["authority"],
        "commitPoint": "mark match started before travel",
        "failureRecovery": "no rollback",
        "cleanup": "none",
    })
    rejected_commit = analyze_architecture(
        tmp_path, proposal=unsafe_commit
    )["proposalValidation"]
    assert any("without rollback" in item for item in rejected_commit["issues"])
    assert any("seamless/non-seamless" in item for item in rejected_commit["issues"])


def test_unreal_lobby_rejects_duplicate_framework_roster_and_hidden_identity_index(
    tmp_path: Path,
) -> None:
    _unreal_lobby_fixture(tmp_path)
    proposal = {
        "decision": "Use an authoritative multiplayer lobby without duplicate truth",
        "invariants": [
            "Only the server mutates lobby state",
            "Participant identifier is unique in [0..3] and safely reused",
        ],
        "impactedSurfaces": [
            "ALobbyGameMode",
            "ALobbyGameState",
            "ALobbyPlayerController",
        ],
        "validationPlan": ["build", "RPC ownership and owning connection callability"],
        "alternatives": ["reuse framework state", "add lobby state"],
        "networking": {
            "authorityOwner": "ALobbyGameMode",
            "clientInitiated": True,
            "requestPath": [
                "client input",
                "ALobbyPlayerController::Server_SetReady",
                "ALobbyGameMode::SetReady",
            ],
            "rpcOwner": "ALobbyPlayerController",
            "owningConnection": "owned by the requesting client's owning connection",
            "serverValidation": "authority, membership, and phase",
            "replicatedState": ["TArray<APlayerState*> LobbyParticipants"],
        },
        "stateInventory": [
            {
                "state": "Lobby participant set",
                "owner": "ALobbyGameMode",
                "lifetime": "join through restart",
                "authority": "server authoritative",
                "source": "new",
                "cleanup": "remove on leave and clear on restart",
            },
            {
                "state": "Participant identifier",
                "owner": "APlayerState",
                "lifetime": "connection",
                "authority": "server authoritative",
                "source": "existing",
                "cleanup": "reset to 0 and remove from TSet<int32> OccupiedIds",
            },
        ],
        "lifecycleTransitions": [{
            "event": "join leave restart",
            "owner": "ALobbyGameMode",
            "preconditions": ["authority"],
            "commitPoint": "update LobbyParticipants and OccupiedIds",
            "failureRecovery": "leave canonical state unchanged",
            "cleanup": "clear stale entries",
        }],
    }

    validation = analyze_architecture(tmp_path, proposal=proposal)["proposalValidation"]

    assert validation["ok"] is False
    assert any("AGameStateBase::PlayerArray" in item for item in validation["issues"])
    assert any("OccupiedIds" in item and "stateInventory" in item for item in validation["issues"])
    assert any("validValues" in item for item in validation["issues"])
    assert any("valid identifier range" in item for item in validation["issues"])
    assert any("owner-qualified" in item for item in validation["issues"])


def test_unreal_lobby_requires_membership_truth_source_in_state_inventory(
    tmp_path: Path,
) -> None:
    _unreal_lobby_fixture(tmp_path)
    proposal = {
        "decision": "Keep authoritative lobby participant tracking in the existing flow",
        "invariants": ["Join and logout clean membership safely"],
        "impactedSurfaces": ["ALobbyGameMode", "ALobbyGameState"],
        "validationPlan": ["build"],
        "alternatives": ["reuse", "add manager"],
        "networking": {
            "authorityOwner": "ALobbyGameMode",
            "clientInitiated": False,
            "replicatedState": ["ALobbyGameState::Phase"],
        },
        "stateInventory": [{
            "state": "Phase", "owner": "ALobbyGameState", "lifetime": "world",
            "authority": "server authoritative", "source": "existing", "cleanup": "reset",
        }],
        "lifecycleTransitions": [{
            "event": "PostLogin and Logout", "owner": "ALobbyGameMode",
            "preconditions": ["authority"], "commitPoint": "update participant tracking",
            "failureRecovery": "leave state unchanged", "cleanup": "remove player",
        }],
    }

    validation = analyze_architecture(tmp_path, proposal=proposal)["proposalValidation"]

    assert any(
        "membership/roster" in item and "AGameStateBase::PlayerArray" in item
        for item in validation["issues"]
    )


def test_unreal_request_path_requires_missing_method_implementation_surfaces(
    tmp_path: Path,
) -> None:
    _unreal_lobby_fixture(tmp_path)
    proposal = {
        "decision": "Route a client lobby command through server authority",
        "invariants": ["Only authority commits state"],
        "impactedSurfaces": ["ALobbyPlayerController", "ALobbyGameMode", "ALobbyGameState"],
        "validationPlan": ["RPC ownership and owning connection callability"],
        "alternatives": ["route through controller", "server-only command"],
        "implementationFiles": ["Source/Lobby/LobbyPlayerController.h"],
        "networking": {
            "authorityOwner": "ALobbyGameMode",
            "clientInitiated": True,
            "requestPath": [
                "ALobbyPlayerController::Server_SetReady",
                "ALobbyGameMode::SetReady",
                "ALobbyGameState::OnAllPlayersReady",
            ],
            "rpcOwner": "ALobbyPlayerController",
            "owningConnection": "controller is owned by the requesting client's owning connection",
            "serverValidation": "authority and membership",
            "replicatedState": ["ALobbyGameState::Phase"],
        },
        "stateInventory": [{
            "state": "Phase", "owner": "ALobbyGameState", "lifetime": "world",
            "authority": "server authoritative", "source": "existing", "cleanup": "reset",
        }],
        "lifecycleTransitions": [{
            "event": "ready request", "owner": "ALobbyGameMode", "preconditions": ["authority"],
            "commitPoint": "after validation", "failureRecovery": "no mutation", "cleanup": "none",
        }],
    }

    validation = analyze_architecture(tmp_path, proposal=proposal)["proposalValidation"]

    assert validation["ok"] is False
    assert any("ALobbyPlayerController::Server_SetReady" in item for item in validation["issues"])
    assert any("LobbyPlayerController.cpp" in item for item in validation["issues"])
    assert any("ALobbyGameState::OnAllPlayersReady" in item for item in validation["issues"])
    assert any("LobbyGameState.h" in item for item in validation["issues"])


def test_unreal_nonseamless_travel_requires_reconstruction_contract_and_rejects_streaming_mix(
    tmp_path: Path,
) -> None:
    _unreal_lobby_fixture(tmp_path)
    transition = {
        "event": "ServerTravel",
        "owner": "ALobbyGameMode",
        "preconditions": ["all players ready"],
        "commitPoint": "after ServerTravel call",
        "failureRecovery": "keep lobby state if ServerTravel fails",
        "cleanup": "reconstruct players in the new world",
        "travelMode": "non-seamless",
    }
    proposal = {
        "decision": "Start an authoritative match using ServerTravel",
        "invariants": ["Travel preserves or reconstructs required state"],
        "impactedSurfaces": ["ALobbyGameMode", "ALobbyGameState"],
        "validationPlan": ["build"],
        "alternatives": ["seamless", "non-seamless"],
        "networking": {
            "authorityOwner": "ALobbyGameMode", "clientInitiated": False,
            "replicatedState": ["ALobbyGameState::Phase"],
        },
        "stateInventory": [{
            "state": "Phase", "owner": "ALobbyGameState", "lifetime": "world",
            "authority": "server authoritative", "source": "existing", "cleanup": "reset",
        }],
        "lifecycleTransitions": [transition],
    }

    validation = analyze_architecture(tmp_path, proposal=proposal)["proposalValidation"]
    assert any("reconstructionSource" in item for item in validation["issues"])
    assert any("completionSignal" in item for item in validation["issues"])

    mixed = copy.deepcopy(proposal)
    mixed["lifecycleTransitions"][0].update({
        "cleanup": "non-seamless level streaming reconstructs players",
        "reconstructionSource": "server-owned session data",
        "completionSignal": "post-load world initialization",
    })
    rejected_mixed = analyze_architecture(tmp_path, proposal=mixed)["proposalValidation"]
    assert any("level streaming" in item for item in rejected_mixed["issues"])


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


def test_uncovered_implementation_files_allow_scope_or_slice_repair() -> None:
    invariant = "preserve behavior"
    covered = "Source/Game/Private/Covered.cpp"
    uncovered = "Source/Game/Private/Uncovered.cpp"
    analysis = {
        "topology": {
            "owners": [{"files": [covered, uncovered]}],
            "sourceDependencyCycles": [],
        },
        "graphEvidence": {"complete": True, "sourceFileCount": 2},
        "focus": {"unmatchedSymbols": []},
    }
    proposal = {
        "decision": "stage a bounded change",
        "invariants": [invariant],
        "impactedSurfaces": [covered, uncovered],
        "validationPlan": ["compile"],
        "alternatives": ["stage", "defer"],
        "implementationFiles": [covered, uncovered],
        "implementationSlices": [
            {
                "sliceId": "covered",
                "files": [covered],
                "dependsOn": [],
                "invariants": [invariant],
                "validation": ["compile"],
            }
        ],
    }

    validation = validate_architecture_proposal(proposal, analysis)
    repair_paths = {
        row["jsonPath"]
        for row in validation["repairRequirements"]
        if "not covered by implementationSlices" in row["constraint"]
    }

    assert repair_paths == {"implementationSlices", "implementationFiles"}


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
