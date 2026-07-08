from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from bootstrap_local_holdout import (  # noqa: E402
    _write_multifile_callback_param_expand_fixture,
    _write_multifile_interface_return_type_change_fixture,
    _write_multifile_method_split_callsite_update_fixture,
    _write_multifile_subsystem_api_move_fixture,
    _write_multifile_uproperty_type_migration_fixture,
)
from multifile_refactor_autofix import (  # noqa: E402
    apply_callback_param_expand_autofix,
    apply_cpp_return_type_sync_autofix,
    apply_multifile_delegate_header_sync_autofix,
    apply_multifile_interface_implementer_autofix,
    apply_multifile_method_rename_autofix,
    apply_multifile_method_split_autofix,
    apply_multifile_refactor_autofixes,
    apply_subsystem_include_autofix,
)
from unreal_static_validate import (  # noqa: E402
    Finding,
    has_blocking_static_errors,
    read_text,
    validate_callback_function_pointer_drift,
    validate_cpp_declarations,
    validate_interface_implementer_drift,
    validate_multifile_callsite_drift,
    validate_unreal_readiness,
)


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


def test_subsystem_include_autofix_dedupes_duplicate_includes(tmp_path: Path) -> None:
    header = tmp_path / "HoldoutGlobalSubsystem.h"
    header.write_text(
        '#pragma once\n#include "CoreMinimal.h"\n#include "Subsystems/GameInstanceSubsystem.h"\n'
        '#include "Subsystems/GameInstanceSubsystem.h"\n#include "HoldoutGlobalSubsystem.generated.h"\n\n'
        "class UHoldoutGlobalSubsystem : public UGameInstanceSubsystem {};\n",
        encoding="utf-8",
    )
    written = apply_subsystem_include_autofix(
        tmp_path,
        [Finding("error", "HoldoutGlobalSubsystem.h", 1, "GENERATED_H_DUPLICATE", "duplicate include")],
    )
    assert written
    assert read_text(header).count("Subsystems/GameInstanceSubsystem.h") == 1


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


def test_interface_return_fixture_autofix(tmp_path: Path) -> None:
    _write_multifile_interface_return_type_change_fixture(tmp_path)
    written = apply_multifile_interface_implementer_autofix(tmp_path)
    assert written
    impl_h = read_text(tmp_path / "Source" / "HoldoutFixture" / "Public" / "HoldoutReturnImplementer.h")
    impl_cpp = read_text(tmp_path / "Source" / "HoldoutFixture" / "Private" / "HoldoutReturnImplementer.cpp")
    assert "bool CanUse() const override" in impl_h
    assert "bool FHoldoutReturnImplementer::CanUse() const" in impl_cpp
    assert "return true;" in impl_cpp
    assert not validate_interface_implementer_drift(tmp_path)


def test_subsystem_api_move_fixture_autofix(tmp_path: Path) -> None:
    _write_multifile_subsystem_api_move_fixture(tmp_path)
    findings = validate_unreal_readiness(tmp_path)
    assert not has_blocking_static_errors(findings)
    written = apply_multifile_refactor_autofixes(tmp_path, findings)
    assert written
    subsystem_cpp = read_text(next(tmp_path.rglob("HoldoutDataSubsystem.cpp")))
    consumer_cpp = read_text(next(tmp_path.rglob("HoldoutDataConsumer.cpp")))
    assert "ApplyData" in subsystem_cpp
    assert "ApplyData" in consumer_cpp
    assert "HandleData" not in subsystem_cpp
    assert "HandleData" not in consumer_cpp


def test_callback_param_expand_fixture_autofix(tmp_path: Path) -> None:
    _write_multifile_callback_param_expand_fixture(tmp_path)
    golden_before = read_text(tmp_path / "golden" / "Source" / "HoldoutFixture" / "Public" / "HoldoutCallbackReceiver.h")
    findings = validate_callback_function_pointer_drift(tmp_path)
    assert any(f.code == "CALLBACK_FUNCTION_POINTER_MISMATCH" for f in findings)
    written = apply_callback_param_expand_autofix(tmp_path)
    assert written
    header = read_text(next(p for p in tmp_path.rglob("HoldoutCallbackReceiver.h") if "golden" not in p.parts))
    cpp = read_text(next(p for p in tmp_path.rglob("HoldoutCallbackReceiver.cpp") if "golden" not in p.parts))
    assert "OnResult(int32 Value, bool bSuccess)" in header
    assert "OnResult(int32 Value, bool bSuccess)" in cpp
    # Exact golden comparisons: guards against a regression where a greedy
    # ret-type regex capture swallows the "public:" access label and/or the
    # "static" keyword from the header into the rebuilt declaration/definition
    # (see clean_method_ret in ue_cpp_signatures.py).
    assert header == (
        '#pragma once\n\n#include "CoreMinimal.h"\n\n'
        "class FHoldoutCallbackReceiver\n{\npublic:\n"
        "\tstatic void OnResult(int32 Value, bool bSuccess);\n};\n"
    )
    assert cpp == (
        '#include "HoldoutCallbackReceiver.h"\n\n'
        "void FHoldoutCallbackReceiver::OnResult(int32 Value, bool bSuccess)\n"
        "{\n\t(void)Value;\n\n\t(void)bSuccess;}\n"
    )
    # The cpp out-of-class definition must never carry an access-specifier
    # label or a duplicated "static" keyword (both are C2059/C2724 in MSVC).
    cpp_def_line = next(line for line in cpp.splitlines() if "FHoldoutCallbackReceiver::OnResult" in line)
    assert "public:" not in cpp_def_line
    assert "static" not in cpp_def_line
    assert read_text(tmp_path / "golden" / "Source" / "HoldoutFixture" / "Public" / "HoldoutCallbackReceiver.h") == golden_before
    assert not validate_callback_function_pointer_drift(tmp_path)


def test_subsystems_include_whitelist(tmp_path: Path) -> None:
    _write_multifile_subsystem_api_move_fixture(tmp_path)
    findings = validate_unreal_readiness(tmp_path)
    assert not any(f.code == "INCLUDE_PATH_NOT_FOUND" for f in findings)


def test_method_split_fixture_validate_and_autofix(tmp_path: Path) -> None:
    _write_multifile_method_split_callsite_update_fixture(tmp_path)
    findings = validate_multifile_callsite_drift(tmp_path)
    assert any(f.code == "MULTIFILE_CALLSITE_DRIFT" for f in findings)
    written = apply_multifile_method_split_autofix(tmp_path)
    assert written
    assert "Prepare()" in read_text(next(tmp_path.rglob("HoldoutSplitComponent.cpp")))
    assert "Commit()" in read_text(next(tmp_path.rglob("HoldoutSplitConsumer.cpp")))
    assert not validate_multifile_callsite_drift(tmp_path)


def test_uproperty_return_type_drift_detected(tmp_path: Path) -> None:
    _write_multifile_uproperty_type_migration_fixture(tmp_path)
    cpp_path = next(p for p in tmp_path.rglob("HoldoutScoreModel.cpp") if "golden" not in p.parts)
    header_path = next(p for p in tmp_path.rglob("HoldoutScoreModel.h") if "golden" not in p.parts)
    headers = {"UHoldoutScoreModel": read_text(header_path)}
    findings = validate_cpp_declarations(cpp_path, read_text(cpp_path), tmp_path, headers)
    assert any(f.code == "CPP_RETURN_TYPE_MISMATCH" for f in findings)


def test_uproperty_return_type_fixture_autofix(tmp_path: Path) -> None:
    _write_multifile_uproperty_type_migration_fixture(tmp_path)
    findings = validate_unreal_readiness(tmp_path)
    assert any(f.code == "CPP_RETURN_TYPE_MISMATCH" for f in findings)
    written = apply_cpp_return_type_sync_autofix(tmp_path)
    assert written
    cpp = read_text(next(p for p in tmp_path.rglob("HoldoutScoreModel.cpp") if "golden" not in p.parts))
    assert "float UHoldoutScoreModel::GetScore() const" in cpp
    assert "static_cast<float>(Score)" in cpp
    assert not any(f.code == "CPP_RETURN_TYPE_MISMATCH" for f in validate_unreal_readiness(tmp_path))


def test_callback_param_expand_holdout_three_file_layout(tmp_path: Path) -> None:
    _write_multifile_callback_param_expand_fixture(tmp_path)
    written = apply_callback_param_expand_autofix(tmp_path)
    assert written
    header = read_text(next(p for p in tmp_path.rglob("HoldoutCallbackReceiver.h") if "golden" not in p.parts))
    cpp = read_text(next(p for p in tmp_path.rglob("HoldoutCallbackReceiver.cpp") if "golden" not in p.parts))
    registration = read_text(next(p for p in tmp_path.rglob("HoldoutCallbackRegistration.cpp") if "golden" not in p.parts))
    assert "OnResult(int32 Value, bool bSuccess)" in header
    assert "OnResult(int32 Value, bool bSuccess)" in cpp
    assert "(void)bSuccess" in cpp or "(void) bSuccess" in cpp
    assert "public:" not in cpp.split("{", 1)[-1]
    assert "using FHoldoutCallback = void (*)(int32, bool);" in registration
    assert not validate_callback_function_pointer_drift(tmp_path)


def test_multifile_refactor_autofixes_uproperty_attempt0_path(tmp_path: Path) -> None:
    _write_multifile_uproperty_type_migration_fixture(tmp_path)
    findings = validate_unreal_readiness(tmp_path)
    written = apply_multifile_refactor_autofixes(tmp_path, findings)
    assert written
    cpp = read_text(next(p for p in tmp_path.rglob("HoldoutScoreModel.cpp") if "golden" not in p.parts))
    assert "static_cast<float>(Score)" in cpp
