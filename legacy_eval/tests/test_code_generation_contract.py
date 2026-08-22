# Archived workflow code-generation test.
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from build_symbol_graph import build_symbol_graph  # noqa: E402
from code_generation_contract import build_generation_contract  # noqa: E402


def test_generic_generation_contract_does_not_claim_project_compatibility() -> None:
    result = build_generation_contract("show an example retry helper")
    invalid = build_generation_contract("show an example", change_kind="typo")

    assert result["ok"] is True
    assert result["mode"] == "generic_example"
    assert result["projectSpecific"] is False
    assert result["writeGate"]["writesAllowed"] is False
    assert invalid["ok"] is False
    assert invalid["mode"] == "blocked"


def test_existing_target_contract_requires_source_and_pair_read(tmp_path: Path) -> None:
    source = tmp_path / "Source" / "Demo"
    public = source / "Public"
    private = source / "Private"
    public.mkdir(parents=True)
    private.mkdir(parents=True)
    header = public / "Worker.h"
    cpp = private / "Worker.cpp"
    header.write_text("class FWorker { public: void Run(); };\n", encoding="utf-8")
    cpp.write_text('#include "Worker.h"\nvoid FWorker::Run() {}\n', encoding="utf-8")
    graph = build_symbol_graph(tmp_path / "Source")

    result = build_generation_contract(
        "add a guard to Run",
        project_root=tmp_path,
        target_files=["Source/Demo/Private/Worker.cpp"],
        change_kind="modify_existing",
        graph=graph,
    )

    assert result["ok"] is True
    assert result["mode"] == "project_specific"
    assert result["targets"][0]["exists"] is True
    assert "Source/Demo/Public/Worker.h" in result["targets"][0]["pairedSources"]
    assert len(result["requiredReads"]) == 2
    assert result["writeGate"]["existingFilesRequirePatchWorkflow"] is True
    assert result["proofBoundary"].startswith("This contract establishes")


def test_new_cpp_contract_reads_existing_paired_header(tmp_path: Path) -> None:
    source = tmp_path / "Source" / "Demo"
    source.mkdir(parents=True)
    header = source / "Worker.h"
    header.write_text("class FWorker { public: void Run(); };\n", encoding="utf-8")

    result = build_generation_contract(
        "create the implementation",
        project_root=tmp_path,
        target_files=["Source/Demo/Worker.cpp"],
        change_kind="new_file",
    )

    assert result["ok"] is True
    assert result["targets"][0]["pairedSources"] == ["Source/Demo/Worker.h"]
    assert [row["filePath"] for row in result["requiredReads"]] == [str(header)]


def test_existing_edit_contract_rejects_missing_target_and_unsafe_path(tmp_path: Path) -> None:
    missing = build_generation_contract(
        "modify worker",
        project_root=tmp_path,
        target_files=["Source/Worker.cpp"],
    )
    unsafe = build_generation_contract(
        "modify generated output",
        project_root=tmp_path,
        target_files=["Intermediate/Generated.cpp"],
    )
    binary = tmp_path / "Content" / "Asset.uasset"
    binary.parent.mkdir()
    binary.write_bytes(b"binary")
    unsupported = build_generation_contract(
        "modify binary asset",
        project_root=tmp_path,
        target_files=["Content/Asset.uasset"],
    )

    assert missing["ok"] is False
    assert "does not exist" in missing["issues"][0]
    assert unsafe["ok"] is False
    assert "protected" in unsafe["issues"][0]
    assert unsupported["ok"] is False
    assert "not a recognized source" in unsupported["issues"][0]


def test_generation_contract_uses_project_relative_protection_and_fails_closed_on_kind(tmp_path: Path) -> None:
    project = tmp_path / "Saved" / "Project"
    target = project / "Source" / "Worker.cpp"
    target.parent.mkdir(parents=True)
    target.write_text("void Run() {}\n", encoding="utf-8")

    valid = build_generation_contract(
        "modify worker",
        project_root=project,
        target_files=["Source/Worker.cpp"],
    )
    invalid_kind = build_generation_contract(
        "modify worker",
        project_root=project,
        target_files=["Source/Worker.cpp"],
        change_kind="modfy_existing",
    )
    existing_as_new = build_generation_contract(
        "create worker",
        project_root=project,
        target_files=["Source/Worker.cpp"],
        change_kind="new_file",
    )

    assert valid["ok"] is True
    assert invalid_kind["ok"] is False
    assert invalid_kind["writeGate"]["writesAllowed"] is False
    assert existing_as_new["ok"] is False


def test_generation_contract_enforces_change_kind_target_cardinality(tmp_path: Path) -> None:
    source = tmp_path / "Source"
    source.mkdir()
    for name in ("One.cpp", "Two.cpp", "Three.cpp"):
        (source / name).write_text(f"void {name[:-4]}() {{}}\n", encoding="utf-8")

    single = build_generation_contract(
        "modify two",
        project_root=tmp_path,
        target_files=["Source/One.cpp", "Source/Two.cpp"],
        change_kind="single_file",
    )
    too_many = build_generation_contract(
        "modify three",
        project_root=tmp_path,
        target_files=["Source/One.cpp", "Source/Two.cpp", "Source/Three.cpp"],
        change_kind="modify_existing",
    )
    multifile = build_generation_contract(
        "modify three",
        project_root=tmp_path,
        target_files=["Source/One.cpp", "Source/Two.cpp", "Source/Three.cpp"],
        change_kind="multifile",
    )

    assert single["ok"] is False
    assert too_many["ok"] is False
    assert multifile["ok"] is True


def test_generation_contract_does_not_attach_graph_symbols_from_unicode_path_alias(
    tmp_path: Path,
) -> None:
    composed_relative = "Source/Demo/\u0130mplementation.cpp"
    alias_relative = "Source/Demo/I\u0307mplementation.cpp"
    target = tmp_path / composed_relative
    target.parent.mkdir(parents=True)
    target.write_text("void Run() {}\n", encoding="utf-8")
    assert composed_relative.casefold() == alias_relative.casefold()

    result = build_generation_contract(
        "modify implementation",
        project_root=tmp_path,
        target_files=[composed_relative],
        graph={
            "symbols": [
                {
                    "file_path": str(tmp_path / alias_relative),
                    "symbol_name": "FAliasOwner",
                    "qualified_name": "FAliasOwner",
                    "symbol_kind": "class",
                }
            ]
        },
    )

    assert result["ok"] is True, result
    assert result["targets"][0]["knownSymbolCount"] == 0
    assert "preserveSymbols" not in result["targets"][0]
