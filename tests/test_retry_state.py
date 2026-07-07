from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import retry_state  # noqa: E402


def test_same_error_repeated_detects_stable_error_key():
    first = retry_state.make_attempt_record(
        attempt=1,
        passed=False,
        error_message="fatal error C1083: Cannot open include file",
        error_code="C1083",
        error_subkind="C1083_MISSING_INCLUDE",
        changed_paths=["Source/Game/Game.Build.cs"],
    )
    second = retry_state.make_attempt_record(
        attempt=2,
        passed=False,
        error_message="fatal error C1083: Cannot open include file",
        error_code="C1083",
        error_subkind="C1083_MISSING_INCLUDE",
        changed_paths=["Source/Game/Game.Build.cs"],
    )

    assert retry_state.same_error_repeated(first, second) is True
    recommendation = retry_state.recommend_retry_action(first, second)
    assert recommendation["sameErrorRepeated"] is True
    assert recommendation["action"] == "escalate_routing"


def test_noop_edit_recommends_new_evidence():
    current = retry_state.make_attempt_record(attempt=1, passed=False, error_message="still failing", changed_paths=[])

    assert current["noOpEdit"] is True
    assert retry_state.detect_noop_edit([]) is True
    assert retry_state.recommend_retry_action(None, current)["action"] == "force_new_evidence"


def test_repeat_error_escalates_after_two_attempts():
    first = retry_state.make_attempt_record(
        attempt=1,
        passed=False,
        error_message="fatal error C1083: Cannot open include file",
        error_code="C1083",
        error_subkind="C1083_MISSING_INCLUDE",
        changed_paths=["Source/Game/Private/A.cpp"],
    )
    second = retry_state.make_attempt_record(
        attempt=2,
        passed=False,
        error_message="fatal error C1083: Cannot open include file",
        error_code="C1083",
        error_subkind="C1083_MISSING_INCLUDE",
        changed_paths=["Source/Game/Private/A.cpp"],
    )
    third = retry_state.make_attempt_record(
        attempt=3,
        passed=False,
        error_message="fatal error C1083: Cannot open include file",
        error_code="C1083",
        error_subkind="C1083_MISSING_INCLUDE",
        changed_paths=["Source/Game/Private/B.cpp"],
    )
    recommendation = retry_state.recommend_retry_action(second, third, attempts=[first, second])
    assert recommendation["sameErrorRepeatCount"] >= 2
    assert recommendation["action"] == "escalate_evidence"
    assert recommendation["deltaTopKBoost"] >= 2
    assert "Source/Game/Private/A.cpp" in recommendation["blockedRepeatPaths"]


def test_third_repeat_stops_with_diagnosis_report():
    attempts = []
    for idx in range(1, 5):
        attempts.append(
            retry_state.make_attempt_record(
                attempt=idx,
                passed=False,
                error_message="same linker error LNK2019 unresolved external",
                error_code="LNK2019",
                error_subkind="LINKER_GENERIC",
                changed_paths=[f"Source/Game/Private/File{idx}.cpp"],
            )
        )
    recommendation = retry_state.recommend_retry_action(attempts[-2], attempts[-1], attempts=attempts[:-1])
    assert recommendation["action"] == "stop_diagnosis_report"
    assert recommendation["escalationLevel"] == 3
