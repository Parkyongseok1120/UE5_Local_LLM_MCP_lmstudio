from __future__ import annotations

import copy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from feature_intent_contract import (  # noqa: E402
    DIMENSIONS,
    analyze_feature_intent_ambiguity,
    can_auto_bind_architecture_feature_intent,
    resolve_architecture_bound_feature_intent,
    resolve_feature_intent,
    target_snapshot_hash,
)


def _complete_candidate(candidate_id: str, score_token: str = "") -> dict:
    return {
        "intentId": candidate_id,
        "title": f"Candidate {candidate_id}",
        "summary": f"Bounded design {score_token}",
        "dimensions": {
            "ownershipLifetime": "World owner",
            "authorityReplication": "Server authority",
            "persistence": "Transient",
            "failureSemantics": "Fail closed",
            "userVisibleBehavior": "One visible result",
            "nonGoals": "No migration",
        },
        "acceptanceCriteria": [
            {
                "criterionId": "observable",
                "statement": "Behavior is observable.",
                "observer": "focused test",
                "oracle": "expected value equals actual value",
            }
        ],
        "reversible": True,
        "boundedScope": True,
    }


def test_generated_contract_has_three_ranked_complete_candidates() -> None:
    result = resolve_feature_intent(
        "Add a subsystem to manage player state",
        write_intent=True,
        include_full=True,
    )

    assert result["candidateCount"] == 3
    assert result["eligibleCandidateCount"] == 3
    scores = [candidate["score"] for candidate in result["candidates"]]
    assert scores == sorted(scores, reverse=True)
    for candidate in result["contract"]["candidates"]:
        assert set(candidate["dimensions"]) == set(DIMENSIONS)
        assert all(candidate["dimensions"].values())
        assert all(
            criterion["observer"] and criterion["oracle"]
            for criterion in candidate["acceptanceCriteria"]
        )


def test_low_ambiguity_bounded_change_uses_bounded_assumption() -> None:
    analysis = analyze_feature_intent_ambiguity(
        "Implement null guard in Source/Demo/Thing.cpp; local transient behavior, "
        "no replication, fail closed, no UI changes.",
        write_intent=True,
    )

    assert analysis["recommendedAction"] == "bounded_assumption"
    assert analysis["requiresResolution"] is False


def test_named_existing_owner_is_bounded_without_a_literal_file_path() -> None:
    analysis = analyze_feature_intent_ambiguity(
        "Add a cooldown timer to the existing player component without "
        "replication or persistence",
        write_intent=True,
    )

    assert analysis["ambiguityScore"] < 0.45
    assert analysis["boundedScope"] is True
    assert analysis["recommendedAction"] == "bounded_assumption"
    assert analysis["requiresResolution"] is False


def test_detailed_named_feature_with_one_ui_choice_is_bounded() -> None:
    analysis = analyze_feature_intent_ambiguity(
        "Implement AGomokuGameMode, AGomokuGameState, and AGomokuBoardActor for "
        "a 15x15 local hotseat game. Use WGomokuHUD or a simple UI widget, keep "
        "architecture clean and extensible for later multiplayer, and provide "
        "all required Unreal C++ files.",
        write_intent=True,
    )

    assert analysis["ambiguityScore"] < 0.45
    assert analysis["boundedScope"] is True
    assert analysis["recommendedAction"] == "bounded_assumption"
    assert analysis["requiresResolution"] is False


def test_detailed_local_feature_summary_stays_bounded_without_class_names() -> None:
    analysis = analyze_feature_intent_ambiguity(
        "Implement Gomoku local 2-player hotseat game with board actor, mouse "
        "click placement, turn system, win detection, restart button, personal "
        "timer, and timeout auto-place logic.",
        write_intent=True,
    )

    assert analysis["ambiguityScore"] < 0.45
    assert analysis["boundedScope"] is True
    assert analysis["recommendedAction"] == "bounded_assumption"
    assert analysis["requiresResolution"] is False


def test_validated_local_architecture_contract_resolves_without_model_reselection() -> None:
    contract = {
        "decision": "Add one bounded helper without changing existing behavior",
        "scope": {
            "networked": False,
            "runtime": "standalone",
            "validationLevel": "Bound",
            "risk": "low",
            "nonGoals": ["No persistence", "No replication"],
        },
        "ownership": {"lifecycleOwner": "existing module"},
        "validationPlan": ["compile", "run focused helper regression"],
        "hasMigrationPlan": False,
    }
    provenance = {
        "source": "validated_architecture",
        "featureIntentContract": contract,
    }

    assert can_auto_bind_architecture_feature_intent(
        slice_provenance=provenance,
        target_files=["Source/Demo/NewHelper.cpp"],
        snapshot_issues=[],
        explicit_semantic_input=False,
    ) is True

    result = resolve_architecture_bound_feature_intent(
        "Create the helper",
        architecture_contract=contract,
        target_files=["Source/Demo/NewHelper.cpp"],
        include_full=True,
    )

    assert result["ok"] is True
    assert result["selectedIntentId"] == "architecture_bound_local"
    assert result["candidateCount"] == 3
    assert result["architectureBound"]["serverOwned"] is True
    assert all(
        criterion["observer"] and criterion["oracle"]
        for criterion in result["selectedCandidate"]["acceptanceCriteria"]
    )


def test_strict_or_network_architecture_contract_cannot_auto_bind() -> None:
    base = {
        "source": "validated_architecture",
        "featureIntentContract": {
            "scope": {
                "networked": False,
                "runtime": "standalone",
                "validationLevel": "Strict",
                "risk": "low",
            },
            "hasMigrationPlan": False,
        },
    }
    assert can_auto_bind_architecture_feature_intent(
        slice_provenance=base,
        target_files=["Source/Demo/Thing.cpp"],
        snapshot_issues=[],
        explicit_semantic_input=False,
    ) is False

    networked = copy.deepcopy(base)
    networked["featureIntentContract"]["scope"].update(
        {"networked": True, "runtime": "listen_server", "validationLevel": "Bound"}
    )
    assert can_auto_bind_architecture_feature_intent(
        slice_provenance=networked,
        target_files=["Source/Demo/Thing.cpp"],
        snapshot_issues=[],
        explicit_semantic_input=False,
    ) is False


def test_broad_vague_cross_cutting_write_is_high_ambiguity() -> None:
    analysis = analyze_feature_intent_ambiguity(
        "Implement the best architecture for this whole project, maybe subsystem "
        "or component, multiplayer, save it, handle errors somehow",
        write_intent=True,
    )

    assert analysis["ambiguityScore"] >= 0.70
    assert analysis["recommendedAction"] == "user_approval"
    assert analysis["requiresResolution"] is True


def test_ambiguous_write_requires_selection_rationale_and_question_answers() -> None:
    request = "Add a subsystem to manage state"
    probe = resolve_feature_intent(request, write_intent=True)
    assert probe["errorCode"] == "FEATURE_INTENT_SELECTION_REQUIRED"
    selected = probe["candidates"][0]["intentId"]
    answers = {
        dimension: f"explicit {dimension}"
        for dimension in probe["ambiguity"]["missingDimensions"][:3]
    }

    resolved = resolve_feature_intent(
        request,
        write_intent=True,
        selected_intent_id=selected,
        selection_rationale="This is the smallest owner matching the requested lifetime.",
        blocking_question_answers=answers,
    )

    assert resolved["ok"] is True
    assert resolved["selectedIntentId"] == selected
    assert len(resolved["intentContractHash"]) == 64
    assert len(resolved["acceptanceOracleHash"]) == 64


def test_high_ambiguity_stays_closed_without_user_approval() -> None:
    request = "Implement the best architecture across multiple modules, maybe persistent or replicated"
    probe = resolve_feature_intent(request, write_intent=True)
    selected = probe["candidates"][0]["intentId"]
    answers = {
        dimension: f"explicit {dimension}"
        for dimension in probe["ambiguity"]["missingDimensions"][:3]
    }
    blocked = resolve_feature_intent(
        request,
        write_intent=True,
        selected_intent_id=selected,
        selection_rationale="Explicitly selected after comparing ownership boundaries.",
        blocking_question_answers=answers,
    )
    assert blocked["ambiguity"]["recommendedAction"] == "user_approval"
    assert blocked["errorCode"] == "FEATURE_INTENT_USER_APPROVAL_REQUIRED"

    approved = resolve_feature_intent(
        request,
        write_intent=True,
        selected_intent_id=selected,
        selection_rationale="Explicitly selected after comparing ownership boundaries.",
        blocking_question_answers=answers,
        user_approved=True,
    )
    assert approved["ok"] is True
    assert approved["intentContractHash"] == blocked["intentContractHash"]


def test_tied_candidates_fail_closed_without_explicit_selection_and_rationale() -> None:
    candidates = [
        _complete_candidate("alpha"),
        _complete_candidate("beta"),
        _complete_candidate("gamma"),
    ]
    result = resolve_feature_intent(
        "Implement local feature in Source/Demo/Thing.cpp",
        candidates=candidates,
        write_intent=False,
    )

    assert result["ok"] is False
    assert result["errorCode"] == "FEATURE_INTENT_TIE_REQUIRES_SELECTION"


def test_fewer_than_two_eligible_candidates_is_rejected() -> None:
    candidates = [
        _complete_candidate("alpha"),
        _complete_candidate("beta"),
        _complete_candidate("gamma"),
    ]
    for candidate in candidates[1:]:
        candidate["dimensions"]["failureSemantics"] = ""

    result = resolve_feature_intent(
        "Implement feature",
        candidates=candidates,
        write_intent=True,
    )

    assert result["ok"] is False
    assert result["errorCode"] == "FEATURE_INTENT_INSUFFICIENT_ELIGIBLE"


def test_compact_default_does_not_expose_candidate_dimension_bodies() -> None:
    result = resolve_feature_intent("Add a subsystem", write_intent=True)

    assert "contract" not in result
    assert "selectedCandidate" not in result
    assert all("dimensions" not in candidate for candidate in result["candidates"])
    assert all("acceptanceCriteria" not in candidate for candidate in result["candidates"])


def test_hashes_are_deterministic_and_snapshot_order_independent() -> None:
    first = resolve_feature_intent("Add a subsystem", write_intent=True)
    second = resolve_feature_intent("Add a subsystem", write_intent=True)
    assert first["intentContractHash"] == second["intentContractHash"]

    snapshots = [
        {"path": "B.cpp", "absolutePath": "C:/P/B.cpp", "exists": True, "fileHash": "2"},
        {"path": "A.cpp", "absolutePath": "C:/P/A.cpp", "exists": True, "fileHash": "1"},
    ]
    reversed_snapshots = list(reversed(copy.deepcopy(snapshots)))
    assert target_snapshot_hash(snapshots) == target_snapshot_hash(reversed_snapshots)
