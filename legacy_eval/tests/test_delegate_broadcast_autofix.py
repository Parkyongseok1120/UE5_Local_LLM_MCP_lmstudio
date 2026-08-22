from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from lmstudio_unreal_wrapper import (  # noqa: E402
    apply_delegate_broadcast_autofix,
    delegate_broadcast_callsite_fix_context,
    edit_scope_blockers,
)
from unreal_static_validate import Finding, validate_delegate_broadcast_consistency  # noqa: E402


def test_delegate_broadcast_callsite_allows_cpp_only_edit() -> None:
    request = "Fix the Broadcast call to match FOnHoldoutScoreChanged payload."
    before = {
        "Source/HoldoutFixture/Private/HoldoutScoreDelegateComponent.cpp": "OnScoreChanged.Broadcast();",
    }
    after = {
        "Source/HoldoutFixture/Private/HoldoutScoreDelegateComponent.cpp": "OnScoreChanged.Broadcast(0);",
    }
    route = {"errorSubkind": "DELEGATE_BROADCAST_SIGNATURE_MISMATCH"}
    assert delegate_broadcast_callsite_fix_context(request, route)
    assert edit_scope_blockers(request, before, after, Path("."), route=route) == []


def test_delegate_handler_still_requires_header_and_cpp() -> None:
    request = "Update delegate handler declaration and definition together."
    before = {
        "Source/HoldoutFixture/Public/HoldoutDelegateOwner.h": "void HandleScoreChanged();",
        "Source/HoldoutFixture/Private/HoldoutDelegateOwner.cpp": "void HandleScoreChanged(int32 Score) {}",
    }
    after = {
        **before,
        "Source/HoldoutFixture/Private/HoldoutDelegateOwner.cpp": "void HandleScoreChanged(int32 Score) { (void)Score; }",
    }
    issues = edit_scope_blockers(request, before, after, Path("."))
    assert issues
    assert any("header declaration and .cpp definition" in issue for issue in issues)


def test_apply_delegate_broadcast_autofix(tmp_path: Path) -> None:
    cpp = tmp_path / "Source" / "Demo" / "Private" / "Score.cpp"
    cpp.parent.mkdir(parents=True)
    original = """#include "Score.h"

void Trigger()
{
\tOnScoreChanged.Broadcast();
}
"""
    cpp.write_text(original, encoding="utf-8")
    written = apply_delegate_broadcast_autofix(
        tmp_path,
        [
            Finding(
                "error",
                "Source/Demo/Private/Score.cpp",
                4,
                "DELEGATE_BROADCAST_SIGNATURE_MISMATCH",
                "missing payload",
            )
        ],
    )
    assert written == []
    assert cpp.read_text(encoding="utf-8") == original
