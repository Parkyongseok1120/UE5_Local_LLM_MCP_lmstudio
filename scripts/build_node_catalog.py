#!/usr/bin/env python
"""Aggregate Blueprint/Material node classes and pins into a node catalog."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from blueprint_graph_format import iter_graph_nodes, iter_pin_links

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


def aggregate_blueprint_nodes(rows: list[dict[str, Any]]) -> dict[str, Any]:
    classes: dict[str, dict[str, Any]] = {}
    for row in rows:
        meta = _row_metadata(row)
        for node in iter_graph_nodes(meta):
            cls = str(node.get("class") or node.get("node_class") or "").strip()
            if not cls:
                continue
            entry = classes.setdefault(
                cls,
                {"class": cls, "seenInAssets": set(), "pinNames": set(), "pinTypes": set(), "count": 0},
            )
            entry["count"] += 1
            asset = str(meta.get("asset_path") or row.get("title") or "")
            if asset:
                entry["seenInAssets"].add(asset)
            for pin in node.get("pins") or []:
                if not isinstance(pin, dict):
                    continue
                name = str(pin.get("name") or "").strip()
                if name:
                    entry["pinNames"].add(name)
                pin_type = str(pin.get("type") or pin.get("pin_type") or "").strip()
                if pin_type:
                    entry["pinTypes"].add(pin_type)
        for link in iter_pin_links(meta):
            for key in ("from_pin", "to_pin"):
                pin_name = str(link.get(key) or "").strip()
                if pin_name:
                    node_cls = str(link.get("from_node_class") or link.get("class") or "Unknown")
                    entry = classes.setdefault(
                        node_cls,
                        {"class": node_cls, "seenInAssets": set(), "pinNames": set(), "pinTypes": set(), "count": 0},
                    )
                    entry["pinNames"].add(pin_name)

    out: dict[str, Any] = {}
    for cls, entry in sorted(classes.items()):
        out[cls] = {
            "class": cls,
            "count": entry["count"],
            "seenInAssets": sorted(entry["seenInAssets"])[:32],
            "pinNames": sorted(entry["pinNames"])[:64],
            "pinTypes": sorted(entry["pinTypes"])[:32],
        }
    return out


def aggregate_material_expressions(rows: list[dict[str, Any]]) -> dict[str, Any]:
    classes: dict[str, dict[str, Any]] = {}
    for row in rows:
        meta = _row_metadata(row)
        asset = str(meta.get("asset_path") or row.get("title") or "")
        for expression in meta.get("expressions") or []:
            if not isinstance(expression, dict):
                continue
            cls = str(expression.get("class") or "").strip()
            if not cls:
                continue
            entry = classes.setdefault(
                cls,
                {"class": cls, "seenInAssets": set(), "inputPins": set(), "count": 0},
            )
            entry["count"] += 1
            if asset:
                entry["seenInAssets"].add(asset)
            wires = expression.get("input_wires") or {}
            if isinstance(wires, dict):
                for pin_name in wires:
                    entry["inputPins"].add(str(pin_name))
        for edge in meta.get("graph_edges") or []:
            if not isinstance(edge, dict):
                continue
            target_input = str(edge.get("to_input") or edge.get("input") or "").strip()
            if target_input:
                cls = str(edge.get("to_class") or "MaterialExpression")
                entry = classes.setdefault(
                    cls,
                    {"class": cls, "seenInAssets": set(), "inputPins": set(), "count": 0},
                )
                entry["inputPins"].add(target_input)

    out: dict[str, Any] = {}
    for cls, entry in sorted(classes.items()):
        out[cls] = {
            "class": cls,
            "count": entry["count"],
            "seenInAssets": sorted(entry["seenInAssets"])[:32],
            "inputPins": sorted(entry["inputPins"])[:64],
        }
    return out


def build_node_catalog(data_dir: Path) -> dict[str, Any]:
    bp_rows = _load_jsonl(data_dir / "raw_blueprint_metadata.jsonl")
    mat_rows = _load_jsonl(data_dir / "raw_material_metadata.jsonl")
    blueprint_nodes = aggregate_blueprint_nodes(bp_rows)
    material_expressions = aggregate_material_expressions(mat_rows)
    pin_index: dict[str, list[str]] = defaultdict(list)
    for cls, entry in blueprint_nodes.items():
        for pin in entry.get("pinNames") or []:
            if cls not in pin_index[pin]:
                pin_index[pin].append(cls)
    return {
        "blueprintNodeClasses": blueprint_nodes,
        "materialExpressionClasses": material_expressions,
        "pinNameIndex": dict(sorted(pin_index.items())),
        "stats": {
            "blueprintRows": len(bp_rows),
            "materialRows": len(mat_rows),
            "blueprintNodeClasses": len(blueprint_nodes),
            "materialExpressionClasses": len(material_expressions),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build node catalog from editor metadata exports.")
    parser.add_argument("--data-dir", default="data/unreal58")
    parser.add_argument("--out", default="")
    args = parser.parse_args()
    workspace = Path(__file__).resolve().parent.parent
    data_dir = Path(args.data_dir)
    if not data_dir.is_absolute():
        data_dir = workspace / data_dir
    catalog = build_node_catalog(data_dir)
    out_path = Path(args.out) if args.out else data_dir / "node_catalog.json"
    if not out_path.is_absolute():
        out_path = workspace / out_path
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"done: {out_path} ({catalog['stats']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
