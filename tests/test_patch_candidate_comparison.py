from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import pytest

from patch_candidate_comparison import compare_patch_candidates  # noqa: E402


def _candidate(
    candidate_id: str,
    diff_hash: str,
    *,
    build: bool = True,
    changed_files: list[str] | None = None,
    isolated_root: str | None = None,
) -> dict:
    return {
        "id": candidate_id,
        "changedFiles": changed_files or ["Source/Demo/Private/Thing.cpp"],
        "diffHash": diff_hash,
        "sandboxEvidence": {
            "isolatedRoot": isolated_root or f"worktrees/{candidate_id}",
            "staticPassed": True,
            "staticProof": {"ok": True, "artifactHash": f"static-{diff_hash}"},
            "buildPassed": build,
            "buildProof": {
                "ok": build,
                "artifactHash": f"build-{diff_hash}" if build else "",
            },
            "runtimeCompatible": True,
            "invariantResults": {"same observer": True, "ownership preserved": True},
        },
    }


def test_two_fully_verified_distinct_candidates_compete_and_select_best() -> None:
    result = compare_patch_candidates(
        [
            _candidate("small", "hash-a"),
            _candidate(
                "broad",
                "hash-b",
                changed_files=[
                    "Source/Demo/Private/Thing.cpp",
                    "Source/Demo/Public/Thing.h",
                ],
            ),
        ]
    )
    assert result["ok"] is True
    assert result["selectedCandidateId"] == "small"
    assert result["eligibleCount"] == 2
    assert result["competitionSatisfied"] is True
    assert result["mode"] == "competition"
    assert result["ambiguous"] is False


def test_two_submitted_but_only_one_eligible_fails_closed() -> None:
    result = compare_patch_candidates(
        [_candidate("small", "hash-a"), _candidate("broken", "hash-b", build=False)]
    )
    assert result["ok"] is False
    assert result["eligibleCount"] == 1
    assert result["competitionSatisfied"] is False
    assert (
        "at least two fully verified sandbox candidates are required for competition"
        in result["issues"]
    )


def test_duplicate_or_single_candidates_fail_closed() -> None:
    single = compare_patch_candidates([_candidate("one", "hash")])
    assert single["ok"] is False
    duplicate = compare_patch_candidates(
        [_candidate("one", "hash"), _candidate("two", "hash")]
    )
    assert duplicate["ok"] is False
    assert duplicate["eligibleCount"] == 0


def test_same_sandbox_root_cannot_count_as_independent_competition() -> None:
    result = compare_patch_candidates(
        [
            _candidate("one", "hash-a", isolated_root="worktrees/shared"),
            _candidate("two", "hash-b", isolated_root="worktrees/shared"),
        ],
        selected_candidate_id="one",
        selection_rationale="prefer the smaller patch",
    )
    assert result["ok"] is False
    assert result["eligibleCount"] == 0
    assert "candidate isolated roots must be unique" in result["issues"]


def test_override_requires_rationale() -> None:
    candidates = [
        _candidate("small", "hash-a"),
        _candidate(
            "broad",
            "hash-b",
            changed_files=[
                "Source/Demo/Private/Thing.cpp",
                "Source/Demo/Public/Thing.h",
            ],
        ),
    ]
    rejected = compare_patch_candidates(
        candidates,
        selected_candidate_id="broad",
    )
    assert rejected["ok"] is False
    assert "overriding the recommended candidate" in " ".join(rejected["issues"])

    accepted = compare_patch_candidates(
        candidates,
        selected_candidate_id="broad",
        selection_rationale="the public declaration must change with the implementation",
    )
    assert accepted["ok"] is True
    assert accepted["selectedCandidateId"] == "broad"


def test_top_score_tie_requires_explicit_selection_and_rationale() -> None:
    candidates = [_candidate("alpha", "hash-a"), _candidate("beta", "hash-b")]
    implicit = compare_patch_candidates(candidates)
    assert implicit["ok"] is False
    assert implicit["ambiguous"] is True
    assert implicit["tiedTopCandidateIds"] == ["alpha", "beta"]
    assert "required when top candidates are tied" in " ".join(implicit["issues"])

    selected_without_reason = compare_patch_candidates(
        candidates,
        selected_candidate_id="alpha",
    )
    assert selected_without_reason["ok"] is False

    explicit = compare_patch_candidates(
        candidates,
        selected_candidate_id="beta",
        selection_rationale="beta keeps the causal fix inside the existing owner",
    )
    assert explicit["ok"] is True
    assert explicit["selectedCandidateId"] == "beta"


@pytest.mark.parametrize(
    ("proof_field", "malformed_proof"),
    [
        ("staticProof", "not-an-object"),
        ("staticProof", {"ok": True, "artifactHash": 123}),
        ("buildProof", {"ok": True, "artifactHash": ["not", "text"]}),
        ("buildProof", {"ok": "true", "logPath": "Build.log"}),
    ],
)
def test_malformed_proof_cannot_make_second_candidate_eligible(
    proof_field: str,
    malformed_proof: object,
) -> None:
    malformed = _candidate("malformed", "hash-b")
    malformed["sandboxEvidence"][proof_field] = malformed_proof
    result = compare_patch_candidates(
        [_candidate("valid", "hash-a"), malformed],
    )
    assert result["ok"] is False
    assert result["eligibleCount"] == 1
    invalid = next(
        candidate for candidate in result["candidates"] if candidate["id"] == "malformed"
    )
    assert invalid["eligible"] is False
