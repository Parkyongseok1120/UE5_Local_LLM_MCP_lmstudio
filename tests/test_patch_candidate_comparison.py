from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from patch_candidate_comparison import compare_patch_candidates  # noqa: E402


def _candidate(candidate_id: str, diff_hash: str, *, build: bool = True) -> dict:
    return {
        "id": candidate_id,
        "changedFiles": ["Source/Demo/Private/Thing.cpp"],
        "diffHash": diff_hash,
        "sandboxEvidence": {
            "isolatedRoot": f"worktrees/{candidate_id}",
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


def test_two_to_four_distinct_candidates_select_verified_patch() -> None:
    result = compare_patch_candidates(
        [_candidate("small", "hash-a"), _candidate("broken", "hash-b", build=False)]
    )
    assert result["ok"] is True
    assert result["selectedCandidateId"] == "small"
    assert result["eligibleCount"] == 1


def test_duplicate_or_single_candidates_fail_closed() -> None:
    single = compare_patch_candidates([_candidate("one", "hash")])
    assert single["ok"] is False
    duplicate = compare_patch_candidates(
        [_candidate("one", "hash"), _candidate("two", "hash")]
    )
    assert duplicate["ok"] is False
