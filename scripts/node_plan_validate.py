#!/usr/bin/env python
"""Validate Blueprint/Material node plans against the node catalog."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _resolve_catalog_path(catalog_path: str | Path | None) -> Path:
    workspace = Path(__file__).resolve().parent.parent
    if catalog_path:
        path = Path(catalog_path)
        return path if path.is_absolute() else workspace / path
    return workspace / "data" / "unreal58" / "node_catalog.json"


def _load_catalog(catalog_path: Path) -> dict[str, Any]:
    if not catalog_path.is_file():
        return {}
    try:
        data = json.loads(catalog_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _normalize_nodes(plan: dict[str, Any]) -> list[dict[str, Any]]:
    nodes = plan.get("nodes") or plan.get("steps") or []
    if isinstance(nodes, dict):
        return [nodes]
    if not isinstance(nodes, list):
        return []
    return [item for item in nodes if isinstance(item, dict)]


def validate_node_plan(
    plan: dict[str, Any],
    *,
    catalog_path: str | Path | None = None,
    domain: str = "auto",
) -> dict[str, Any]:
    """Return verdicts for each planned node against node_catalog.json."""
    catalog = _load_catalog(_resolve_catalog_path(catalog_path))
    bp_catalog = catalog.get("blueprintNodeClasses") or {}
    mat_catalog = catalog.get("materialExpressionClasses") or {}
    nodes = _normalize_nodes(plan)
    results: list[dict[str, Any]] = []

    for index, node in enumerate(nodes):
        cls = str(node.get("class") or node.get("nodeClass") or node.get("expressionClass") or "").strip()
        kind = str(node.get("kind") or node.get("domain") or domain or "auto").strip().lower()
        pins = [str(p) for p in (node.get("pins") or node.get("inputPins") or []) if p]
        label = str(node.get("name") or node.get("label") or cls or f"node_{index}")

        if not cls:
            results.append(
                {
                    "index": index,
                    "label": label,
                    "verdict": "invalid",
                    "class": "",
                    "notes": ["Missing node class."],
                }
            )
            continue

        is_material = kind == "material" or cls.startswith("MaterialExpression")
        catalog_entry = (mat_catalog if is_material else bp_catalog).get(cls)
        if not catalog and not bp_catalog and not mat_catalog:
            verdict = "unknown_catalog"
            notes = ["node_catalog.json missing; run scripts/build_node_catalog.py first."]
        elif catalog_entry:
            missing_pins = [
                pin for pin in pins if pin not in (catalog_entry.get("pinNames") or catalog_entry.get("inputPins") or [])
            ]
            if pins and missing_pins:
                verdict = "partial"
                notes = [f"Pin(s) not seen in catalog: {', '.join(missing_pins[:8])}"]
            else:
                verdict = "supported"
                notes = []
        else:
            verdict = "unsupported"
            notes = [f"Class {cls} not found in catalog."]

        results.append(
            {
                "index": index,
                "label": label,
                "verdict": verdict,
                "class": cls,
                "domain": "material" if is_material else "blueprint",
                "pins": pins,
                "catalogHit": bool(catalog_entry),
                "notes": notes,
            }
        )

    bad = sum(1 for item in results if item["verdict"] in {"unsupported", "invalid", "unknown_catalog"})
    return {
        "ok": bad == 0 and bool(results),
        "catalogPath": str(_resolve_catalog_path(catalog_path)),
        "catalogLoaded": bool(catalog),
        "nodeCount": len(results),
        "unsupportedCount": bad,
        "results": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate a node plan JSON against node_catalog.json.")
    parser.add_argument("--plan", required=True, help="Path to node plan JSON file.")
    parser.add_argument("--catalog", default="")
    parser.add_argument("--domain", default="auto", choices=["auto", "blueprint", "material"])
    args = parser.parse_args()
    plan_path = Path(args.plan)
    plan = json.loads(plan_path.read_text(encoding="utf-8-sig"))
    payload = validate_node_plan(plan, catalog_path=args.catalog or None, domain=args.domain)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
