#!/usr/bin/env python
"""Validate Blueprint wiring claims against exported Blueprint metadata."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from blueprint_graph_format import blueprint_row_search_text, iter_graph_nodes, iter_pin_links
from project_row_filter import filter_rows_by_project
from workspace_paths import load_shared_config

IDENT_RE = re.compile(
    r"\b(?:BP_[A-Za-z0-9_]+|WBP_[A-Za-z0-9_]+|[A-Z][A-Za-z0-9_]*(?:Blueprint|Widget|Component|Actor|Character|Controller|GameMode)?|IA_[A-Za-z0-9_]+)\b"
)
LINK_WORD_RE = re.compile(
    r"\b(pin|link|linked|connect|connected|wiring|calls?|bound|binding|event)\b|핀|연결|바인딩|호출",
    re.IGNORECASE,
)


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.is_file():
        return rows
    for line in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def _row_metadata(row: dict[str, Any]) -> dict[str, Any]:
    meta = row.get("metadata")
    return meta if isinstance(meta, dict) else row


def _asset_label(row: dict[str, Any]) -> str:
    meta = _row_metadata(row)
    return str(meta.get("asset_path") or row.get("path") or row.get("title") or "")


def _resolve_index_dir(index_dir: str | Path | None) -> Path:
    workspace = Path(__file__).resolve().parent.parent
    idx = Path(index_dir) if index_dir else workspace / "data" / "unreal58"
    return idx if idx.is_absolute() else workspace / idx


def _active_project_name() -> str:
    active = str(load_shared_config().get("activeProject") or "")
    if not active:
        return ""
    path = Path(active)
    return path.stem if path.suffix.lower() == ".uproject" else path.name


def _matching_rows(rows: list[dict[str, Any]], identifiers: list[str]) -> list[dict[str, Any]]:
    matches = []
    for row in rows:
        meta = _row_metadata(row)
        haystack = blueprint_row_search_text(meta)
        label = _asset_label(row).lower()
        if identifiers and not any(
            identifier.lower() in haystack or identifier.lower() in label for identifier in identifiers
        ):
            continue
        matches.append(row)
    return matches


def infer_blueprint_coverage(meta: dict[str, Any]) -> str:
    """Return full|partial|registry_only based on export metadata richness."""
    if meta.get("graph_links") or iter_pin_links(meta):
        return "full"
    nodes = iter_graph_nodes(meta)
    if nodes or meta.get("graphs"):
        has_pins = any(isinstance(node, dict) and node.get("pins") for node in nodes)
        if has_pins:
            return "full"
        return "partial"
    if any(meta.get(key) for key in ("variables", "functions", "interfaces", "parent_class")):
        return "partial"
    if meta.get("asset_path") or meta.get("generated_class"):
        return "registry_only"
    return "registry_only"


def _coverage_for_matches(matches: list[dict[str, Any]]) -> str:
    if not matches:
        return "registry_only"
    levels = {infer_blueprint_coverage(_row_metadata(row)) for row in matches}
    if "full" in levels:
        return "full"
    if "partial" in levels:
        return "partial"
    return "registry_only"


def validate_blueprint_claims(
    claims: list[str],
    index_dir: str | Path | None = None,
    project_name: str | None = None,
) -> dict[str, Any]:
    idx = _resolve_index_dir(index_dir)
    rows = _load_jsonl(idx / "raw_blueprint_metadata.jsonl")
    active_name = project_name or _active_project_name()
    if active_name:
        rows = filter_rows_by_project(rows, active_name)

    results: list[dict[str, Any]] = []
    for claim in claims:
        text = str(claim or "").strip()
        if not text:
            continue
        identifiers = list(dict.fromkeys(IDENT_RE.findall(text)))
        matches = _matching_rows(rows, identifiers)
        wants_link = bool(LINK_WORD_RE.search(text))

        matching_assets = [_asset_label(row) for row in matches]
        graph_link_evidence = []
        node_evidence = []
        for row in matches:
            meta = _row_metadata(row)
            links = iter_pin_links(meta)
            if links:
                graph_link_evidence.append({"asset": _asset_label(row), "graph_links": links[:24]})
            for graph in meta.get("graphs") or []:
                if not isinstance(graph, dict):
                    continue
                for node in graph.get("nodes") or []:
                    if isinstance(node, dict) and identifiers:
                        node_blob = json.dumps(node, ensure_ascii=False, default=str).lower()
                        if any(identifier.lower() in node_blob for identifier in identifiers):
                            node_evidence.append({"asset": _asset_label(row), "node": node})

        if not rows:
            verdict = "no_metadata"
        elif wants_link and not graph_link_evidence:
            verdict = "unsupported"
        elif matching_assets or node_evidence or graph_link_evidence:
            verdict = "supported_partial" if wants_link and graph_link_evidence else "supported"
        else:
            verdict = "unsupported"

        results.append(
            {
                "claim": text[:500],
                "verdict": verdict,
                "coverage": _coverage_for_matches(matches),
                "identifiers": identifiers,
                "assetExists": bool(matching_assets),
                "nodeEvidenceCount": len(node_evidence),
                "pinLinkEvidenceCount": len(graph_link_evidence),
                "matchingAssets": list(dict.fromkeys(matching_assets))[:8],
                "evidence": {
                    "nodes": node_evidence[:3],
                    "pinLinks": graph_link_evidence[:3],
                },
                "notes": [] if rows else ["No raw_blueprint_metadata.jsonl found. Run Editor metadata export before verifying Blueprint wiring."],
            }
        )

    unsupported = sum(1 for r in results if r["verdict"] in {"unsupported", "no_metadata"})
    return {
        "ok": unsupported == 0,
        "indexDir": str(idx),
        "projectName": active_name,
        "metadataRows": len(rows),
        "claimCount": len(results),
        "unsupportedCount": unsupported,
        "results": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Blueprint claims against exported metadata.")
    parser.add_argument("--claim", action="append", default=[])
    parser.add_argument("--claims-file", default="")
    parser.add_argument("--index-dir", default="data/unreal58")
    parser.add_argument("--project-name", default="")
    args = parser.parse_args()
    claims = list(args.claim or [])
    if args.claims_file:
        data = json.loads(Path(args.claims_file).read_text(encoding="utf-8-sig"))
        if isinstance(data, list):
            claims.extend(str(x) for x in data)
        elif isinstance(data, dict):
            claims.extend(str(x) for x in data.get("claims", []))
    payload = validate_blueprint_claims(claims, args.index_dir, args.project_name or None)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
