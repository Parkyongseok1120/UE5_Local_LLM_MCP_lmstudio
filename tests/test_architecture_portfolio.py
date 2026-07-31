from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from architecture_portfolio import (  # noqa: E402
    compare_architecture_alternatives,
    generate_architecture_portfolio,
)


def _analysis(owner: str = "", source_file: str = "") -> dict[str, object]:
    owners = []
    if owner:
        owners.append(
            {
                "id": owner,
                "files": [source_file] if source_file else [],
            }
        )
    return {
        "topology": {
            "owners": owners,
            "sourceDependencyCycles": [],
        },
        "stateTransitions": {
            "stateOwnershipCandidates": [],
            "multipleWriterCandidateCount": 0,
        },
        "lifecycle": {
            "callbacks": [],
            "pairingGaps": [],
        },
        "graphEvidence": {"complete": True},
        "focus": {"unmatchedSymbols": []},
    }


def _pattern_ids(portfolio: dict[str, object]) -> list[list[str]]:
    return [
        candidate["patternIds"]
        for candidate in portfolio["candidates"]  # type: ignore[index]
    ]


def _assert_bounded(portfolio: dict[str, object]) -> None:
    assert 3 <= portfolio["candidateCount"] <= 5  # type: ignore[operator]
    assert portfolio["implementationReady"] is False
    assert all(
        candidate["proofLevel"] == "Proposed"
        for candidate in portfolio["candidates"]  # type: ignore[index]
    )


def test_per_actor_replicated_state_rejects_global_and_asset_owners() -> None:
    portfolio = generate_architecture_portfolio(
        _analysis(
            "UHealthComponent",
            "Source/Game/Private/HealthComponent.cpp",
        ),
        objective=(
            "Per-actor replicated mutable health state with server authoritative writes"
        ),
    )

    _assert_bounded(portfolio)
    assert portfolio["provisionalRecommendation"] == "ActorComponent"
    assert portfolio["recommendedCandidate"] == "ActorComponent"
    eliminated = {
        pattern_id
        for row in portfolio["eliminatedPatterns"]
        for pattern_id in row["patternIds"]
    }
    assert {"engine_subsystem", "data_asset_config"} <= eliminated


def test_cross_map_session_state_rejects_world_scoped_owners() -> None:
    portfolio = generate_architecture_portfolio(
        _analysis(
            "UMatchGameInstanceSubsystem",
            "Source/Game/Private/MatchGameInstanceSubsystem.cpp",
        ),
        objective="Cross-map session state that must survive ordinary map travel",
    )

    _assert_bounded(portfolio)
    assert portfolio["provisionalRecommendation"] == "GameInstanceSubsystem"
    eliminated = {
        tuple(row["patternIds"]): row["hardContradictions"]
        for row in portfolio["eliminatedPatterns"]
    }
    assert ("world_subsystem",) in eliminated
    assert ("mass",) in eliminated


def test_very_high_entity_scale_prefers_mass_with_source_evidence() -> None:
    portfolio = generate_architecture_portfolio(
        _analysis(
            "UMassSpawnerSubsystem",
            "Source/Game/Private/Mass/MassSpawnerSubsystem.cpp",
        ),
        objective="Process 100000 entities at runtime with stable frame cost",
    )

    _assert_bounded(portfolio)
    assert portfolio["requirementDimensions"]["scalePerformance"]["value"] == "very_high"
    assert portfolio["provisionalRecommendation"] == "Mass Entity"
    assert portfolio["recommendedCandidate"] == "Mass Entity"
    assert ["mass", "world_subsystem"] in _pattern_ids(portfolio)


def test_predicted_attributes_prefer_gas_and_reject_mass_primary_owner() -> None:
    portfolio = generate_architecture_portfolio(
        _analysis(
            "UAbilitySystemComponent",
            "Source/Game/Private/Abilities/AbilitySystemComponent.cpp",
        ),
        objective=(
            "Server authoritative predicted replicated attributes and cooldowns"
        ),
    )

    _assert_bounded(portfolio)
    assert "Gameplay Ability System" in portfolio["provisionalRecommendation"]
    assert portfolio["requirementDimensions"]["prediction"]["value"] == "required"
    assert any(
        row["patternIds"] == ["mass"]
        for row in portfolio["eliminatedPatterns"]
    )


def test_static_designer_tuning_prefers_data_asset_config() -> None:
    portfolio = generate_architecture_portfolio(
        _analysis(
            "UCombatBalanceDataAsset",
            "Source/Game/Private/Data/CombatBalanceDataAsset.cpp",
        ),
        objective="Designer-authored static tuning in a Data Asset",
    )

    _assert_bounded(portfolio)
    assert portfolio["provisionalRecommendation"] == "DataAsset/config"
    assert portfolio["recommendedCandidate"] == "DataAsset/config"


def test_owner_recommendation_fails_closed_without_matching_source_evidence() -> None:
    portfolio = generate_architecture_portfolio(
        _analysis(),
        objective="Cross-map session state",
    )

    _assert_bounded(portfolio)
    assert portfolio["provisionalRecommendation"] == "GameInstanceSubsystem"
    assert portfolio["recommendedCandidate"] == ""
    assert portfolio["nextAction"] == "collect_source_evidence_for_owner_choice"


def test_state_owner_evidence_uses_architecture_reasoning_source_schema() -> None:
    analysis = _analysis()
    analysis["stateTransitions"]["stateOwnershipCandidates"] = [  # type: ignore[index]
        {
            "ownerCandidate": "UHealthComponent",
            "stateField": "Health",
            "writerFiles": ["Source/Game/Private/HealthComponent.cpp"],
            "evidence": [
                {
                    "filePath": "Source/Game/Private/HealthComponent.cpp",
                    "lineStart": 42,
                }
            ],
        }
    ]

    portfolio = generate_architecture_portfolio(
        analysis,
        objective="Per-actor replicated mutable health state",
    )

    assert portfolio["recommendedCandidate"] == "ActorComponent"
    candidate = portfolio["candidates"][0]
    assert candidate["ownerEvidence"]["satisfied"] is True
    assert (
        candidate["ownerEvidence"]["evidence"][0]["location"]
        == "Source/Game/Private/HealthComponent.cpp:42"
    )


def test_conflicting_requirements_fail_closed_without_inventing_candidate() -> None:
    portfolio = generate_architecture_portfolio(
        _analysis(),
        objective=(
            "Per-actor cross-map session state with predicted replicated attributes "
            "for 100000 entities"
        ),
    )

    assert portfolio["candidateCount"] == 0
    assert portfolio["portfolioStatus"] == "no_viable_candidate"
    assert portfolio["requirementConflict"] is True
    assert portfolio["recommendedCandidate"] == ""
    assert (
        portfolio["nextAction"]
        == "resolve_or_partition_conflicting_requirements"
    )
    assert portfolio["eliminatedPatterns"]


def test_adaptive_comparison_requires_owner_evidence_and_preserves_override() -> None:
    scores = {
        "fit": 4,
        "testability": 4,
        "migration": 3,
        "complexity": 3,
        "risk": 3,
        "performance": 4,
    }
    alternatives = [
        {
            "name": "ActorComponent",
            "scores": scores,
            "ownerEvidence": [{"location": "HealthComponent.cpp:10"}],
        },
        {
            "name": "Owned UObject service",
            "scores": scores,
            "ownerEvidence": [{"location": "HealthService.cpp:15"}],
        },
        {
            "name": "Unproved subsystem",
            "scores": scores,
        },
    ]

    missing_rationale = compare_architecture_alternatives(
        alternatives,
        selected_alternative="Owned UObject service",
    )
    with_rationale = compare_architecture_alternatives(
        alternatives,
        selected_alternative="Owned UObject service",
        selection_rationale="Prefer the existing isolated service seam.",
    )

    assert missing_rationale["eligibleCount"] == 2
    assert missing_rationale["ambiguous"] is True
    assert missing_rationale["selectionValid"] is False
    assert with_rationale["selectionValid"] is True
    assert any(
        "ownerEvidence is required" in issue
        for issue in missing_rationale["alternatives"][2]["issues"]
    )
