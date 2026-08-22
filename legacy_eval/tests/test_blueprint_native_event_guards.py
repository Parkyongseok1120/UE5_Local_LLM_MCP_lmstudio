from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from lmstudio_unreal_wrapper import repeat_patch_blockers  # noqa: E402
from wrapper_guards import (  # noqa: E402
    blueprint_native_event_dual_surface_blockers,
    blueprint_native_event_impl_fix_context,
    edit_scope_blockers,
)


def test_cpp_only_allowed_when_header_blocked_for_repeat_patch() -> None:
    bundle = {
        "patches": [
            {
                "path": "Source/Game/Holdout.cpp",
                "oldText": "old",
                "newText": "new",
            }
        ]
    }
    blockers = repeat_patch_blockers(bundle, ["Source/Game/Holdout.h"])
    assert blockers == []


def test_dual_surface_header_and_cpp_blocked() -> None:
    bundle = {
        "patches": [
            {
                "path": "Source/Game/Holdout.h",
                "oldText": "old",
                "newText": "virtual void OnHoldoutEvent_Implementation();",
            },
            {
                "path": "Source/Game/Holdout.cpp",
                "oldText": "old",
                "newText": "void A::OnHoldoutEvent_Implementation() {}",
            },
        ]
    }
    assert blueprint_native_event_dual_surface_blockers(bundle)


def test_blueprint_context_allows_cpp_only_edit_scope(tmp_path: Path) -> None:
    header = tmp_path / "Holdout.h"
    cpp = tmp_path / "Holdout.cpp"
    header.write_text("void Event();\n", encoding="utf-8")
    cpp.write_text("void A::Event() {}\n", encoding="utf-8")
    before = {
        "Holdout.h": header.read_text(encoding="utf-8"),
        "Holdout.cpp": cpp.read_text(encoding="utf-8"),
    }
    after = dict(before)
    after["Holdout.cpp"] = "void A::OnHoldoutEvent_Implementation() {}\n"
    assert blueprint_native_event_impl_fix_context("BlueprintNativeEvent missing matching cpp _Implementation")
    issues = edit_scope_blockers(
        "BlueprintNativeEvent missing matching cpp _Implementation",
        before,
        after,
        tmp_path,
    )
    assert not issues
