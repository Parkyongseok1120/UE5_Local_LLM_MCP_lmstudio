from __future__ import annotations

from pathlib import Path

from code_sketch_pipeline import (
    load_declaration_context,
    proposed_code_surface,
    validate_active_slice_surface,
)


def _contract(*targets: dict) -> dict:
    return {
        "ok": True,
        "targets": list(targets),
        "issues": [],
        "writeGate": {"writesAllowed": True},
        "requiredReads": [],
    }


def test_active_slice_rejects_extra_labeled_file_and_reflected_class(tmp_path: Path) -> None:
    target = tmp_path / "Source" / "Generic" / "BoardActor.h"
    target.parent.mkdir(parents=True)
    target.write_text("class ABoardActor;\n", encoding="utf-8")
    contract = _contract({"exists": True, "absolutePath": str(target), "path": "Source/Generic/BoardActor.h"})
    result = validate_active_slice_surface(
        "// File: Source/Generic/OtherController.h\nUCLASS() class AOtherController {};",
        target_files=["Source/Generic/BoardActor.h"],
        generation_contract=contract,
        graph=None,
    )
    assert result["ok"] is False
    assert result["writeGate"]["writesAllowed"] is False
    assert any("outside targetFiles" in item for item in result["issues"])
    assert any("reflected UCLASS" in item for item in result["issues"])


def test_source_backed_reflected_header_mismatch_is_rejected() -> None:
    contract = _contract({"exists": False, "path": "Source/Generic/Consumer.cpp"})
    graph = {
        "symbols": [
            {
                "is_reflected": True,
                "symbol_kind": "class",
                "symbol_name": "UInventoryComponent",
                "file_path": "Source/Generic/Public/InventoryOwner.h",
            }
        ]
    }
    result = validate_active_slice_surface(
        '#include "InventoryComponent.h"\nvoid Use() {}',
        target_files=["Source/Generic/Consumer.cpp"],
        generation_contract=contract,
        graph=graph,
    )
    assert result["writeGate"]["reason"] == "reflected include path is not source-backed"


def test_qualified_definition_owner_must_belong_to_active_target_surface(
    tmp_path: Path,
) -> None:
    source = tmp_path / "Source" / "Demo"
    source.mkdir(parents=True)
    controller_h = source / "GomokuPlayerController.h"
    controller_cpp = source / "GomokuPlayerController.cpp"
    controller_h.write_text("class AGomokuPlayerController {};\n", encoding="utf-8")
    controller_cpp.write_text(
        '#include "GomokuPlayerController.h"\n',
        encoding="utf-8",
    )
    contract = _contract(
        {
            "exists": True,
            "absolutePath": str(controller_h),
            "path": "Source/Demo/GomokuPlayerController.h",
        },
        {
            "exists": True,
            "absolutePath": str(controller_cpp),
            "path": "Source/Demo/GomokuPlayerController.cpp",
        },
    )
    contract["requiredReads"] = [
        {"filePath": str(controller_h)},
        {"filePath": str(controller_cpp)},
    ]
    graph = {
        "symbols": [
            {
                "symbol_kind": "class",
                "symbol_name": "AGomokuGameState",
                "qualified_name": "AGomokuGameState",
                "file_path": "Source/Demo/GomokuGameState.h",
            },
            {
                "symbol_kind": "function",
                "symbol_name": "HandlePlaceStone",
                "qualified_name": "AGomokuGameState::HandlePlaceStone",
                "file_path": "Source/Demo/GomokuGameState.cpp",
            },
        ]
    }

    result = validate_active_slice_surface(
        "void AGomokuGameState::HandlePlaceStone() {}",
        target_files=[
            "Source/Demo/GomokuPlayerController.h",
            "Source/Demo/GomokuPlayerController.cpp",
        ],
        generation_contract=contract,
        graph=graph,
    )

    assert result["ok"] is False
    assert result["writeGate"]["reason"] == (
        "sketch definition owner is outside active targetFiles"
    )
    outside = result["surfaceBinding"]["outsideDefinitionOwners"]
    assert outside[0]["owner"] == "AGomokuGameState"
    assert outside[0]["member"] == "HandlePlaceStone"
    assert outside[0]["knownOwnerFiles"] == [
        "Source/Demo/GomokuGameState.cpp",
        "Source/Demo/GomokuGameState.h",
    ]

    graphless = validate_active_slice_surface(
        "void AGomokuGameState::HandlePlaceStone() {}",
        target_files=[
            "Source/Demo/GomokuPlayerController.h",
            "Source/Demo/GomokuPlayerController.cpp",
        ],
        generation_contract=_contract(
            {
                "exists": True,
                "absolutePath": str(controller_h),
                "path": "Source/Demo/GomokuPlayerController.h",
            },
            {
                "exists": True,
                "absolutePath": str(controller_cpp),
                "path": "Source/Demo/GomokuPlayerController.cpp",
            },
        ),
        graph=None,
    )
    assert graphless["ok"] is False
    assert graphless["surfaceBinding"]["outsideDefinitionOwners"][0][
        "knownOwnerFiles"
    ] == []


def test_paired_header_can_establish_owner_for_new_cpp_definition(tmp_path: Path) -> None:
    source = tmp_path / "Source" / "Demo"
    source.mkdir(parents=True)
    header = source / "GomokuGameState.h"
    header.write_text("class AGomokuGameState {};\n", encoding="utf-8")
    contract = _contract(
        {
            "exists": False,
            "absolutePath": str(source / "GomokuGameState.cpp"),
            "path": "Source/Demo/GomokuGameState.cpp",
        }
    )
    contract["requiredReads"] = [{"filePath": str(header)}]

    result = validate_active_slice_surface(
        "void AGomokuGameState::HandlePlaceStone() {}",
        target_files=["Source/Demo/GomokuGameState.cpp"],
        generation_contract=contract,
        graph=None,
    )

    assert result["ok"] is True
    assert result["surfaceBinding"]["outsideDefinitionOwners"] == []


def test_declaration_context_is_bounded_and_deduplicated(tmp_path: Path) -> None:
    files = []
    for index in range(6):
        path = tmp_path / f"Type{index}.h"
        path.write_text("x" * 100, encoding="utf-8")
        files.append({"filePath": str(path)})
    contract = _contract()
    contract["requiredReads"] = [files[0], files[0], *files[1:]]
    context, selected = load_declaration_context(contract, max_files=2, max_chars=150)
    assert len(selected) == 2
    assert len(context) == 151  # 100 chars + separator + 50 chars


def test_task_bound_existing_sketch_rejects_gui_restatement_and_delegate_inline(
    tmp_path: Path,
) -> None:
    source = tmp_path / "Source" / "O_Mock"
    source.mkdir(parents=True)
    controller = source / "GomokuPlayerController.cpp"
    controller.write_text(
        """
void AGomokuPlayerController::HandlePrimaryClick()
{
    if (!GetWorld()) return;
    HandleMouseMove(FVector2D::ZeroVector);
}

void AGomokuPlayerController::OnMouseMoveX(float Value)
{
    if (!GetWorld()) return;
    FVector2D MousePos;
    GetMousePosition(MousePos.X, MousePos.Y);
    HandleMouseMove(MousePos);
}

void AGomokuPlayerController::OnMouseMoveY(float Value)
{
    OnMouseMoveX(Value);
}
""".strip(),
        encoding="utf-8",
    )
    contract = _contract(
        {
            "exists": True,
            "absolutePath": str(controller),
            "path": "Source/O_Mock/GomokuPlayerController.cpp",
        }
    )
    sketch = """
void AGomokuPlayerController::HandlePrimaryClick()
{
    if (!GetWorld()) return;
    HandleMouseMove(FVector2D::ZeroVector);
}

void AGomokuPlayerController::OnMouseMoveY(float Value)
{
    if (!GetWorld()) return;
    FVector2D MousePos;
    GetMousePosition(MousePos.X, MousePos.Y);
    HandleMouseMove(MousePos);
}
"""

    result = validate_active_slice_surface(
        sketch,
        target_files=["Source/O_Mock/GomokuPlayerController.cpp"],
        generation_contract=contract,
        graph=None,
        require_material_delta=True,
    )

    assert result["ok"] is False
    assert result["writeGate"]["reason"] == "sketch does not change existing behavior"
    assert result["materialDelta"]["status"] == "no_material_delta"
    assert result["materialDelta"]["definitionDeltas"] == [
        {
            "owner": "AGomokuPlayerController",
            "member": "HandlePrimaryClick",
            "status": "existing_restatement",
        },
        {
            "owner": "AGomokuPlayerController",
            "member": "OnMouseMoveY",
            "status": "equivalent_delegate_inline",
        },
    ]


def test_task_bound_existing_sketch_accepts_concrete_changed_statement(
    tmp_path: Path,
) -> None:
    source = tmp_path / "Source" / "Demo"
    source.mkdir(parents=True)
    target = source / "Worker.cpp"
    target.write_text(
        "void FWorker::Run() { ExistingCall(); }\n",
        encoding="utf-8",
    )
    result = validate_active_slice_surface(
        "void FWorker::Run() { if (!Owner) return; ExistingCall(); }",
        target_files=["Source/Demo/Worker.cpp"],
        generation_contract=_contract(
            {
                "exists": True,
                "absolutePath": str(target),
                "path": "Source/Demo/Worker.cpp",
            }
        ),
        graph=None,
        require_material_delta=True,
    )

    assert result["ok"] is True
    assert result["materialDelta"]["status"] == "material_delta"


def test_unified_diff_validation_uses_after_image_and_ignores_deleted_owner(
    tmp_path: Path,
) -> None:
    source = tmp_path / "Source" / "Demo"
    source.mkdir(parents=True)
    target = source / "Worker.cpp"
    target.write_text(
        "void FWorker::Run() { OldCall(); }\n",
        encoding="utf-8",
    )
    contract = _contract(
        {
            "exists": True,
            "absolutePath": str(target),
            "path": "Source/Demo/Worker.cpp",
        }
    )
    contract["projectRoot"] = str(tmp_path)
    sketch = """--- a/Source/Demo/Worker.cpp
+++ b/Source/Demo/Worker.cpp
@@ -1,2 +1,2 @@
-void FUnrelatedOwner::Run() { MissingApi(); }
+void FWorker::Run() { NewCall(); }
"""

    live, info = proposed_code_surface(sketch)
    assert "FUnrelatedOwner" not in live
    assert "FWorker::Run" in live
    assert info["deletedLineCount"] == 1

    result = validate_active_slice_surface(
        sketch,
        target_files=["Source/Demo/Worker.cpp"],
        generation_contract=contract,
        graph=None,
        require_material_delta=True,
    )
    assert result["surfaceBinding"]["outsideDefinitionOwners"] == []
    assert result["materialDelta"]["explicitDiff"] is True


def test_deletion_only_unified_diff_is_a_material_delta(tmp_path: Path) -> None:
    source = tmp_path / "Source" / "Demo"
    source.mkdir(parents=True)
    target = source / "Worker.cpp"
    target.write_text("void FWorker::Legacy() {}\n", encoding="utf-8")
    contract = _contract(
        {
            "exists": True,
            "absolutePath": str(target),
            "path": "Source/Demo/Worker.cpp",
        }
    )
    contract["projectRoot"] = str(tmp_path)
    result = validate_active_slice_surface(
        """--- a/Source/Demo/Worker.cpp
+++ b/Source/Demo/Worker.cpp
@@ -1 +0,0 @@
-void FWorker::Legacy() {}
""",
        target_files=["Source/Demo/Worker.cpp"],
        generation_contract=contract,
        graph=None,
        require_material_delta=True,
    )
    assert result["ok"] is True
    assert result["materialDelta"]["status"] == "material_delta"
    assert result["diffSurface"]["deletedLineCount"] == 1


def test_unified_diff_headers_cannot_hide_an_out_of_slice_file(tmp_path: Path) -> None:
    source = tmp_path / "Source" / "Demo"
    source.mkdir(parents=True)
    target = source / "Worker.cpp"
    target.write_text("void Run() {}\n", encoding="utf-8")
    contract = _contract(
        {
            "exists": True,
            "absolutePath": str(target),
            "path": "Source/Demo/Worker.cpp",
        }
    )
    contract["projectRoot"] = str(tmp_path)

    result = validate_active_slice_surface(
        """--- a/Source/Demo/Outside.cpp
+++ b/Source/Demo/Outside.cpp
@@ -0,0 +1 @@
+int32 HiddenOutOfSliceValue = 1;
""",
        target_files=["Source/Demo/Worker.cpp"],
        generation_contract=contract,
        graph=None,
        require_material_delta=True,
    )

    assert result["ok"] is False
    assert result["diffSurface"]["targetFiles"] == ["Source/Demo/Outside.cpp"]
    assert any("outside targetFiles" in issue for issue in result["issues"])


def test_new_unresolved_quoted_include_closes_generation_gate(tmp_path: Path) -> None:
    source = tmp_path / "Source" / "Demo"
    source.mkdir(parents=True)
    target = source / "Worker.cpp"
    target.write_text('#include "Worker.h"\nvoid FWorker::Run() {}\n', encoding="utf-8")
    (source / "Worker.h").write_text("struct FWorker { void Run(); };\n", encoding="utf-8")
    contract = _contract(
        {
            "exists": True,
            "absolutePath": str(target),
            "path": "Source/Demo/Worker.cpp",
        }
    )
    contract["projectRoot"] = str(tmp_path)

    result = validate_active_slice_surface(
        '#include "DevTest/Test.h"\nvoid FWorker::Run() { NewCall(); }',
        target_files=["Source/Demo/Worker.cpp"],
        generation_contract=contract,
        graph=None,
        require_material_delta=True,
    )

    assert result["ok"] is False
    assert result["quotedIncludes"]["unresolved"][0]["include"] == "DevTest/Test.h"


def test_unrelated_module_private_header_does_not_prove_leaf_include(
    tmp_path: Path,
) -> None:
    demo = tmp_path / "Source" / "Demo"
    unrelated = tmp_path / "Source" / "Unrelated" / "Private"
    demo.mkdir(parents=True)
    unrelated.mkdir(parents=True)
    target = demo / "Worker.cpp"
    target.write_text("void FWorker::Run() {}\n", encoding="utf-8")
    (unrelated / "Ghost.h").write_text("struct FGhost {};\n", encoding="utf-8")
    contract = _contract(
        {
            "exists": True,
            "absolutePath": str(target),
            "path": "Source/Demo/Worker.cpp",
        }
    )
    contract["projectRoot"] = str(tmp_path)

    result = validate_active_slice_surface(
        '#include "Ghost.h"\nvoid FWorker::Run() { NewCall(); }',
        target_files=["Source/Demo/Worker.cpp"],
        generation_contract=contract,
        graph=None,
        require_material_delta=True,
    )

    assert result["ok"] is False
    assert result["quotedIncludes"]["unresolved"][0]["include"] == "Ghost.h"
    assert result["writeGate"]["reason"] == "quoted include path is not source-backed"
