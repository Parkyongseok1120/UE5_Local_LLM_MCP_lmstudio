from __future__ import annotations

from pathlib import Path

from code_sketch_pipeline import load_declaration_context, validate_active_slice_surface


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
