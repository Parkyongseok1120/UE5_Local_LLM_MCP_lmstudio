#!/usr/bin/env python
"""Asset taxonomy config and classification helpers."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from asset_taxonomy import (  # noqa: E402
    classify_ue_asset_class,
    graph_lookup_guidance,
    load_taxonomy,
    taxonomy_text_lines,
)


def test_taxonomy_json_loads():
    data = load_taxonomy()
    assert data.get("version") == 1
    assert len(data.get("work_domains") or []) == 10
    assert len(data.get("sections") or []) == 21


def test_classify_material_layer():
    info = classify_ue_asset_class("MaterialFunctionMaterialLayer")
    assert info is not None
    assert info["item_name"] == "Material Layer"
    assert info["rag_coverage"] == "graph_material"


def test_classify_master_material_graph_export():
    info = classify_ue_asset_class("Material")
    assert info is not None
    assert info["rag_coverage"] == "graph_material"


def test_classify_material_function_graph_export():
    info = classify_ue_asset_class("MaterialFunction")
    assert info is not None
    assert info["rag_coverage"] == "graph_material"


def test_classify_data_table_structured():
    info = classify_ue_asset_class("DataTable")
    assert info is not None
    assert info["rag_coverage"] == "structured_metadata"


def test_taxonomy_text_lines_include_coverage():
    lines = taxonomy_text_lines("MaterialFunctionMaterialLayer")
    joined = "\n".join(lines)
    assert "rag_coverage: graph_material" in joined
    assert "taxonomy_item: Material Layer" in joined


def test_graph_lookup_guidance_for_material_layer():
    hints = graph_lookup_guidance(asset_class="MaterialFunctionMaterialLayer", asset_path="/Game/Foo/ML_BaseColor")
    assert any("Material Layer" in hint for hint in hints)
    assert any("graph_material" in hint.lower() or "material_metadata" in hint.lower() for hint in hints)
