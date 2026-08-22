from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import build_symbol_graph as build_symbol_graph_module  # noqa: E402
import symbol_graph as symbol_graph_module  # noqa: E402
from unreal_rag_mcp import (  # noqa: E402
    McpServer,
    _is_source_derived_project_row,
    annotate_other_project_rows,
)


def test_project_evidence_filters_use_injected_host_case_rules() -> None:
    row = {"project": "DemoProject", "source": "project_architecture"}

    assert not _is_source_derived_project_row(
        row,
        ["demoproject"],
        host_platform="linux",
    )
    assert _is_source_derived_project_row(
        row,
        ["demoproject"],
        host_platform="win32",
    )
    assert annotate_other_project_rows(
        [row],
        ["demoproject"],
        host_platform="linux",
    )[0]["otherProject"] is True
    assert "otherProject" not in annotate_other_project_rows(
        [row],
        ["demoproject"],
        host_platform="win32",
    )[0]


def test_project_evidence_filters_reject_unicode_casefold_alias() -> None:
    composed = "\u0130Project"
    decomposed = "I\u0307Project"
    assert composed.casefold() == decomposed.casefold()
    row = {"project": composed, "source": "project_architecture"}

    for host_platform in ("linux", "win32"):
        assert not _is_source_derived_project_row(
            row,
            [decomposed],
            host_platform=host_platform,
        )
        annotated = annotate_other_project_rows(
            [row],
            [decomposed],
            host_platform=host_platform,
        )
        assert annotated[0]["otherProject"] is True


def test_architecture_cache_does_not_use_unicode_normcase(
    tmp_path: Path,
    monkeypatch,
) -> None:
    upper = tmp_path / "\u00c4Project"
    lower = tmp_path / "\u00e4Project"
    assert str(upper).lower() == str(lower).lower()
    monkeypatch.setattr(
        build_symbol_graph_module,
        "source_inventory_signature",
        lambda _root: "same-inventory",
    )
    monkeypatch.setattr(
        build_symbol_graph_module,
        "graph_is_fresh_for_root",
        lambda _graph, _root: False,
    )
    monkeypatch.setattr(
        build_symbol_graph_module,
        "build_symbol_graph",
        lambda root: {"sourceRoot": str(root)},
    )
    monkeypatch.setattr(
        symbol_graph_module,
        "load_symbol_graph",
        lambda _workspace: {},
    )
    server = McpServer(tmp_path / "missing.sqlite")

    upper_graph, upper_source, _ = server.architecture_graph(upper)
    lower_graph, lower_source, _ = server.architecture_graph(lower)

    assert upper_source == "rebuilt"
    assert lower_source == "rebuilt"
    assert upper_graph["sourceRoot"] != lower_graph["sourceRoot"]
