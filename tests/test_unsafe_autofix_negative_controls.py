from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from autofix_runtime import AutofixStep, run_autofix_pipeline  # noqa: E402
from lmstudio_unreal_wrapper import (  # noqa: E402
    apply_blueprint_event_rename_autofix,
    apply_delegate_broadcast_autofix,
    apply_editor_runtime_guard_autofix,
    build_static_autofix_steps,
    reorder_generated_h_header_text,
)
from unreal_static_validate import Finding  # noqa: E402


def test_no_python_bom_in_scripts_and_tests() -> None:
    issues: list[str] = []
    for folder in (ROOT / "scripts", ROOT / "tests"):
        for path in folder.rglob("*.py"):
            if path.read_bytes().startswith(b"\xef\xbb\xbf"):
                issues.append(str(path))
    assert not issues, f"UTF-8 BOM detected: {issues}"


def test_editor_runtime_autofix_preserves_source(tmp_path: Path) -> None:
    cpp = tmp_path / "Source" / "Demo" / "Private" / "EditorBoundary.cpp"
    cpp.parent.mkdir(parents=True)
    original = """#include "CoreMinimal.h"
#include "UEditorEngine.h"

void RefreshEditorViewports()
{
#if WITH_EDITOR
	if (GEditor)
	{
		GEditor->RedrawAllViewports();
	}
#endif
}
"""
    cpp.write_text(original, encoding="utf-8")
    written = apply_editor_runtime_guard_autofix(
        tmp_path,
        [Finding("error", str(cpp.relative_to(tmp_path)), 2, "EDITOR_ONLY_INCLUDE_IN_RUNTIME_MODULE", "x")],
    )
    assert written == []
    assert cpp.read_text(encoding="utf-8") == original


def test_delegate_broadcast_autofix_does_not_guess_zero(tmp_path: Path) -> None:
    cpp = tmp_path / "Source" / "Demo" / "Private" / "Score.cpp"
    cpp.parent.mkdir(parents=True)
    original = "void Trigger() { OnScoreChanged.Broadcast(); }\n"
    cpp.write_text(original, encoding="utf-8")
    written = apply_delegate_broadcast_autofix(
        tmp_path,
        [
            Finding(
                "error",
                str(cpp.relative_to(tmp_path)),
                1,
                "DELEGATE_BROADCAST_SIGNATURE_MISMATCH",
                "missing payload",
            )
        ],
    )
    assert written == []
    assert cpp.read_text(encoding="utf-8") == original


def test_blueprint_event_rename_does_not_rename_multiple_events(tmp_path: Path) -> None:
    header = tmp_path / "Source" / "Demo" / "Public" / "Actor.h"
    cpp = tmp_path / "Source" / "Demo" / "Private" / "Actor.cpp"
    header.parent.mkdir(parents=True)
    cpp.parent.mkdir(parents=True)
    header.write_text(
        "UFUNCTION(BlueprintNativeEvent)\nvoid EventA();\nUFUNCTION(BlueprintNativeEvent)\nvoid EventB();\n",
        encoding="utf-8",
    )
    original = "void ADemo::WrongA_Implementation() {}\nvoid ADemo::WrongB_Implementation() {}\n"
    cpp.write_text(original, encoding="utf-8")
    written = apply_blueprint_event_rename_autofix(
        tmp_path,
        [Finding("warning", str(cpp.relative_to(tmp_path)), 1, "CPP_FUNCTION_NOT_DECLARED_IN_HEADER", "x")],
    )
    assert written == []
    assert cpp.read_text(encoding="utf-8") == original


def test_generated_h_reorder_preserves_ifdef_and_comments() -> None:
    text = """#include "CoreMinimal.h"
// keep this comment
#if WITH_EDITOR
#include "EditorSubsystem.h"
#endif
#include "Demo.generated.h"
#include "Components/ActorComponent.h"

UCLASS()
class UDemo : public UActorComponent {};
"""
    updated = reorder_generated_h_header_text(text)
    assert updated is not None
    assert "// keep this comment" in updated
    assert "#if WITH_EDITOR" in updated
    assert updated.index("ActorComponent.h") < updated.index("Demo.generated.h")


def test_compile_fix_mode_does_not_register_multifile_autofix_step() -> None:
    steps = build_static_autofix_steps("compile_fix")
    assert not any(step.name == "multifile_refactor" for step in steps)


def test_multifile_refactor_mode_registers_multifile_step() -> None:
    steps = build_static_autofix_steps("multifile_refactor")
    assert any(step.name == "multifile_refactor" for step in steps)


def test_compile_fix_pipeline_skips_multifile_autofix_with_drift_findings(tmp_path: Path) -> None:
    cpp = tmp_path / "Source" / "Demo" / "Private" / "Demo.cpp"
    cpp.parent.mkdir(parents=True)
    cpp.write_text("void Demo() {}\n", encoding="utf-8")
    findings = [
        Finding("warning", "Source/Demo/Private/Demo.cpp", 1, "MULTIFILE_CALLSITE_DRIFT", "drift"),
    ]
    steps = build_static_autofix_steps("compile_fix")
    result = run_autofix_pipeline(tmp_path, findings, "compile_fix", steps)
    assert "multifile_refactor" not in result.step_names


def test_disabled_autofix_records_diagnostic(tmp_path: Path) -> None:
    cpp = tmp_path / "Source" / "Demo" / "Private" / "Score.cpp"
    cpp.parent.mkdir(parents=True)
    cpp.write_text("void X() { OnScoreChanged.Broadcast(); }\n", encoding="utf-8")
    steps = [
        AutofixStep(
            "delegate_broadcast",
            apply_delegate_broadcast_autofix,
            {"DELEGATE_BROADCAST_SIGNATURE_MISMATCH"},
        )
    ]
    findings = [
        Finding("error", str(cpp.relative_to(tmp_path)), 1, "DELEGATE_BROADCAST_SIGNATURE_MISMATCH", "x"),
    ]
    result = run_autofix_pipeline(tmp_path, findings, "compile_fix", steps)
    assert result.written == []
    assert any(item.get("code") == "AUTOFIX_DISABLED" for item in result.diagnostics)
