#!/usr/bin/env python
"""Claim validators use strict project row filtering."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from blueprint_claim_validate import validate_blueprint_claims  # noqa: E402
from material_claim_validate import validate_material_claims  # noqa: E402


def _write_material_rows(path: Path, rows: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")


def test_material_claim_validate_blocks_other_project_rows(tmp_path):
    raw_path = tmp_path / "raw_material_metadata.jsonl"
    _write_material_rows(
        raw_path,
        [
            {
                "metadata": {
                    "project": "",
                    "asset_path": "/Game/Legacy/M_Old",
                    "expressions": [{"class": "MaterialExpressionScalarParameter", "name": "LegacyParam"}],
                }
            },
            {
                "metadata": {
                    "project": "DemoGame",
                    "asset_path": "/Game/Shaders/MF_Test/M_Demo",
                    "expressions": [{"class": "MaterialExpressionScalarParameter", "name": "DemoParam"}],
                }
            },
            {
                "metadata": {
                    "project": "OtherGame",
                    "asset_path": "/Game/Other/M_Other",
                    "expressions": [{"class": "MaterialExpressionScalarParameter", "name": "OtherParam"}],
                }
            },
        ],
    )
    payload = validate_material_claims(
        ["M_Demo material uses DemoParam"],
        index_dir=tmp_path,
        project_name="DemoGame",
    )
    assert payload["projectName"] == "DemoGame"
    assert payload["results"]
    assets = payload["results"][0]["matchingAssets"]
    assert assets == ["/Game/Shaders/MF_Test/M_Demo"]


def test_blueprint_claim_validate_blocks_other_project_rows(tmp_path):
    raw_path = tmp_path / "raw_blueprint_metadata.jsonl"
    raw_path.write_text(
        json.dumps(
            {
                "metadata": {
                    "project": "OtherGame",
                    "asset_path": "/Game/BP_Other",
                    "graph_links": [],
                }
            },
            ensure_ascii=False,
        )
        + "\n"
        + json.dumps(
            {
                "metadata": {
                    "project": "DemoGame",
                    "asset_path": "/Game/BP_Demo",
                    "graph_links": [{"from_node": "Event", "to_node": "Print"}],
                }
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    payload = validate_blueprint_claims(
        ["BP_Demo event pin link"],
        index_dir=tmp_path,
        project_name="DemoGame",
    )
    assert payload["results"][0]["matchingAssets"] == ["/Game/BP_Demo"]

