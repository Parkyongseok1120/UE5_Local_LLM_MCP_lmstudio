from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from lmstudio_unreal_wrapper import multifile_surface_blockers  # noqa: E402


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
