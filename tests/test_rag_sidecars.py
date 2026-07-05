from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import rag_search  # noqa: E402
import symbol_graph  # noqa: E402


def _make_index(path: Path) -> Path:
    conn = sqlite3.connect(path)
    conn.execute(
        """
        create table chunks(
            chunk_id text,
            source text,
            title text,
            locator text,
            chunk_index integer,
            text text,
            project text,
            relative_path text,
            extension text,
            layer text,
            doc_type text,
            genre text,
            symbol_name text,
            symbol_kind text,
            module_name text,
            error_code text,
            error_file text,
            path_only text
        )
        """
    )
    conn.execute("create virtual table chunks_fts using fts5(title, text)")
    rows = [
        (
            "base:compile",
            "project_guideline",
            "Unreal compile error triage",
            "RAG_Project_Guidelines/Unreal_Programming/01_Unreal_Compile_Error_Triage.md",
            0,
            "C1083 generated.h UHT Build.cs module dependency EnhancedInputComponent UserWidget ADemoActor",
            "",
            "",
            ".md",
            "compile_fix",
            "guideline",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
        ),
        (
            "base:source",
            "unreal_project_text",
            "DemoActor.cpp",
            "Source/Demo/Private/DemoActor.cpp",
            0,
            "ADemoActor Fire EnhancedInputComponent UserWidget generated.h",
            "Demo",
            "Source/Demo/Private/DemoActor.cpp",
            ".cpp",
            "project_text",
            "source",
            "",
            "ADemoActor",
            "class",
            "Demo",
            "",
            "",
            "",
        ),
    ]
    for idx, row in enumerate(rows, start=1):
        conn.execute(
            """
            insert into chunks values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            row,
        )
        conn.execute("insert into chunks_fts(rowid, title, text) values (?, ?, ?)", (idx, row[2], row[5]))
    conn.commit()
    conn.close()
    return path


def _sidecar(rows: list[dict], sidecar_type: str) -> dict | None:
    for row in rows:
        if row.get("source") == "rag_sidecar" and row.get("sidecarType") == sidecar_type:
            return row
    return None


def test_rag_search_works_when_symbol_graph_missing(tmp_path, monkeypatch):
    index = _make_index(tmp_path / "rag.sqlite")
    monkeypatch.setattr(symbol_graph, "default_graph_path", lambda root=None: tmp_path / "missing.json")

    rows = rag_search.search(index, "ADemoActor C1083", 5, rag_search.SearchOptions(mode="compile_fix"))

    assert rows
    assert _sidecar(rows, "symbol_graph") is None


def test_rag_search_includes_symbol_graph_sidecar_when_available(tmp_path, monkeypatch):
    index = _make_index(tmp_path / "rag.sqlite")
    graph_path = tmp_path / "symbol_graph.json"
    graph_path.write_text(
        json.dumps(
            {
                "version": 1,
                "symbols": [
                    {
                        "symbol_name": "ADemoActor",
                        "symbol_kind": "class",
                        "file_path": "Source/Demo/Public/DemoActor.h",
                        "line_start": 7,
                        "line_end": 18,
                        "module_name": "Demo",
                        "owner_build_cs": "Source/Demo/Demo.Build.cs",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(symbol_graph, "default_graph_path", lambda root=None: graph_path)

    rows = rag_search.search(index, "Fix ADemoActor C1083", 5, rag_search.SearchOptions(mode="compile_fix"))
    sidecar = _sidecar(rows, "symbol_graph")

    assert sidecar is not None
    assert sidecar["items"][0]["symbol"] == "ADemoActor"
    assert sidecar["items"][0]["ownerBuildCs"].endswith("Demo.Build.cs")


def test_module_fix_enhanced_input_header_produces_module_resolver_sidecar(tmp_path):
    index = _make_index(tmp_path / "rag.sqlite")

    rows = rag_search.search(
        index,
        "fatal error C1083 Cannot open include file EnhancedInputComponent.h",
        5,
        rag_search.SearchOptions(mode="module_fix"),
    )
    sidecar = _sidecar(rows, "module_resolver")

    assert sidecar is not None
    assert sidecar["items"][0]["module"] == "EnhancedInput"


def test_module_fix_user_widget_header_produces_umg_sidecar(tmp_path):
    index = _make_index(tmp_path / "rag.sqlite")

    rows = rag_search.search(
        index,
        "fatal error C1083 Cannot open include file UserWidget.h",
        5,
        rag_search.SearchOptions(mode="module_fix"),
    )
    sidecar = _sidecar(rows, "module_resolver")

    assert sidecar is not None
    assert sidecar["items"][0]["module"] == "UMG"


def test_reflection_generated_h_query_adds_error_route_sidecar(tmp_path):
    index = _make_index(tmp_path / "rag.sqlite")

    rows = rag_search.search(
        index,
        "BadActor.generated.h must be the last include before UCLASS",
        5,
        rag_search.SearchOptions(mode="reflection_fix"),
    )
    sidecar = _sidecar(rows, "error_route")

    assert sidecar is not None
    assert sidecar["items"][0]["broadMode"] == "reflection_fix"
    assert rows[0].get("resolved_mode") == "reflection_fix"


def test_architecture_sidecar_missing_map_is_nonfatal(tmp_path, monkeypatch):
    index = _make_index(tmp_path / "rag.sqlite")
    monkeypatch.setattr(rag_search, "default_architecture_map_path", lambda root=None: tmp_path / "missing.json")

    rows = rag_search.search(index, "Review ADemoActor architecture ownership", 5, rag_search.SearchOptions(mode="review"))

    assert rows
    assert _sidecar(rows, "architecture_map") is None


def test_architecture_sidecar_uses_compact_map_rows(tmp_path, monkeypatch):
    index = _make_index(tmp_path / "rag.sqlite")
    arch_path = tmp_path / "architecture_map.json"
    arch_path.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "project": {"name": "Demo"},
                "types": [
                    {
                        "name": "UDemoCombatComponent",
                        "module": "Demo",
                        "header": "Source/Demo/Public/DemoCombatComponent.h",
                        "cpp": "Source/Demo/Private/DemoCombatComponent.cpp",
                        "category": "ActorComponent",
                        "responsibilityHints": ["hint: combat state / action execution candidate"],
                        "riskFlags": ["blueprint_facing_surface"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(rag_search, "default_architecture_map_path", lambda root=None: arch_path)

    rows = rag_search.search(
        index,
        "Review ADemoActor UDemoCombatComponent architecture ownership",
        5,
        rag_search.SearchOptions(mode="review"),
    )
    sidecar = _sidecar(rows, "architecture_map")

    assert sidecar is not None
    assert sidecar["items"][0]["type"] == "UDemoCombatComponent"
    assert sidecar["items"][0]["riskFlags"] == ["blueprint_facing_surface"]
    assert "architecture_map" in sidecar["locator"]


def test_architecture_sidecar_does_not_trigger_on_generic_review(tmp_path, monkeypatch):
    index = _make_index(tmp_path / "rag.sqlite")
    arch_path = tmp_path / "architecture_map.json"
    arch_path.write_text(json.dumps({"types": [{"name": "UDemoCombatComponent"}]}), encoding="utf-8")
    monkeypatch.setattr(rag_search, "default_architecture_map_path", lambda root=None: arch_path)

    rows = rag_search.search(
        index,
        "Review ADemoActor compile behavior",
        5,
        rag_search.SearchOptions(mode="review"),
    )

    assert _sidecar(rows, "architecture_map") is None


def test_architecture_sidecar_does_not_appear_for_unrelated_mode(tmp_path, monkeypatch):
    index = _make_index(tmp_path / "rag.sqlite")
    arch_path = tmp_path / "architecture_map.json"
    arch_path.write_text(json.dumps({"types": [{"name": "UDemoCombatComponent"}]}), encoding="utf-8")
    monkeypatch.setattr(rag_search, "default_architecture_map_path", lambda root=None: arch_path)

    rows = rag_search.search(
        index,
        "ADemoActor UDemoCombatComponent architecture ownership",
        5,
        rag_search.SearchOptions(mode="api_lookup"),
    )

    assert _sidecar(rows, "architecture_map") is None
