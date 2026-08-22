from __future__ import annotations

import argparse
import io
import json
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from build_rag_index import build  # noqa: E402
from collect_editor_metadata import merge_export_into_raw  # noqa: E402
from direct_rag_retrieval import retrieve  # noqa: E402
from direct_rag_evidence import factual_rows  # noqa: E402
from direct_rag_search import rag_search  # noqa: E402
from direct_rag_symbol import symbol_lookup_capability  # noqa: E402
from direct_rag_project_merge import (  # noqa: E402
    merge_project_jsonl,
    replace_project_architecture,
)
from workspace_paths import filesystem_path_identity  # noqa: E402


def _project(owner: Path) -> Path:
    descriptor = owner / "Game" / "Game.uproject"
    descriptor.parent.mkdir(parents=True)
    descriptor.write_text("{}", encoding="utf-8")
    return descriptor


def _versioned_project(owner: Path, version: str) -> Path:
    descriptor = owner / "Game" / "Game.uproject"
    descriptor.parent.mkdir(parents=True)
    descriptor.write_text(json.dumps({"EngineAssociation": version}), encoding="utf-8")
    return descriptor


def _document(
    identifier: str,
    source: str,
    text: str,
    project: Path | None,
    *,
    symbol: str = "",
) -> dict:
    metadata = {
        "project": project.stem if project else "",
        "project_root": str(project.parent) if project else "",
        "relative_path": f"Source/Game/{identifier}.cpp" if project else "Engine/Source/Runtime/Core",
        "symbol_name": symbol,
        "symbol_kind": "class" if symbol else "",
        "scope": "project" if project else "engine",
    }
    return {
        "id": identifier,
        "source": source,
        "path": metadata["relative_path"],
        "title": identifier,
        "text": text,
        "metadata": metadata,
    }


def _build_index(tmp_path: Path, documents: list[dict]) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    raw = tmp_path / "raw.jsonl"
    raw.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in documents),
        encoding="utf-8",
    )
    out = tmp_path / "index"
    build(
        argparse.Namespace(
            input=[str(raw)],
            out_dir=str(out),
            workspace_root=str(tmp_path),
            chunk_tokens=900,
            overlap_tokens=120,
        )
    )
    return out / "rag.sqlite"


def _build_versioned_index(
    data_root: Path,
    namespace: str,
    version: str,
    workspace: Path,
    documents: list[dict],
) -> Path:
    inputs = data_root / "inputs"
    inputs.mkdir(parents=True, exist_ok=True)
    raw = inputs / f"{namespace}.jsonl"
    raw.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in documents),
        encoding="utf-8",
    )
    out = data_root / namespace
    build(
        argparse.Namespace(
            input=[str(raw)],
            out_dir=str(out),
            workspace_root=str(workspace),
            chunk_tokens=900,
            overlap_tokens=120,
        )
    )
    manifest_path = out / "build_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["engineVersion"] = version
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return out / "rag.sqlite"


def _roots(rows: list[dict]) -> set[str]:
    return {str(row.get("project_root") or "") for row in rows}


def test_architecture_collector_preserves_exact_descriptor_in_shared_folder(
    tmp_path: Path,
) -> None:
    from collect_project_architecture import (
        make_rag_doc,
        make_summary_text,
        resolve_project_descriptor,
        scan_architecture,
    )

    shared = tmp_path / "B"
    shared.mkdir()
    project_a = shared / "A.uproject"
    project_b = shared / "B.uproject"
    project_a.write_text(
        json.dumps({"Modules": [{"Name": "AModule", "Type": "Runtime"}]}),
        encoding="utf-8",
    )
    project_b.write_text(
        json.dumps({"Modules": [{"Name": "BModule", "Type": "Runtime"}]}),
        encoding="utf-8",
    )

    assert resolve_project_descriptor(str(project_a)) == project_a.resolve()
    with pytest.raises(SystemExit, match="Ambiguous project directory"):
        resolve_project_descriptor(str(shared))

    architecture_a = scan_architecture(project_a)
    architecture_b = scan_architecture(project_b)
    document_a = make_rag_doc(architecture_a, make_summary_text(architecture_a))
    document_b = make_rag_doc(architecture_b, make_summary_text(architecture_b))

    assert architecture_a["project"] == "A"
    assert architecture_a["projectFile"] == str(project_a.resolve())
    assert architecture_a["pluginContext"]["projectName"] == "A"
    assert architecture_b["project"] == "B"
    assert architecture_b["pluginContext"]["projectName"] == "B"
    assert document_a["id"] != document_b["id"]
    assert document_a["metadata"]["project_file"] == str(project_a.resolve())
    assert document_b["metadata"]["project_file"] == str(project_b.resolve())


def test_exact_descriptor_never_uses_shared_parent_name_as_project_alias(
    tmp_path: Path,
) -> None:
    from direct_rag_index_ownership import resolve_common_project_owner

    shared = tmp_path / "B"
    shared.mkdir()
    project_a = shared / "A.uproject"
    project_b = shared / "B.uproject"
    project_a.write_text("{}", encoding="utf-8")
    project_b.write_text("{}", encoding="utf-8")
    index = _build_index(
        tmp_path / "built",
        [
            _document(
                "a",
                "unreal_symbol",
                "SharedFolderNeedle Alpha",
                project_a,
                symbol="AExactSymbol",
            ),
            _document(
                "b",
                "unreal_symbol",
                "SharedFolderNeedle Beta",
                project_b,
                symbol="BExactSymbol",
            ),
        ],
    )

    page = retrieve(
        index,
        "SharedFolderNeedle",
        8,
        {"scope": "project", "project": str(project_a)},
        workspace=tmp_path,
    )
    assert "Alpha" in page.context
    assert "Beta" not in page.context

    b_only = _build_index(
        tmp_path / "b-only",
        [_document("b", "unreal_project_text", "Only sibling B", project_b)],
    )
    ownership = resolve_common_project_owner(
        [b_only],
        [project_a],
        [{"engineVersion": "", "engineAssociation": ""}],
    )
    assert ownership["ok"] is False
    assert ownership["errorCode"] == "PROJECT_SELECTOR_NOT_INDEXED"


def test_freshness_uses_exact_root_and_project_stem_composite(tmp_path: Path) -> None:
    from direct_rag_freshness import invalidate_freshness_cache, project_freshness
    from workspace_paths import filesystem_path_identity

    shared = tmp_path / "B"
    shared.mkdir()
    project_a = shared / "A.uproject"
    project_b = shared / "B.uproject"
    project_a.write_text("{}", encoding="utf-8")
    project_b.write_text("{}", encoding="utf-8")
    index = _build_index(
        tmp_path / "freshness-index",
        [
            _document("a", "unreal_project_text", "A factual row", project_a),
            _document(
                "b-symbol",
                "unreal_symbol",
                "B sibling symbol",
                project_b,
                symbol="BOnlySymbol",
            ),
            _document("b-arch", "project_architecture", "B architecture", project_b),
        ],
    )

    invalidate_freshness_cache()
    freshness_a = project_freshness(index, projects=str(project_a))
    invalidate_freshness_cache()
    freshness_b = project_freshness(index, projects=str(project_b))
    manifest = json.loads(
        (index.parent / "build_manifest.json").read_text(encoding="utf-8")
    )

    assert freshness_a["projectSourceFresh"] is True
    assert freshness_a["projectSymbolsFresh"] is False
    assert freshness_a["architectureFresh"] is False
    assert freshness_b["projectSymbolsFresh"] is True
    assert freshness_b["architectureFresh"] is True
    assert filesystem_path_identity(index.resolve(), strip_project_uri=False) in freshness_a[
        "indexFingerprint"
    ]
    assert manifest["generationId"] in freshness_a["indexFingerprint"]


def test_structured_project_root_with_comma_is_not_split(tmp_path: Path) -> None:
    project = _project(tmp_path / "Owner,Studio")
    index = _build_index(
        tmp_path / "comma-index",
        [
            _document(
                "comma",
                "unreal_symbol",
                "CommaRootNeedle Exact",
                project,
                symbol="CommaRootSymbol",
            )
        ],
    )

    page = retrieve(
        index,
        "CommaRootNeedle",
        8,
        {"scope": "project", "project": str(project)},
        workspace=tmp_path,
    )

    assert page.rows
    assert "Exact" in page.context
    assert _roots(page.rows) == {
        filesystem_path_identity(project.parent, strip_project_uri=False)
    }


def test_exact_paths_isolate_same_name_clones_in_lexical_hybrid_mixed_and_symbol(
    tmp_path: Path,
) -> None:
    project_a = _project(tmp_path / "OwnerA")
    project_b = _project(tmp_path / "OwnerB")
    index = _build_index(
        tmp_path,
        [
            _document("alpha-text", "unreal_project_text", "CloneNeedle AlphaOnly", project_a),
            _document("beta-text", "unreal_project_text", "CloneNeedle BetaOnly", project_b),
            _document("alpha-symbol", "unreal_symbol", "SharedCloneSymbol AlphaOnly", project_a, symbol="SharedCloneSymbol"),
            _document("beta-symbol", "unreal_symbol", "SharedCloneSymbol BetaOnly", project_b, symbol="SharedCloneSymbol"),
            _document("engine-symbol", "unreal_symbol", "SharedCloneSymbol EngineOnly", None, symbol="SharedCloneSymbol"),
        ],
    )
    root_a = filesystem_path_identity(project_a.parent, strip_project_uri=False)
    root_b = filesystem_path_identity(project_b.parent, strip_project_uri=False)

    lexical = retrieve(
        index,
        "CloneNeedle",
        8,
        {"scope": "project", "project": [str(project_a)]},
        workspace=tmp_path,
    )
    hybrid = retrieve(
        index,
        "SharedCloneSymbol",
        8,
        {"scope": "project", "project": [str(project_a)], "hybrid": True},
        workspace=tmp_path,
    )
    mixed = retrieve(
        index,
        "SharedCloneSymbol",
        8,
        {"scope": "mixed", "project": [str(project_a)], "hybrid": True},
        workspace=tmp_path,
    )
    symbol = symbol_lookup_capability(
        SimpleNamespace(index=index, workspace=tmp_path),
        {"query": "SharedCloneSymbol", "project": [str(project_a)], "top_k": 8},
    ).payload

    assert lexical.rows and _roots(lexical.rows) == {root_a}
    assert hybrid.rows and root_b not in _roots(hybrid.rows)
    assert _roots(mixed.rows) <= {root_a, ""}
    assert root_a in _roots(mixed.rows) and "" in _roots(mixed.rows)
    assert symbol["ok"] is True
    assert root_b not in {row.get("projectRoot") for row in symbol["matches"]}


def test_same_name_selector_is_typed_ambiguity_but_exact_path_succeeds(tmp_path: Path) -> None:
    project_a = _project(tmp_path / "OwnerA")
    project_b = _project(tmp_path / "OwnerB")
    index = _build_index(
        tmp_path,
        [
            _document("alpha", "unreal_project_text", "CloneNeedle AlphaOnly", project_a),
            _document("beta", "unreal_project_text", "CloneNeedle BetaOnly", project_b),
            _document("alpha-symbol", "unreal_symbol", "CloneNeedle AlphaSymbol", project_a, symbol="AlphaSymbol"),
            _document("beta-symbol", "unreal_symbol", "CloneNeedle BetaSymbol", project_b, symbol="BetaSymbol"),
        ],
    )
    runtime = SimpleNamespace(index=index, workspace=tmp_path)

    ambiguous = rag_search(
        runtime,
        {"query": "CloneNeedle", "scope": "project", "project": ["Game"]},
    ).payload
    exact = rag_search(
        runtime,
        {"query": "CloneNeedle", "scope": "project", "project": [str(project_b)]},
    ).payload

    assert ambiguous["ok"] is False
    assert ambiguous["errorCode"] == "PROJECT_SELECTOR_AMBIGUOUS"
    assert len(ambiguous["projectRoots"]) == 2
    assert exact["ok"] is True and exact["matchCount"] >= 1
    assert "BetaOnly" in exact["evidence"]
    assert "AlphaOnly" not in exact["evidence"]


def test_editor_raw_merge_keeps_same_name_clone_rows_separate(tmp_path: Path) -> None:
    project_a = _project(tmp_path / "OwnerA")
    project_b = _project(tmp_path / "OwnerB")
    export_a = tmp_path / "a.jsonl"
    export_b = tmp_path / "b.jsonl"
    export_a.write_text(
        json.dumps({"asset_path": "/Game/BP_Shared", "title": "AlphaBlueprint"}) + "\n",
        encoding="utf-8",
    )
    export_b.write_text(
        json.dumps({"asset_path": "/Game/BP_Shared", "title": "BetaBlueprint"}) + "\n",
        encoding="utf-8",
    )
    raw = tmp_path / "raw_blueprint_metadata.jsonl"

    merge_export_into_raw(
        export_a,
        "blueprint",
        "Game",
        raw,
        project_root=str(project_a.parent),
    )
    merge_export_into_raw(
        export_b,
        "blueprint",
        "Game",
        raw,
        project_root=str(project_b.parent),
    )

    rows = [json.loads(line) for line in raw.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 2
    assert {row["metadata"]["project_root"] for row in rows} == {
        str(project_a.parent),
        str(project_b.parent),
    }

    rows.extend(
        [
            _document("alpha-symbol", "unreal_symbol", "Blueprint AlphaSymbol", project_a, symbol="AlphaSymbol"),
            _document("beta-symbol", "unreal_symbol", "Blueprint BetaSymbol", project_b, symbol="BetaSymbol"),
        ]
    )
    index = _build_index(tmp_path / "built", rows)
    page = retrieve(
        index,
        "Blueprint",
        8,
        {"scope": "project", "project": [str(project_a)]},
        workspace=tmp_path,
    )
    assert "AlphaBlueprint" in page.context
    assert "BetaBlueprint" not in page.context


def test_builder_excludes_retired_failure_memory_source(tmp_path: Path) -> None:
    index = _build_index(
        tmp_path,
        [
            _document("fact", "unreal_source", "OrdinaryNeedle", None),
            _document("failure", "unreal_failure_memory", "FailureNeedle", None),
        ],
    )
    with sqlite3.connect(index) as connection:
        assert connection.execute(
            "select count(*) from chunks where source = 'unreal_failure_memory'"
        ).fetchone()[0] == 0


def test_factual_rows_excludes_retired_failure_memory() -> None:
    rows = factual_rows(
        [
            {"chunk_id": "failure", "source": "unreal_failure_memory"},
            {"chunk_id": "fact", "source": "unreal_source"},
        ]
    )

    assert rows == [{"chunk_id": "fact", "source": "unreal_source"}]


def test_project_merge_replaces_only_the_exact_root_for_same_name_clones(
    tmp_path: Path,
) -> None:
    project_a = _project(tmp_path / "OwnerA")
    project_b = _project(tmp_path / "OwnerB")
    destination = tmp_path / "raw_project_symbols.jsonl"
    destination.write_text(
        "".join(
            json.dumps(row) + "\n"
            for row in (
                _document("a-old", "unreal_symbol", "AlphaOld", project_a),
                _document("b-old", "unreal_symbol", "BetaOld", project_b),
            )
        ),
        encoding="utf-8",
    )
    incoming = tmp_path / "incoming.jsonl"
    incoming.write_text(
        json.dumps(_document("b-new", "unreal_symbol", "BetaNew", project_b)) + "\n",
        encoding="utf-8",
    )

    merge_project_jsonl(destination, incoming, project_b)
    rows = [json.loads(line) for line in destination.read_text(encoding="utf-8").splitlines()]

    assert [row["id"] for row in rows] == ["a-old", "b-new"]
    assert {row["metadata"]["project_root"] for row in rows} == {
        str(project_a.parent),
        str(project_b.parent),
    }

    incoming.write_text("", encoding="utf-8")
    merge_project_jsonl(destination, incoming, project_b)
    remaining = [json.loads(line) for line in destination.read_text(encoding="utf-8").splitlines()]
    assert [row["id"] for row in remaining] == ["a-old"]


def test_project_merge_uses_descriptor_composite_in_one_shared_root(
    tmp_path: Path,
) -> None:
    shared = tmp_path / "Shared"
    shared.mkdir()
    project_a = shared / "GameA.uproject"
    project_b = shared / "GameB.uproject"
    project_a.write_text("{}", encoding="utf-8")
    project_b.write_text("{}", encoding="utf-8")
    destination = tmp_path / "raw_project_symbols.jsonl"
    destination.write_text(
        "".join(
            json.dumps(row) + "\n"
            for row in (
                _document("a-old", "unreal_symbol", "AlphaOld", project_a),
                _document("b-old", "unreal_symbol", "BetaOld", project_b),
            )
        ),
        encoding="utf-8",
    )
    incoming = tmp_path / "incoming.jsonl"
    incoming.write_text(
        json.dumps(_document("b-new", "unreal_symbol", "BetaNew", project_b)) + "\n",
        encoding="utf-8",
    )

    merge_project_jsonl(destination, incoming, project_b)
    rows = [json.loads(line) for line in destination.read_text(encoding="utf-8").splitlines()]
    assert [row["id"] for row in rows] == ["a-old", "b-new"]
    with pytest.raises(RuntimeError, match="exact captured project"):
        merge_project_jsonl(destination, incoming, project_a)

    architecture_root = tmp_path / "project_architecture"
    architecture_a = tmp_path / "architecture-a"
    architecture_b = tmp_path / "architecture-b"
    architecture_a.mkdir()
    architecture_b.mkdir()
    (architecture_a / "owner.txt").write_text("GameA", encoding="utf-8")
    (architecture_b / "owner.txt").write_text("GameB", encoding="utf-8")
    replace_project_architecture(architecture_root, architecture_a, project_a)
    replace_project_architecture(architecture_root, architecture_b, project_b)
    owner_files = sorted(architecture_root.glob("*/owner.txt"))
    assert len(owner_files) == 2
    assert {path.read_text(encoding="utf-8") for path in owner_files} == {
        "GameA",
        "GameB",
    }


def _legacy_project_symbol(
    identifier: str,
    project: Path,
    *,
    collector_root: Path | None = None,
    source_path: Path | None = None,
) -> dict:
    root = collector_root or project.parent / "Source"
    path = source_path or root / project.stem / f"{identifier}.cpp"
    return {
        "id": identifier,
        "source": "unreal_symbol",
        "path": str(path.resolve()),
        "title": identifier,
        "text": identifier,
        "metadata": {
            "root": str(root.resolve()),
            "relative_path": f"{project.stem}/{identifier}.cpp",
            "scope": "project",
            "project": project.stem,
            "symbol_name": identifier,
            "symbol_kind": "class",
        },
    }


def test_project_merge_replaces_selected_legacy_symbols_missing_project_root(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path / "Owner")
    source = project.parent / "Source" / project.stem
    source.mkdir(parents=True)
    old_path = source / "Legacy.cpp"
    old_path.write_text("legacy", encoding="utf-8")
    destination = tmp_path / "raw_project_symbols.jsonl"
    destination.write_text(
        json.dumps(_legacy_project_symbol("legacy", project, source_path=old_path)) + "\n",
        encoding="utf-8",
    )
    incoming = tmp_path / "incoming.jsonl"
    incoming.write_text(
        json.dumps(_document("current", "unreal_symbol", "Current", project)) + "\n",
        encoding="utf-8",
    )

    merge_project_jsonl(destination, incoming, project)

    rows = [json.loads(line) for line in destination.read_text(encoding="utf-8").splitlines()]
    assert [row["id"] for row in rows] == ["current"]
    assert {row["metadata"]["project_root"] for row in rows} == {
        str(project.parent)
    }


def test_legacy_symbol_migration_preserves_same_name_clone_and_split_paths(
    tmp_path: Path,
) -> None:
    selected = _project(tmp_path / "SelectedOwner")
    clone = _project(tmp_path / "CloneOwner")
    selected_source = selected.parent / "Source" / selected.stem
    clone_source = clone.parent / "Source" / clone.stem
    selected_source.mkdir(parents=True)
    clone_source.mkdir(parents=True)
    clone_path = clone_source / "Clone.cpp"
    clone_path.write_text("clone", encoding="utf-8")
    destination = tmp_path / "raw_project_symbols.jsonl"
    destination.write_text(
        "".join(
            json.dumps(row) + "\n"
            for row in (
                _legacy_project_symbol("clone", clone, source_path=clone_path),
                _legacy_project_symbol(
                    "split",
                    selected,
                    collector_root=selected_source,
                    source_path=clone_path,
                ),
            )
        ),
        encoding="utf-8",
    )
    incoming = tmp_path / "incoming.jsonl"
    incoming.write_text(
        json.dumps(_document("current", "unreal_symbol", "Current", selected)) + "\n",
        encoding="utf-8",
    )

    merge_project_jsonl(destination, incoming, selected)

    rows = [json.loads(line) for line in destination.read_text(encoding="utf-8").splitlines()]
    assert [row["id"] for row in rows] == ["clone", "split", "current"]
    assert "project_root" not in rows[0]["metadata"]
    assert "project_root" not in rows[1]["metadata"]


def test_same_root_collectors_generate_distinct_descriptor_bound_ids(
    tmp_path: Path,
) -> None:
    from collect_unreal_projects import collect_project
    from collect_unreal_symbols import make_item, resolve_symbol_ownership

    shared = tmp_path / "Shared"
    source = shared / "Source"
    source.mkdir(parents=True)
    header = source / "SharedActor.h"
    header.write_text("UCLASS()\nclass USharedActor {};\n", encoding="utf-8")
    project_a = shared / "GameA.uproject"
    project_b = shared / "GameB.uproject"
    project_a.write_text("{}", encoding="utf-8")
    project_b.write_text("{}", encoding="utf-8")
    args = SimpleNamespace(
        max_text_bytes=1_000_000,
        min_chars=1,
        skip_asset_paths=True,
    )

    project_rows: dict[str, list[dict]] = {}
    for descriptor in (project_a, project_b):
        output = io.StringIO()
        collect_project(descriptor, output, args, None)
        project_rows[descriptor.stem] = [
            json.loads(line) for line in output.getvalue().splitlines()
        ]
    ids_a = {row["id"] for row in project_rows["GameA"]}
    ids_b = {row["id"] for row in project_rows["GameB"]}
    assert ids_a.isdisjoint(ids_b)
    assert {
        row["metadata"]["project_file"] for row in project_rows["GameA"]
    } == {str(project_a.resolve())}

    symbol_rows: list[dict] = []
    for descriptor in (project_a, project_b):
        ownership = resolve_symbol_ownership(
            scope="project",
            project_name=descriptor.stem,
            project_root=str(shared.resolve()),
        )
        symbol_rows.append(
            make_item(
                root=source,
                path=header,
                ownership=ownership,
                source="unreal_symbol",
                title="USharedActor",
                text="class USharedActor",
                symbol_name="USharedActor",
                symbol_kind="class",
                module_name="Shared",
            )
        )
    assert symbol_rows[0]["id"] != symbol_rows[1]["id"]
    assert [row["metadata"]["project"] for row in symbol_rows] == ["GameA", "GameB"]
    assert {row["metadata"]["project_root"] for row in symbol_rows} == {
        str(shared.resolve())
    }


def test_project_symbol_ownership_has_no_inferred_or_shared_fallback(
    tmp_path: Path,
) -> None:
    from collect_unreal_symbols import resolve_symbol_ownership

    shared = tmp_path / "Shared"
    shared.mkdir()
    (shared / "Game.uproject").write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError, match="requires both"):
        resolve_symbol_ownership(scope="project", project_name="Game")
    with pytest.raises(ValueError, match="one exact"):
        resolve_symbol_ownership(
            scope="project",
            project_name="Other",
            project_root=str(shared),
        )


def test_project_collector_removes_unique_scratch_directory_on_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import direct_rag_project_collection as collection

    project = tmp_path / "Game" / "Game.uproject"
    project.parent.mkdir()
    project.write_text("{}", encoding="utf-8")
    stage = tmp_path / "stage"
    monkeypatch.setattr(
        collection,
        "run_script",
        lambda *_args: {
            "ok": False,
            "returncode": 9,
            "command": [],
            "outputTail": "injected failure",
        },
    )

    steps, failed = collection.run_project_collectors(
        ROOT,
        project,
        stage,
        lambda _message: None,
    )

    assert failed == "collect_unreal_projects.py"
    assert steps[0]["returncode"] == 9
    assert not list(stage.glob(".project-collection-*"))


def test_real_project_refresh_preserves_other_clone_and_replaces_selected_rows(
    monkeypatch,
    tmp_path: Path,
) -> None:
    import active_project_paths
    from active_project_sync import sync_active_project

    monkeypatch.setattr(active_project_paths, "indexing_tier", lambda _workspace: "lite")

    project_a = _project(tmp_path / "OwnerA")
    project_b = _project(tmp_path / "OwnerB")
    source_a = project_a.parent / "Source" / "Game" / "Public"
    source_b = project_b.parent / "Source" / "Game" / "Public"
    source_a.mkdir(parents=True)
    source_b.mkdir(parents=True)
    (source_a / "AlphaKeep.h").write_text("UCLASS()\nclass FAlphaKeep {};\n", encoding="utf-8")
    (source_b / "BetaNew.h").write_text("UCLASS()\nclass FBetaNew {};\n", encoding="utf-8")

    index_dir = tmp_path / "data" / "unreal58"
    index_dir.mkdir(parents=True)
    seeded = [
        _document("alpha-seed", "unreal_symbol", "AlphaKeep", project_a, symbol="FAlphaKeep"),
        _document("beta-old", "unreal_symbol", "BetaOld", project_b, symbol="FBetaOld"),
    ]
    for name in (
        "raw_projects.jsonl",
        "raw_project_profiles.jsonl",
        "raw_project_architecture.jsonl",
        "raw_project_symbols.jsonl",
    ):
        (index_dir / name).write_text(
            "".join(json.dumps(row) + "\n" for row in seeded),
            encoding="utf-8",
        )
    build(
        argparse.Namespace(
            input=[
                str(index_dir / name)
                for name in (
                    "raw_projects.jsonl",
                    "raw_project_profiles.jsonl",
                    "raw_project_architecture.jsonl",
                    "raw_project_symbols.jsonl",
                )
            ],
            out_dir=str(index_dir),
            workspace_root=str(ROOT),
            chunk_tokens=900,
            overlap_tokens=120,
        )
    )

    refreshed = sync_active_project(
        project=project_b,
        index_dir=index_dir,
        workspace=ROOT,
    )

    assert refreshed["ok"] is True, refreshed
    merged_rows = [
        json.loads(line)
        for line in (index_dir / "raw_project_symbols.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    roots = [str(row["metadata"]["project_root"]) for row in merged_rows]
    assert str(project_a.parent) in roots
    assert sum(root == str(project_a.parent) for root in roots) == 1
    serialized = json.dumps(merged_rows)
    assert "AlphaKeep" in serialized
    assert "BetaOld" not in serialized
    assert "BetaNew" in serialized

    page_a = retrieve(
        index_dir / "rag.sqlite",
        "AlphaKeep",
        8,
        {"scope": "project", "project": str(project_a)},
        workspace=ROOT,
    )
    page_b = retrieve(
        index_dir / "rag.sqlite",
        "BetaNew",
        8,
        {"scope": "project", "project": str(project_b)},
        workspace=ROOT,
    )
    assert "AlphaKeep" in page_a.context
    assert "BetaNew" in page_b.context


def test_one_runtime_switches_versioned_sibling_indexes_without_engine_mixing(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    project_58 = _versioned_project(tmp_path / "Owner58", "5.8")
    project_57 = _versioned_project(tmp_path / "Owner57", "5.7")
    data = tmp_path / "data"
    index_58 = _build_versioned_index(
        data,
        "unreal58",
        "5.8",
        workspace,
        [
            _document("p58", "unreal_project_text", "VersionNeedle Project58", project_58),
            _document("e58", "unreal_source", "VersionNeedle Engine58", None),
        ],
    )
    index_57 = _build_versioned_index(
        data,
        "unreal57",
        "5.7",
        workspace,
        [
            _document("p57", "unreal_project_text", "VersionNeedle Project57", project_57),
            _document("e57", "unreal_source", "VersionNeedle Engine57", None),
            _document("s57", "unreal_symbol", "VersionSymbol Engine57", None, symbol="VersionSymbol"),
        ],
    )
    runtime = SimpleNamespace(index=index_58, workspace=workspace)

    result_58 = rag_search(
        runtime,
        {"query": "VersionNeedle", "scope": "mixed", "project": str(project_58)},
    ).payload
    result_57 = rag_search(
        runtime,
        {"query": "VersionNeedle", "scope": "mixed", "project": str(project_57)},
    ).payload
    symbol_57 = symbol_lookup_capability(
        runtime,
        {"query": "VersionSymbol", "project": str(project_57)},
    ).payload
    ambiguous_name = rag_search(
        runtime,
        {"query": "VersionNeedle", "scope": "mixed", "project": "Game"},
    ).payload

    assert result_58["ok"] is True and result_58["indexPath"] == str(index_58)
    assert "Engine58" in result_58["evidence"] and "Engine57" not in result_58["evidence"]
    assert result_57["ok"] is True and result_57["indexPath"] == str(index_57)
    assert "Engine57" in result_57["evidence"] and "Engine58" not in result_57["evidence"]
    assert symbol_57["ok"] is True and symbol_57["indexPath"] == str(index_57)
    assert "Engine57" in symbol_57["evidence"]
    assert ambiguous_name["ok"] is False
    assert ambiguous_name["errorCode"] == "PROJECT_SELECTOR_AMBIGUOUS"


def test_unique_project_name_selects_its_sibling_engine_index(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    project = tmp_path / "Owner57" / "Unique57.uproject"
    project.parent.mkdir(parents=True)
    project.write_text(json.dumps({"EngineAssociation": "5.7"}), encoding="utf-8")
    data = tmp_path / "data"
    base = _build_versioned_index(
        data,
        "unreal58",
        "5.8",
        workspace,
        [_document("e58", "unreal_source", "UniqueNeedle Engine58", None)],
    )
    sibling = _build_versioned_index(
        data,
        "unreal57",
        "5.7",
        workspace,
        [
            _document("p57", "unreal_project_text", "UniqueNeedle Project57", project),
            _document("e57", "unreal_source", "UniqueNeedle Engine57", None),
        ],
    )

    result = rag_search(
        SimpleNamespace(index=base, workspace=workspace),
        {"query": "UniqueNeedle", "scope": "mixed", "project": "Unique57"},
    ).payload

    assert result["ok"] is True
    assert result["indexPath"] == str(sibling)
    assert "Engine57" in result["evidence"]
    assert "Engine58" not in result["evidence"]


def test_missing_or_cross_version_index_fails_closed_with_typed_error(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    project_58 = _versioned_project(tmp_path / "Owner58", "5.8")
    project_57 = _versioned_project(tmp_path / "Owner57", "5.7")
    project_56 = _versioned_project(tmp_path / "Owner56", "5.6")
    base = _build_versioned_index(
        tmp_path / "data",
        "unreal58",
        "5.8",
        workspace,
        [_document("e58", "unreal_source", "VersionNeedle Engine58", None)],
    )
    runtime = SimpleNamespace(index=base, workspace=workspace)

    missing = rag_search(
        runtime,
        {"query": "VersionNeedle", "scope": "mixed", "project": str(project_56)},
    ).payload
    mixed_versions = rag_search(
        runtime,
        {
            "query": "VersionNeedle",
            "scope": "mixed",
            "project": [str(project_58), str(project_57)],
        },
    ).payload

    assert missing["ok"] is False
    assert missing["errorCode"] == "RAG_ENGINE_INDEX_MISMATCH"
    assert missing["engineIndex"]["projectEngineVersion"] == "5.6"
    assert mixed_versions["ok"] is False
    assert mixed_versions["errorCode"] == "RAG_MULTI_ENGINE_QUERY_UNSUPPORTED"


def test_mixed_exact_and_named_selectors_are_all_validated(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    project_58 = tmp_path / "Owner58" / "Game58.uproject"
    project_58.parent.mkdir(parents=True)
    project_58.write_text(json.dumps({"EngineAssociation": "5.8"}), encoding="utf-8")
    project_57 = tmp_path / "Owner57" / "Game57.uproject"
    project_57.parent.mkdir(parents=True)
    project_57.write_text(json.dumps({"EngineAssociation": "5.7"}), encoding="utf-8")
    data = tmp_path / "data"
    base = _build_versioned_index(
        data,
        "unreal58",
        "5.8",
        workspace,
        [_document("p58", "unreal_project_text", "MixedSelector58", project_58)],
    )
    _build_versioned_index(
        data,
        "unreal57",
        "5.7",
        workspace,
        [_document("p57", "unreal_project_text", "MixedSelector57", project_57)],
    )
    runtime = SimpleNamespace(index=base, workspace=workspace)

    cross_engine = rag_search(
        runtime,
        {
            "query": "MixedSelector",
            "scope": "mixed",
            "project": [str(project_58), "Game57"],
        },
    ).payload
    missing = rag_search(
        runtime,
        {
            "query": "MixedSelector",
            "scope": "mixed",
            "project": [str(project_58), "TypoProject"],
        },
    ).payload

    assert cross_engine["ok"] is False
    assert cross_engine["errorCode"] == "RAG_MULTI_ENGINE_QUERY_UNSUPPORTED"
    assert missing["ok"] is False
    assert missing["errorCode"] == "PROJECT_SELECTOR_NOT_FOUND"


def test_same_engine_projects_in_different_shards_never_partially_succeed(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    project_a = tmp_path / "OwnerA" / "GameA.uproject"
    project_b = tmp_path / "OwnerB" / "GameB.uproject"
    project_a.parent.mkdir(parents=True)
    project_b.parent.mkdir(parents=True)
    for project in (project_a, project_b):
        project.write_text(json.dumps({"EngineAssociation": "5.8"}), encoding="utf-8")
    data = tmp_path / "data"
    shard_a = _build_versioned_index(
        data,
        "unreal58-a-0000000001",
        "5.8",
        workspace,
        [_document("a", "unreal_project_text", "ShardNeedleA", project_a)],
    )
    _build_versioned_index(
        data,
        "unreal58-b-0000000002",
        "5.8",
        workspace,
        [_document("b", "unreal_project_text", "ShardNeedleB", project_b)],
    )

    result = rag_search(
        SimpleNamespace(index=shard_a, workspace=workspace),
        {
            "query": "ShardNeedle",
            "scope": "project",
            "project": [str(project_a), "GameB"],
        },
    ).payload

    assert result["ok"] is False
    assert result["errorCode"] == "RAG_MULTI_INDEX_QUERY_UNSUPPORTED"


def test_refresh_resolution_keeps_an_exact_project_on_its_unique_owner_shard(
    tmp_path: Path,
) -> None:
    from direct_rag_index_registry import resolve_request_index

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    project_a = tmp_path / "OwnerA" / "GameA.uproject"
    project_b = tmp_path / "OwnerB" / "GameB.uproject"
    project_a.parent.mkdir(parents=True)
    project_b.parent.mkdir(parents=True)
    for project in (project_a, project_b):
        project.write_text(json.dumps({"EngineAssociation": "5.8"}), encoding="utf-8")
    data = tmp_path / "data"
    shard_a = _build_versioned_index(
        data,
        "unreal58-a-0000000001",
        "5.8",
        workspace,
        [_document("a", "unreal_project_text", "OwnerA", project_a)],
    )
    shard_b = _build_versioned_index(
        data,
        "unreal58-b-0000000002",
        "5.8",
        workspace,
        [_document("b", "unreal_project_text", "OwnerB", project_b)],
    )

    resolution = resolve_request_index(
        shard_b,
        workspace,
        project_selector=str(project_a),
        use_active=False,
        allow_unbuilt=True,
    )

    assert resolution["ok"] is True
    assert resolution["index"] == str(shard_a.resolve())


def test_numeric_project_never_routes_to_custom_build_manifest(tmp_path: Path) -> None:
    import direct_rag_index_registry as registry

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    project = _versioned_project(tmp_path / "OwnerNumeric", "5.8")
    base = _build_versioned_index(
        tmp_path / "data",
        "unreal58",
        "5.8",
        workspace,
        [_document("custom", "unreal_source", "CustomBuildEvidence", None)],
    )
    manifest_path = base.parent / "build_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["engineAssociation"] = "CustomBuild-A"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    resolution = registry.resolve_request_index(
        base,
        workspace,
        project_selector=str(project),
    )

    assert resolution["ok"] is False
    assert resolution["errorCode"] == "RAG_ENGINE_INDEX_MISMATCH"


def test_embedded_version_custom_association_uses_registered_engine_root(
    monkeypatch,
    tmp_path: Path,
) -> None:
    import direct_rag_project_engine as project_engine
    import workspace_paths

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    project = tmp_path / "Game.uproject"
    project.write_text(
        json.dumps({"EngineAssociation": "Studio-UE5.7-Fork"}),
        encoding="utf-8",
    )
    engine = tmp_path / "CustomEngine"
    build_dir = engine / "Engine" / "Build"
    build_dir.mkdir(parents=True)
    (build_dir / "Build.version").write_text(
        json.dumps({"MajorVersion": 5, "MinorVersion": 7}),
        encoding="utf-8",
    )
    observed: list[str] = []

    def resolve(association: str, _workspace: Path) -> dict:
        observed.append(association)
        return {"ok": True, "engineRoot": str(engine)}

    monkeypatch.setattr(workspace_paths, "resolve_engine_root_for_association", resolve)

    result = project_engine.project_engine_version(project, workspace)

    assert result["ok"] is True
    assert result["engineVersion"] == "5.7"
    assert result["engineAssociation"] == "Studio-UE5.7-Fork"
    assert observed == ["Studio-UE5.7-Fork"]


def test_name_selector_rejects_stale_row_after_project_engine_upgrade(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    project = tmp_path / "Owner" / "UpgradeGame.uproject"
    project.parent.mkdir(parents=True)
    project.write_text(json.dumps({"EngineAssociation": "5.7"}), encoding="utf-8")
    base = _build_versioned_index(
        tmp_path / "data",
        "unreal57",
        "5.7",
        workspace,
        [_document("old", "unreal_project_text", "UpgradeNeedle", project)],
    )
    project.write_text(json.dumps({"EngineAssociation": "5.8"}), encoding="utf-8")

    result = rag_search(
        SimpleNamespace(index=base, workspace=workspace),
        {"query": "UpgradeNeedle", "scope": "project", "project": "UpgradeGame"},
    ).payload

    assert result["ok"] is False
    assert result["errorCode"] == "PROJECT_SELECTOR_NOT_FOUND"


def test_refresh_creates_only_the_selected_engine_shard_without_touching_base(
    monkeypatch,
    tmp_path: Path,
) -> None:
    import hashlib
    import active_project_paths
    import direct_rag_engine_collection
    import direct_rag_index

    project_57 = _versioned_project(tmp_path / "Owner57", "5.7")
    source = project_57.parent / "Source" / "Game" / "Public"
    source.mkdir(parents=True)
    (source / "Refresh57.h").write_text(
        "UCLASS()\nclass FRefresh57 {};\n",
        encoding="utf-8",
    )
    data = tmp_path / "data"
    index_58 = _build_versioned_index(
        data,
        "unreal58",
        "5.8",
        ROOT,
        [_document("e58", "unreal_source", "Engine58Sentinel", None)],
    )
    before = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in index_58.parent.iterdir()
        if path.is_file()
    }
    monkeypatch.setattr(
        active_project_paths,
        "load_shared_config",
        lambda: {"activeProject": str(project_57)},
    )

    def collect_tiny_engine(**kwargs):
        stage = Path(kwargs["stage"])
        (stage / "raw_symbols.jsonl").write_text(
            json.dumps(_document("engine57", "unreal_symbol", "Engine57Evidence", None)) + "\n",
            encoding="utf-8",
        )
        return [{"name": "collect-engine-public-symbols", "ok": True}], None

    monkeypatch.setattr(direct_rag_engine_collection, "ensure_engine_inputs", collect_tiny_engine)
    runtime = SimpleNamespace(index=index_58, workspace=ROOT, notify=lambda *_args: None)

    refreshed = direct_rag_index.rag_refresh(
        runtime,
        {"scope": "project_source", "allowEditorLaunch": False},
    ).payload

    index_57 = data / "unreal57" / "rag.sqlite"
    assert refreshed["ok"] is True, refreshed
    assert index_57.is_file()
    manifest = json.loads((index_57.parent / "build_manifest.json").read_text(encoding="utf-8"))
    assert manifest["engineVersion"] == "5.7"
    assert manifest["engineAssociation"] == "5.7"
    assert manifest["corpusCapabilities"]["engineEvidence"] is True
    with sqlite3.connect(index_57) as connection:
        generation = connection.execute(
            "select value from index_meta where key = 'generation_id'"
        ).fetchone()[0]
    assert generation == manifest["generationId"]
    after = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in index_58.parent.iterdir()
        if path.is_file()
    }
    assert after == before

    result = rag_search(
        runtime,
        {"query": "FRefresh57", "scope": "mixed", "project": str(project_57)},
    ).payload
    assert result["ok"] is True
    assert result["indexPath"] == str(index_57.resolve())
    assert "Engine58Sentinel" not in result["evidence"]


def test_custom_engine_association_requires_matching_manifest_provenance(
    monkeypatch,
    tmp_path: Path,
) -> None:
    import direct_rag_index_registry as registry

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    project = _versioned_project(tmp_path / "OwnerCustom", "CustomBuild-A")
    data = tmp_path / "data"
    base = _build_versioned_index(
        data,
        "unreal58",
        "5.8",
        workspace,
        [
            _document("base", "unreal_source", "Base", None),
            _document("project", "unreal_project_text", "CustomProject", project),
        ],
    )
    monkeypatch.setattr(
        registry,
        "project_engine_version",
        lambda _project, _workspace: {
            "ok": True,
            "project": str(project),
            "engineAssociation": "CustomBuild-A",
            "engineVersion": "5.8",
        },
    )

    mismatch = registry.resolve_request_index(
        base,
        workspace,
        project_selector=str(project),
    )
    assert mismatch["ok"] is False
    assert mismatch["errorCode"] == "RAG_ENGINE_INDEX_MISMATCH"

    manifest_path = base.parent / "build_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["engineAssociation"] = "CustomBuild-A"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    matched = registry.resolve_request_index(
        base,
        workspace,
        project_selector=str(project),
    )
    assert matched["ok"] is True
    assert matched["index"] == str(base.resolve())


def test_fresh_configured_custom_index_can_be_created_without_guessing_a_sibling(
    monkeypatch,
    tmp_path: Path,
) -> None:
    import direct_rag_index_registry as registry
    import workspace_paths

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    project = _versioned_project(tmp_path / "OwnerCustom", "StudioFork")
    configured = tmp_path / "data" / "studio" / "rag.sqlite"
    monkeypatch.setattr(workspace_paths, "resolve_engine_version", lambda _workspace: "5.8")
    monkeypatch.setattr(
        registry,
        "project_engine_version",
        lambda _project, _workspace: {
            "ok": True,
            "project": str(project),
            "engineAssociation": "StudioFork",
            "engineVersion": "5.8",
        },
    )

    resolution = registry.resolve_request_index(
        configured,
        workspace,
        project_selector=str(project),
        use_active=False,
        allow_unbuilt=True,
    )

    assert resolution["ok"] is True
    assert resolution["index"] == str(configured.resolve())
    assert resolution["projectEngineAssociation"] == "StudioFork"
    assert resolution["unbuiltIndex"] is True


def test_unrelated_torn_sibling_does_not_poison_exact_healthy_lookup(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    project = _versioned_project(tmp_path / "Owner58", "5.8")
    data = tmp_path / "data"
    base = _build_versioned_index(
        data,
        "unreal58",
        "5.8",
        workspace,
        [_document("healthy", "unreal_project_text", "HealthyNeedle", project)],
    )
    torn = _build_versioned_index(
        data,
        "unreal58-torn-0000000000",
        "5.8",
        workspace,
        [_document("irrelevant", "unreal_source", "IrrelevantNeedle", None)],
    )
    torn_manifest = torn.parent / "build_manifest.json"
    payload = json.loads(torn_manifest.read_text(encoding="utf-8"))
    payload["generationId"] = "torn-sibling-generation"
    torn_manifest.write_text(json.dumps(payload), encoding="utf-8")

    result = rag_search(
        SimpleNamespace(index=base, workspace=workspace),
        {"query": "HealthyNeedle", "scope": "project", "project": str(project)},
    ).payload

    assert result["ok"] is True
    assert result["indexPath"] == str(base.resolve())


def test_transaction_directories_are_never_enumerated_as_live_shards(
    tmp_path: Path,
) -> None:
    import shutil

    from direct_rag_shard_selection import candidate_indexes

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data = tmp_path / "data"
    base = _build_versioned_index(
        data,
        "unreal58",
        "5.8",
        workspace,
        [_document("live", "unreal_source", "LiveNeedle", None)],
    )
    for name in (
        ".unreal58.direct-refresh-copy",
        ".unreal58.direct-refresh-backup-copy",
    ):
        shutil.copytree(base.parent, data / name)

    assert candidate_indexes(base) == [base.resolve()]


def test_managed_custom_associations_get_distinct_stable_shard_names(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import direct_rag_index_registry as registry
    from direct_rag_unbuilt_shard import shard_namespace

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data = tmp_path / "data"
    base = _build_versioned_index(
        data,
        "unreal58",
        "5.8",
        workspace,
        [_document("engine", "unreal_source", "EngineNeedle", None)],
    )
    project_a = _versioned_project(tmp_path / "OwnerA", "Studio-A")
    project_b = _versioned_project(tmp_path / "OwnerB", "Studio-B")
    project_numeric = _versioned_project(tmp_path / "OwnerNumeric", "UE_5.8")
    bindings = {
        project_a.resolve(): "Studio-A",
        project_b.resolve(): "Studio-B",
        project_numeric.resolve(): "UE_5.8",
    }

    def resolve(project: Path, _workspace: Path) -> dict:
        descriptor = project.resolve()
        return {
            "ok": True,
            "project": str(descriptor),
            "engineAssociation": bindings[descriptor],
            "engineVersion": "5.8",
        }

    monkeypatch.setattr(registry, "project_engine_version", resolve)
    resolved_a = registry.resolve_request_index(
        base, workspace, project_selector=str(project_a), use_active=False, allow_unbuilt=True
    )
    resolved_b = registry.resolve_request_index(
        base, workspace, project_selector=str(project_b), use_active=False, allow_unbuilt=True
    )
    resolved_numeric = registry.resolve_request_index(
        base,
        workspace,
        project_selector=str(project_numeric),
        use_active=False,
        allow_unbuilt=True,
    )

    assert resolved_a["index"] != resolved_b["index"]
    assert Path(resolved_a["index"]).parent.name == shard_namespace("5.8", "studio-a")
    assert Path(resolved_b["index"]).parent.name == shard_namespace("5.8", "studio-b")
    assert resolved_numeric["index"] == str(base.resolve())


def test_named_resolution_surfaces_transition_only_without_a_healthy_match(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import direct_rag_named_index as named
    from direct_rag_generation_identity import RagGenerationTransitionError

    broken = tmp_path / "broken.sqlite"
    healthy = tmp_path / "healthy.sqlite"
    broken.write_bytes(b"fixture")
    healthy.write_bytes(b"fixture")
    descriptor = tmp_path / "Game.uproject"
    descriptor.write_text("{}", encoding="utf-8")

    def degraded(index: Path, _name: str, _workspace: Path):
        if index == broken:
            raise RagGenerationTransitionError("broken shard is swapping")
        return [(healthy.resolve(), str(tmp_path), descriptor, "5.8", "")]

    monkeypatch.setattr(named, "match_named_candidate", degraded)
    success = named.resolve_named_index([broken, healthy], "Game", tmp_path)
    assert success is not None and success["ok"] is True
    assert success["index"] == str(healthy.resolve())

    monkeypatch.setattr(
        named,
        "match_named_candidate",
        lambda *_args: (_ for _ in ()).throw(
            RagGenerationTransitionError("all matching shards are swapping")
        ),
    )
    failed = named.resolve_named_index([broken], "Game", tmp_path)
    assert failed is not None and failed["ok"] is False
    assert failed["errorCode"] == "RAG_GENERATION_TRANSITION"
    assert failed["retryAllowed"] is True


def test_hybrid_request_never_mixes_two_promoted_generations(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import direct_rag_lexical

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    project = _versioned_project(tmp_path / "Owner", "5.8")
    index = _build_versioned_index(
        tmp_path / "data",
        "unreal58",
        "5.8",
        workspace,
        [
            _document("engine", "unreal_symbol", "FlipNeedle engine", None, symbol="FlipNeedle"),
            _document("project", "unreal_symbol", "FlipNeedle project", project, symbol="FlipNeedle"),
        ],
    )
    real_fetch = direct_rag_lexical.fetch_fts_rows
    swapped = False

    def fetch_then_promote(*args, **kwargs):
        nonlocal swapped
        rows = real_fetch(*args, **kwargs)
        if not swapped:
            swapped = True
            with sqlite3.connect(index) as connection:
                connection.execute(
                    "update index_meta set value = 'generation-two' where key = 'generation_id'"
                )
                connection.commit()
            manifest_path = index.parent / "build_manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["generationId"] = "generation-two"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        return rows

    monkeypatch.setattr(direct_rag_lexical, "fetch_fts_rows", fetch_then_promote)
    result = rag_search(
        SimpleNamespace(index=index, workspace=workspace),
        {
            "query": "FlipNeedle",
            "scope": "mixed",
            "project": str(project),
            "hybrid": True,
        },
    ).payload

    assert swapped is True
    assert result["ok"] is False
    assert result["errorCode"] == "RAG_GENERATION_TRANSITION"
    assert result["retry"] == {"allowed": True, "mode": "same_arguments"}
    assert "evidence" not in result


def test_symbol_freshness_transition_is_retryable_at_capability_boundary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import direct_rag_symbol
    from direct_rag_generation_identity import RagGenerationTransitionError

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    project = _versioned_project(tmp_path / "Owner", "5.8")
    index = _build_versioned_index(
        tmp_path / "data",
        "unreal58",
        "5.8",
        workspace,
        [_document("symbol", "unreal_symbol", "BoundarySymbol", project, symbol="BoundarySymbol")],
    )

    def changing(*_args, **_kwargs):
        raise RagGenerationTransitionError("freshness observed a new generation")

    monkeypatch.setattr(direct_rag_symbol, "project_freshness", changing)
    result = symbol_lookup_capability(
        SimpleNamespace(index=index, workspace=workspace),
        {"query": "BoundarySymbol", "project": str(project)},
    ).payload

    assert result["ok"] is False
    assert result["errorCode"] == "RAG_GENERATION_TRANSITION"
    assert result["retry"] == {"allowed": True, "mode": "same_arguments"}


def test_engine_collection_uses_numeric_hint_and_rejects_build_version_drift(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import active_project_paths
    import direct_rag_engine_collection
    import workspace_paths

    engine = tmp_path / "CustomEngine"
    (engine / "Engine" / "Source").mkdir(parents=True)
    build_dir = engine / "Engine" / "Build"
    build_dir.mkdir(parents=True)
    (build_dir / "Build.version").write_text(
        json.dumps({"MajorVersion": 5, "MinorVersion": 8}),
        encoding="utf-8",
    )
    stage = tmp_path / "stage"
    stage.mkdir()
    observed: list[str] = []
    calls: list[str] = []
    monkeypatch.setattr(active_project_paths, "indexing_tier", lambda _workspace: "standard")

    def resolve(hint: str, _workspace: Path) -> dict:
        observed.append(hint)
        return {"ok": True, "engineRoot": str(engine)}

    monkeypatch.setattr(workspace_paths, "resolve_engine_root_for_association", resolve)
    steps, failed = direct_rag_engine_collection.ensure_engine_inputs(
        workspace=tmp_path,
        project=tmp_path / "Game.uproject",
        stage=stage,
        engine_binding={"engineVersion": "5.7", "engineAssociation": ""},
        run_script=lambda _ws, script, *_args: calls.append(script) or {"ok": True},
        emit=lambda _message: None,
    )

    assert observed == ["5.7"]
    assert failed == "resolve-engine-root"
    assert steps[-1]["errorCode"] == "RAG_ENGINE_ROOT_VERSION_MISMATCH"
    assert calls == []


def test_tier_downgrade_prunes_engine_inputs_and_records_matching_manifest(
    tmp_path: Path,
) -> None:
    from direct_rag_engine_tier import prune_engine_inputs_for_tier
    from index_inputs import existing_input_paths

    project = _versioned_project(tmp_path / "Owner", "5.8")
    for tier, engine_evidence in (("standard", True), ("lite", False)):
        stage = tmp_path / tier
        stage.mkdir()
        (stage / "raw_source.jsonl").write_text(
            json.dumps(_document("source", "unreal_source", "EngineSource", None)) + "\n",
            encoding="utf-8",
        )
        (stage / "raw_symbols.jsonl").write_text(
            json.dumps(_document("symbol", "unreal_symbol", "EngineSymbol", None)) + "\n",
            encoding="utf-8",
        )
        (stage / "sidecar_symbols_meta.jsonl").write_text("{}\n", encoding="utf-8")
        (stage / "raw_projects.jsonl").write_text(
            json.dumps(_document("project", "unreal_project_text", "ProjectText", project)) + "\n",
            encoding="utf-8",
        )

        prune_engine_inputs_for_tier(stage, tier)
        build(
            argparse.Namespace(
                input=[str(path) for path in existing_input_paths(stage)],
                out_dir=str(stage),
                workspace_root=str(tmp_path),
                engine_version="5.8",
                engine_association="",
                indexing_tier=tier,
                chunk_tokens=900,
                overlap_tokens=120,
            )
        )
        manifest = json.loads((stage / "build_manifest.json").read_text(encoding="utf-8"))
        assert not (stage / "raw_source.jsonl").exists()
        assert manifest["indexingTier"] == tier
        assert manifest["corpusCapabilities"]["engineEvidence"] is engine_evidence
        if tier == "lite":
            assert not (stage / "raw_symbols.jsonl").exists()
