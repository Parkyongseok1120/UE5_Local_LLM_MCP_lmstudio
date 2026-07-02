#!/usr/bin/env python
"""Validate material graph claims against exported material metadata."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from material_graph_format import material_row_search_text
from project_row_filter import filter_rows_by_project
from workspace_paths import load_shared_config

MATERIAL_IDENT_RE = re.compile(
    r"\b(?:M_[A-Za-z0-9_]+|MI_[A-Za-z0-9_]+|Material(?:Instance)?(?:Constant)?_[A-Za-z0-9_]+)\b|/Game/[A-Za-z0-9_/]+",
    re.IGNORECASE,
)
EXPRESSION_CLASS_RE = re.compile(r"\bMaterialExpression[A-Za-z0-9_]+\b")
WIRE_WORD_RE = re.compile(
    r"\b(wire|wiring|connect(?:ed|ion)?|pipeline|input|output|expression|node|multiply|add|lerp|texture\s*sample)\b|와이어|연결|파이프|노드",
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
        haystack = material_row_search_text(meta).lower()
        label = _asset_label(row).lower()
        if identifiers and not any(
            identifier.lower() in haystack or identifier.lower() in label for identifier in identifiers
        ):
            continue
        matches.append(row)
    return matches


def validate_material_claims(
    claims: list[str],
    index_dir: str | Path | None = None,
    project_name: str | None = None,
) -> dict[str, Any]:
    idx = _resolve_index_dir(index_dir)
    rows = _load_jsonl(idx / "raw_material_metadata.jsonl")
    active_name = project_name or _active_project_name()
    if active_name:
        rows = filter_rows_by_project(rows, active_name)

    results: list[dict[str, Any]] = []
    for claim in claims:
        text = str(claim or "").strip()
        if not text:
            continue
        identifiers = list(dict.fromkeys(MATERIAL_IDENT_RE.findall(text)))
        class_names = list(dict.fromkeys(EXPRESSION_CLASS_RE.findall(text)))
        matches = _matching_rows(rows, identifiers or class_names)
        wants_wires = bool(WIRE_WORD_RE.search(text))

        matching_assets = [_asset_label(row) for row in matches]
        wire_evidence = []
        expression_evidence = []
        for row in matches:
            meta = _row_metadata(row)
            if meta.get("graph_edges"):
                wire_evidence.append(
                    {
                        "asset": _asset_label(row),
                        "graph_edges": meta.get("graph_edges"),
                        "root_outputs": meta.get("root_outputs"),
                    }
                )
            if meta.get("expressions"):
                expression_evidence.append(
                    {
                        "asset": _asset_label(row),
                        "expressions": meta.get("expressions"),
                    }
                )

        if not rows:
            verdict = "no_metadata"
        elif wants_wires and not wire_evidence:
            verdict = "unsupported"
        elif matching_assets or expression_evidence:
            verdict = "supported_partial" if wants_wires and wire_evidence else "supported"
        else:
            verdict = "unsupported"

        results.append(
            {
                "claim": text,
                "verdict": verdict,
                "identifiers": identifiers,
                "expressionClasses": class_names,
                "matchingAssets": matching_assets[:8],
                "wireEvidence": wire_evidence[:4],
                "expressionEvidence": expression_evidence[:4],
                "notes": [] if rows else ["No raw_material_metadata.jsonl found. Export material metadata from Unreal Editor first."],
            }
        )

    return {
        "ok": all(item["verdict"] in {"supported", "supported_partial"} for item in results) if results else False,
        "projectName": active_name,
        "indexDir": str(idx),
        "results": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate material graph claims against exported metadata.")
    parser.add_argument("--claim", action="append", default=[])
    parser.add_argument("--index-dir", default="data/unreal58")
    parser.add_argument("--project-name", default="")
    args = parser.parse_args()
    payload = validate_material_claims(args.claim, args.index_dir, args.project_name or None)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
