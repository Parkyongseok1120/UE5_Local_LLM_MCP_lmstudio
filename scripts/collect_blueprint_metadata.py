#!/usr/bin/env python
"""Ingest Blueprint metadata JSONL exported from Unreal Editor."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


def stable_id(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()


def load_export(path: Path) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            items.append(json.loads(line))
    return items


def to_rag_doc(item: dict[str, Any], project_name: str) -> dict[str, Any]:
    asset_path = str(item.get("asset_path") or item.get("path") or "")
    asset_type = str(item.get("asset_type") or item.get("type") or "Blueprint")
    generated_class = str(item.get("generated_class") or item.get("class") or "")
    parent = str(item.get("parent_class") or "")
    lines = [
        f"Blueprint metadata: {asset_path}",
        f"Asset type: {asset_type}",
        f"Generated class: {generated_class}",
        f"Parent class: {parent}",
    ]
    for key in ("components", "variables", "functions", "interfaces"):
        vals = item.get(key) or []
        if vals:
            lines.append(f"{key}: {', '.join(str(v) for v in vals[:30])}")
    text = "\n".join(lines)
    return {
        "id": stable_id(f"bp:{project_name}:{asset_path}"),
        "source": "unreal_blueprint_metadata",
        "path": asset_path,
        "title": f"{asset_type}: {generated_class or Path(asset_path).stem}",
        "text": text,
        "metadata": {
            "project": project_name,
            "asset_path": asset_path,
            "asset_type": asset_type,
            "generated_class": generated_class,
            "parent_class": parent,
            "extension": ".uasset",
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Convert Blueprint metadata export to RAG JSONL.")
    parser.add_argument("--in", dest="input_path", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--project", default="UnknownProject")
    args = parser.parse_args()

    items = load_export(Path(args.input_path))
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as handle:
        for item in items:
            handle.write(json.dumps(to_rag_doc(item, args.project), ensure_ascii=False) + "\n")
    print(f"Wrote {len(items)} blueprint metadata records to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
