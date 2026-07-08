from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from wrapper_guards import multifile_surface_blockers  # noqa: E402
from unreal_static_validate import Finding  # noqa: E402


def test_multifile_delegate_header_only_blocked() -> None:
    before = {
        "Source/HoldoutFixture/Public/HoldoutDelegateOwner.h": "void HandleScoreChanged();",
        "Source/HoldoutFixture/Private/HoldoutDelegateOwner.cpp": "void HandleScoreChanged(int32 Score) {}",
    }
    after = {
        **before,
        "Source/HoldoutFixture/Public/HoldoutDelegateOwner.h": "void HandleScoreChanged(int32 Score);",
    }
    issues = multifile_surface_blockers(
        "Fix delegate signature drift across declaration and definition.",
        before,
        after,
        Path("."),
        mode="multifile_refactor",
    )
    assert issues
    assert any("header declaration changed without matching .cpp" in issue for issue in issues)


def test_multifile_interface_single_file_blocked() -> None:
    before = {
        "Source/HoldoutFixture/Public/HoldoutActionImplementer.h": "void ApplyInteraction(int32 Strength);",
        "Source/HoldoutFixture/Private/HoldoutActionImplementer.cpp": "void ApplyInteraction(int32 Strength) {}",
    }
    after = {
        **before,
        "Source/HoldoutFixture/Public/HoldoutActionImplementer.h": "void ApplyInteraction(float Strength);",
    }
    issues = multifile_surface_blockers(
        "Fix interface implementer signature mismatch.",
        before,
        after,
        Path("."),
        mode="multifile_refactor",
    )
    assert issues
    assert any("Interface mismatch" in issue for issue in issues)


def test_multifile_enforced_for_multifile_request_text() -> None:
    before = {
        "Source/HoldoutFixture/Public/HoldoutDelegateOwner.h": "void HandleScoreChanged();",
        "Source/HoldoutFixture/Private/HoldoutDelegateOwner.cpp": "void HandleScoreChanged(int32 Score) {}",
    }
    after = {
        **before,
        "Source/HoldoutFixture/Public/HoldoutDelegateOwner.h": "void HandleScoreChanged(int32 Score);",
    }
    issues = multifile_surface_blockers(
        "Fix multi-file delegate handler declaration and definition drift.",
        before,
        after,
        Path("."),
        mode="compile_fix",
    )
    assert issues


def test_partial_apply_disabled_on_first_attempt() -> None:
    from wrapper_guards import scope_blocker_allows_partial_apply

    blockers = ["header declaration changed without matching .cpp definition"]
    assert scope_blocker_allows_partial_apply("multifile_refactor", blockers, attempt=1) is False
    assert scope_blocker_allows_partial_apply("multifile_refactor", blockers, attempt=2) is True


def test_multifile_uproperty_cpp_only_authoritative_header_allowed() -> None:
    before = {
        "Source/HoldoutFixture/Public/HoldoutScoreModel.h": "float GetScore() const;",
        "Source/HoldoutFixture/Private/HoldoutScoreModel.cpp": "int32 UHoldoutScoreModel::GetScore() const { return Score; }",
    }
    after = {
        **before,
        "Source/HoldoutFixture/Private/HoldoutScoreModel.cpp": (
            "float UHoldoutScoreModel::GetScore() const { return static_cast<float>(Score); }"
        ),
    }
    findings = [
        Finding(
            "warning",
            "Source/HoldoutFixture/Private/HoldoutScoreModel.cpp",
            1,
            "CPP_RETURN_TYPE_MISMATCH",
            "return type drift",
        )
    ]
    issues = multifile_surface_blockers(
        "Fix the reflected score type migration so the UPROPERTY, UFUNCTION declaration, and cpp definition agree.",
        before,
        after,
        Path("."),
        mode="multifile_refactor",
        findings=findings,
    )
    assert not any("without matching header declaration" in issue for issue in issues)


def test_multifile_callback_two_file_patch_allowed() -> None:
    before = {
        "Source/HoldoutFixture/Public/HoldoutCallbackReceiver.h": "static void OnResult(int32 Value);",
        "Source/HoldoutFixture/Private/HoldoutCallbackReceiver.cpp": "void FHoldoutCallbackReceiver::OnResult(int32 Value) {}",
        "Source/HoldoutFixture/Private/HoldoutCallbackRegistration.cpp": "return &FHoldoutCallbackReceiver::OnResult;",
    }
    after = {
        **before,
        "Source/HoldoutFixture/Public/HoldoutCallbackReceiver.h": "static void OnResult(int32 Value, bool bSuccess);",
        "Source/HoldoutFixture/Private/HoldoutCallbackReceiver.cpp": (
            "void FHoldoutCallbackReceiver::OnResult(int32 Value, bool bSuccess) {}"
        ),
    }
    issues = multifile_surface_blockers(
        "Fix the expanded callback parameter list across declaration, definition, and registration callsite.",
        before,
        after,
        Path("."),
        mode="multifile_refactor",
    )
    assert not issues
