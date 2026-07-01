#!/usr/bin/env python
"""Validate Blueprint wiring claims against exported Blueprint metadata."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from workspace_paths import load_shared_config

IDENT_RE = re.compile(r"\b(?:BP_[A-Za-z0-9_]+|WBP_[A-Za-z0-9_]+|[A-Z][A-Za-z0-9_]*(?:Blueprint|Widget|Component|Actor|Character|Controller|GameMode)?|IA_[A-Za-z0-9_]+)\b")
LINK_WORD_RE = re.compile(r"\b(pin|link|linked|connect|connected|wiring|calls?|bound|binding|event)\b|핀|연결|바인딩|호출", re.IGNORECASE)


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


def _row_text(row: dict[str, Any]) -> str:
    meta = _row_metadata(row)
    return json.dumps(meta, ensure_ascii=False, default=str) + "\n" + str(row.get("text") or "")


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


def validate_blueprint_claims(
    claims: list[str],
    index_dir: str | Path | None = None,
    project_name: str | None = None,
) -> dict[str, Any]:
    idx = _resolve_index_dir(index_dir)
    rows = _load_jsonl(idx / "raw_blueprint_metadata.jsonl")
    active_name = project_name or _active_project_name()
    if active_name:
        rows = [r for r in rows if str(_row_metadata(r).get("project") or r.get("project") or "") in {"", active_name}]

    results: list[dict[str, Any]] = []
    for claim in claims:
        text = str(claim or "").strip()
        if not text:
            continue
        identifiers = list(dict.fromkeys(IDENT_RE.findall(text)))
        matching_assets = []
        matching_nodes = []
        matching_pins = []
        matching_functions = []
        for row in rows:
            meta = _row_metadata(row)
            haystack = _row_text(row).lower()
            label = _asset_label(row)
            if identifiers and not any(identifier.lower() in haystack or identifier.lower() in label.lower() for identifier in identifiers):
                continue
            matching_assets.append(label)
            for key, sink in (("nodes", matching_nodes), ("pins", matching_pins), ("functions", matching_functions)):
                value = meta.get(key)
                if value and any(identifier.lower() in json.dumps(value, ensure_ascii=False, default=str).lower() for identifier in identifiers):
                    sink.append({"asset": label, key: value})

        wants_link = bool(LINK_WORD_RE.search(text))
        pin_link_evidence = []
        for item in matching_pins:
            pins = item.get("pins")
            pin_text = json.dumps(pins, ensure_ascii=False, default=str).lower()
            if any(marker in pin_text for marker in ("linked", "link", "connected", "links", "to_pin", "to_node")):
                pin_link_evidence.append(item)

        if not rows:
            verdict = "no_metadata"
        elif wants_link and not pin_link_evidence:
            verdict = "unsupported"
        elif matching_assets or matching_nodes or matching_functions or matching_pins:
            verdict = "supported_partial" if wants_link and pin_link_evidence else "supported"
        else:
            verdict = "unsupported"

        results.append(
            {
                "claim": text[:500],
                "verdict": verdict,
                "identifiers": identifiers,
                "assetExists": bool(matching_assets),
                "nodeEvidenceCount": len(matching_nodes),
                "pinEvidenceCount": len(matching_pins),
                "pinLinkEvidenceCount": len(pin_link_evidence),
                "functionEvidenceCount": len(matching_functions),
                "matchingAssets": list(dict.fromkeys(matching_assets))[:8],
                "evidence": {
                    "nodes": matching_nodes[:3],
                    "pins": matching_pins[:3],
                    "pinLinks": pin_link_evidence[:3],
                    "functions": matching_functions[:3],
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