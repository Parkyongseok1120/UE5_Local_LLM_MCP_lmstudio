from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from multifile_refactor_autofix import (  # noqa: E402
    apply_multifile_delegate_header_sync_autofix,
    apply_multifile_method_rename_autofix,
    apply_subsystem_include_autofix,
)
from unreal_static_validate import read_text  # noqa: E402


def test_subsystem_include_autofix(tmp_path: Path) -> None:
    header = tmp_path / "HoldoutGlobalSubsystem.h"
    header.write_text(
        '#pragma once\n#include "CoreMinimal.h"\n#include "HoldoutGlobalSubsystem.generated.h"\n\n'
        "class UHoldoutGlobalSubsystem : public UGameInstanceSubsystem {};\n",
        encoding="utf-8",
    )
    written = apply_subsystem_include_autofix(tmp_path, [])
    assert written
    text = read_text(header)
    assert "Subsystems/GameInstanceSubsystem.h" in text


def test_delegate_header_sync_autofix(tmp_path: Path) -> None:
    (tmp_path / "HoldoutDelegateOwner.h").write_text(
        "#pragma once\nclass FHoldoutDelegateOwner { public: void HandleScoreChanged(); };\n",
        encoding="utf-8",
    )
    (tmp_path / "HoldoutDelegateOwner.cpp").write_text(
        '#include "HoldoutDelegateOwner.h"\nvoid FHoldoutDelegateOwner::HandleScoreChanged(int32 Score) {}\n',
        encoding="utf-8",
    )
    written = apply_multifile_delegate_header_sync_autofix(tmp_path)
    assert written
    assert "HandleScoreChanged(int32 Score)" in read_text(tmp_path / "HoldoutDelegateOwner.h")


def test_method_rename_autofix(tmp_path: Path) -> None:
    (tmp_path / "HoldoutRefactorComponent.h").write_text(
        "#pragma once\nclass UHoldoutRefactorComponent { public: void StartCharge(); };\n",
        encoding="utf-8",
    )
    (tmp_path / "HoldoutRefactorComponent.cpp").write_text(
        '#include "HoldoutRefactorComponent.h"\nvoid UHoldoutRefactorComponent::BeginCharge() {}\n',
        encoding="utf-8",
    )
    (tmp_path / "HoldoutRefactorConsumer.cpp").write_text(
        '#include "HoldoutRefactorComponent.h"\nvoid Use(UHoldoutRefactorComponent* C){ C->BeginCharge(); }\n',
        encoding="utf-8",
    )
    written = apply_multifile_method_rename_autofix(tmp_path)
    assert written
    assert "StartCharge" in read_text(tmp_path / "HoldoutRefactorComponent.cpp")
    assert "StartCharge" in read_text(tmp_path / "HoldoutRefactorConsumer.cpp")
