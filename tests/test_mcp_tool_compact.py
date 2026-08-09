#!/usr/bin/env python
"""Tests for MCP tool response compaction."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from asset_graph_lookup import graph_detail_limits, lookup_asset_graph  # noqa: E402
from mcp_tool_compact import (  # noqa: E402
    compact_architecture_payload,
    compact_asset_graph_payload,
    compact_json_text,
    compact_sync_metadata_payload,
    max_tool_result_chars,
    truncate_text,
)
from token_budget import code_detail_limits, resolve_code_detail  # noqa: E402


def test_default_tool_result_ceiling_is_high():
    assert max_tool_result_chars() >= 80_000


def test_code_detail_limits_tiers():
    compact = code_detail_limits("compact")
    medium = code_detail_limits("medium")
    assert compact["assembly_chars"] < medium["assembly_chars"]
    assert compact["read_bytes"] < medium["read_bytes"]
    assert resolve_code_detail("bogus") == "compact"


def test_truncate_text():
    assert truncate_text("abc", 10) == "abc"
    long = "x" * 100
    out = truncate_text(long, 20)
    assert len(out) > 20
    assert "truncated" in out


def test_compact_sync_metadata_payload_drops_full_status_files():
    payload = {
        "ok": False,
        "projectName": "OtherGame",
        "ingestReason": "metadata_status_needs_export_or_ingest",
        "ingest": {"ok": True, "reason": "x", "stdout": "x" * 5000, "stderr": ""},
        "rebuild": {"ok": True, "stdout": "done"},
        "metadataStatusBefore": {"ok": False, "files": {"material": {"rowCount": 999}}},
        "metadataStatusAfter": {"ok": False, "missingKinds": ["texture"], "files": {"material": {"rowCount": 257}}},
        "exportResult": {"ok": False, "error": "boom", "traceback": "t" * 3000},
        "nextActions": ["a", "b", "c", "d", "e"],
    }
    compact = compact_sync_metadata_payload(payload)
    assert "metadataStatusBefore" not in compact
    assert compact["metadataStatusAfter"]["missingKinds"] == ["texture"]
    assert "files" not in (compact.get("metadataStatusAfter") or {})
    assert len(compact["exportResult"]["error"]) <= 500


def test_lookup_empty_graph_sets_stop_retry(tmp_path: Path):
    index_dir = tmp_path / "data"
    index_dir.mkdir()
    row = {
        "metadata": {
            "asset_path": "/Game/01_Character/98_Shading/M_Layer/ML_BaseColor",
            "asset_type": "MaterialFunctionMaterialLayer",
            "project": "OtherGame",
            "expressions": [],
            "graph_edges": [],
        }
    }
    (index_dir / "raw_material_metadata.jsonl").write_text(
        json.dumps(row) + "\n",
        encoding="utf-8",
    )
    payload = lookup_asset_graph(
        "/Game/01_Character/98_Shading/M_Layer/ML_BaseColor",
        index_dir=index_dir,
        project_name="OtherGame",
        compact=True,
    )
    assert payload["ok"] is True
    assert payload["primary"]["graphExported"] is False
    assert payload["primary"]["stopRetryingLookup"] is True
    compact = compact_asset_graph_payload(payload)
    assert compact["stopRetryingLookup"] is True
    assert "matches" not in compact or len(compact.get("otherMatches") or []) == 0


def test_short_name_lookup_does_not_match_falloff_suffix(tmp_path: Path):
    index_dir = tmp_path / "data"
    index_dir.mkdir()
    rows = [
        {
            "metadata": {
                "asset_path": "/Game/01_Character/98_Shading/M_Layer/ML_BaseColor",
                "asset_type": "MaterialFunctionMaterialLayer",
                "project": "OtherGame",
                "expressions": [{"name": "Tex_1", "class": "MaterialExpressionTextureSample"}],
                "graph_edges": [{"from": "Tex_1", "to": "Out_1", "to_input": "A"}],
            }
        },
        {
            "metadata": {
                "asset_path": "/Game/Samples/Functions/ML_BaseColorFallOff",
                "asset_type": "MaterialFunction",
                "project": "OtherGame",
                "expressions": [{"name": "Other_1", "class": "MaterialExpressionConstant"}],
                "graph_edges": [],
            }
        },
    ]
    (index_dir / "raw_material_metadata.jsonl").write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n",
        encoding="utf-8",
    )
    payload = lookup_asset_graph(
        "ML_BaseColor",
        asset_kind="material",
        index_dir=index_dir,
        project_name="OtherGame",
        compact=True,
    )
    assert payload["ok"] is True
    assert payload["matchCount"] == 1
    assert payload["primary"]["assetPath"].endswith("/ML_BaseColor")


def test_compact_lookup_large_graph_stays_under_tool_budget(tmp_path: Path):
    index_dir = tmp_path / "data"
    index_dir.mkdir()
    expressions = [
        {
            "name": f"Node_{index}",
            "class": "MaterialExpressionMultiply",
            "input_wires": {f"In_{index}": f"Node_{index - 1}"} if index else {},
        }
        for index in range(200)
    ]
    graph_edges = [
        {"from": f"Node_{index}", "to": f"Node_{index + 1}", "to_input": f"In_{index + 1}"}
        for index in range(199)
    ]
    row = {
        "metadata": {
            "asset_path": "/Game/Materials/M_Huge",
            "asset_type": "Material",
            "project": "Demo",
            "expressions": expressions,
            "graph_edges": graph_edges,
        }
    }
    (index_dir / "raw_material_metadata.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
    payload = lookup_asset_graph(
        "M_Huge",
        asset_kind="material",
        index_dir=index_dir,
        project_name="Demo",
        detail="compact",
    )
    compact = compact_asset_graph_payload(payload)
    text = compact_json_text(compact, limit=graph_detail_limits("compact")["max_tool_chars"])
    assert payload["primary"]["expressionCount"] == 200
    assert len(payload["primary"]["expressions"]) == 12
    assert len(payload["primary"]["graphEdges"]) == 20
    assert payload["primary"]["graphSampled"] is True
    assert payload["primary"]["nextDetailLevel"] == "medium"
    assert compact["nextDetailLevel"] == "medium"
    assert len(text) <= 10_000


def test_lookup_medium_detail_returns_more_nodes(tmp_path: Path):
    index_dir = tmp_path / "data"
    index_dir.mkdir()
    expressions = [
        {"name": f"Node_{index}", "class": "MaterialExpressionMultiply", "input_wires": {}}
        for index in range(80)
    ]
    row = {
        "metadata": {
            "asset_path": "/Game/Materials/M_Mid",
            "asset_type": "Material",
            "project": "Demo",
            "expressions": expressions,
            "graph_edges": [],
        }
    }
    (index_dir / "raw_material_metadata.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
    payload = lookup_asset_graph(
        "M_Mid",
        asset_kind="material",
        index_dir=index_dir,
        project_name="Demo",
        detail="medium",
    )
    assert payload["detailLevel"] == "medium"
    assert len(payload["primary"]["expressions"]) == 36
    assert payload["primary"]["nextDetailLevel"] == "large"


def test_architecture_compaction_preserves_safety_gate_and_counts():
    payload = {
        "ok": True,
        "projectRoot": "Demo",
        "focus": {"symbols": ["Run"], "unmatchedSymbols": []},
        "graphEvidence": {"complete": True},
        "topology": {
            "owners": [{"name": f"Owner{index}", "files": [f"F{n}" for n in range(8)]} for index in range(12)],
            "boundaryDependencies": [{"from": f"A{index}", "to": "B", "evidence": list(range(8))} for index in range(12)],
            "sourceDependencyCycles": [["A", "B", "A"]],
        },
        "dataFlow": {"flows": [{"symbol": str(index)} for index in range(20)]},
        "stateTransitions": {
            "transitions": [{"symbol": str(index)} for index in range(20)],
            "stateOwnershipCandidates": [{"owner": str(index)} for index in range(20)],
        },
        "lifecycle": {
            "callbacks": [{"function": str(index)} for index in range(20)],
            "asyncEventBoundaries": [{"call": str(index)} for index in range(20)],
            "pairingGaps": [{"owner": "Demo", "missingCandidatePhase": "runtime_stop"}],
        },
        "proposalValidation": {
            "ok": False,
            "implementationGate": {"writesAllowed": False, "requiredValidation": ["build"]},
        },
        "proofBoundary": "candidates are not runtime proof",
    }

    compact = compact_architecture_payload(payload, "compact")

    assert compact["summary"]["ownerCount"] == 12
    assert compact["summary"]["stateOwnershipCandidateCount"] == 20
    assert compact["summary"]["lifecycleCallbackCandidateCount"] == 20
    assert len(compact["topology"]["owners"]) == 8
    assert len(compact["stateTransitions"]["stateOwnershipCandidates"]) == 12
    assert len(compact["lifecycle"]["callbacks"]) == 12
    assert compact["lifecycle"]["pairingGaps"][0]["owner"] == "Demo"
    assert compact["topology"]["sourceDependencyCycles"] == [["A", "B", "A"]]
    assert compact["proposalValidation"]["implementationGate"]["writesAllowed"] is False
    assert compact["truncated"] is True
    assert compact["nextDetailLevel"] == "standard"


def test_architecture_full_detail_is_not_sampled():
    payload = {"ok": True, "topology": {"owners": list(range(20))}}
    full = compact_architecture_payload(payload, "full")
    assert full["topology"]["owners"] == list(range(20))
    assert full["truncated"] is False


def test_architecture_full_detail_remains_hard_bounded_for_large_graphs():
    payload = {
        "ok": True,
        "topology": {
            "owners": [
                {
                    "name": f"Owner{index}",
                    "evidence": {
                        "kind": "project_source",
                        "location": f"Source/Game/File{index}.cpp:10",
                        "fileHash": "x" * 40,
                    },
                }
                for index in range(200)
            ]
        },
    }

    full = compact_architecture_payload(payload, "full")

    assert len(full["topology"]["owners"]) == 24
    assert full["truncated"] is True
    assert "nextDetailLevel" not in full
    assert "fileHash" not in full["topology"]["owners"][0]["evidence"]


def test_architecture_rejection_precedes_sampled_evidence_in_serialized_output():
    payload = {
        "ok": False,
        "errorCode": "ARCHITECTURE_PROPOSAL_INVALID",
        "retryable": True,
        "stopCurrentWorkflow": False,
        "requiredNextAction": "revise_architecture_proposal",
        "nextActionIsTool": False,
        "proposalValidation": {
            "ok": False,
            "issues": ["select one callable RPC ownership path"],
            "implementationGate": {"writesAllowed": False},
        },
        "dataFlow": {"flows": [{"symbol": str(index)} for index in range(500)]},
        "candidatePortfolio": {
            "candidateCount": 500,
            "candidates": [{"name": f"candidate-{index}"} for index in range(500)],
        },
    }

    compact = compact_architecture_payload(payload, "full")
    encoded = json.dumps(compact)

    assert compact["errorCode"] == "ARCHITECTURE_PROPOSAL_INVALID"
    assert encoded.index("proposalValidation") < encoded.index("dataFlow")
    assert len(compact["dataFlow"]["flows"]) == 8
    assert len(compact["candidatePortfolio"]["candidates"]) == 4
    assert len(encoded) < 20_000

