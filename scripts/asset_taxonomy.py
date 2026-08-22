#!/usr/bin/env python
"""Unreal asset taxonomy — work-domain classification and RAG coverage hints."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
TAXONOMY_PATH = ROOT / "config" / "unreal_asset_taxonomy.json"


@lru_cache(maxsize=1)
def load_taxonomy() -> dict[str, Any]:
    if not TAXONOMY_PATH.is_file():
        return {}
    return json.loads(TAXONOMY_PATH.read_text(encoding="utf-8-sig"))


@lru_cache(maxsize=1)
def _class_index() -> dict[str, dict[str, Any]]:
    data = load_taxonomy()
    index: dict[str, dict[str, Any]] = {}
    for section in data.get("sections") or []:
        section_id = str(section.get("id") or "")
        section_title = str(section.get("title_ko") or "")
        work_domain = str(section.get("work_domain") or "")
        for item in section.get("items") or []:
            payload = {
                "section_id": section_id,
                "section_title": section_title,
                "work_domain": work_domain,
                "item_name": str(item.get("name") or ""),
                "description_ko": str(item.get("description_ko") or ""),
                "examples": list(item.get("examples") or []),
                "rag_coverage": str(item.get("rag_coverage") or ""),
                "npr_relevant": bool(item.get("npr_relevant")),
            }
            for cls in item.get("ue_asset_classes") or []:
                key = str(cls).strip()
                if key:
                    index[key] = payload
    return index


def classify_ue_asset_class(asset_class: str) -> dict[str, Any] | None:
    key = str(asset_class or "").strip()
    if not key:
        return None
    return _class_index().get(key)


def taxonomy_text_lines(asset_class: str) -> list[str]:
    info = classify_ue_asset_class(asset_class)
    if not info:
        return []
    lines = [
        f"taxonomy_item: {info['item_name']}",
        f"taxonomy_section: {info['section_title']}",
        f"work_domain: {info['work_domain']}",
        f"rag_coverage: {info['rag_coverage']}",
    ]
    if info.get("npr_relevant"):
        lines.append("npr_relevant: true")
    if info.get("description_ko"):
        lines.append(f"taxonomy_description: {info['description_ko']}")
    return lines


def work_domain_label(domain_id: str) -> str:
    for row in load_taxonomy().get("work_domains") or []:
        if str(row.get("id") or "") == domain_id:
            return str(row.get("label_ko") or domain_id)
    return domain_id
