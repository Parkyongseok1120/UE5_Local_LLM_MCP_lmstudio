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


def graph_lookup_guidance(*, asset_class: str = "", asset_path: str = "") -> list[str]:
    data = load_taxonomy()
    levels = data.get("rag_coverage_levels") or {}
    info = classify_ue_asset_class(asset_class)
    actions: list[str] = []

    if info:
        coverage = info["rag_coverage"]
        level_desc = str(levels.get(coverage) or coverage)
        actions.append(
            f"Asset class '{asset_class}' is taxonomy '{info['item_name']}' "
            f"({info['section_title']}). RAG coverage: {coverage} — {level_desc}"
        )
        if coverage == "graph_material":
            actions.append(
                "Material, MaterialInstance, MaterialFunction, MaterialLayer, and MPC export via unreal_material_metadata."
            )
        elif coverage == "structured_metadata":
            actions.append("Use unreal_asset_graph_lookup or unreal_rag_search; structured fields export via unreal_structured_metadata.")
        elif coverage == "texture_metadata":
            actions.append("Texture settings export via unreal_texture_metadata (width/height/compression/sRGB).")
        elif coverage == "mesh_metadata":
            actions.append("Mesh slots/LOD/Nanite export via unreal_mesh_metadata.")
        elif coverage == "world_look_metadata":
            actions.append("PostProcess/Sky/Fog export via unreal_world_look_metadata.")
        elif coverage == "fmod_metadata":
            actions.append("FMOD assets export via unreal_fmod_metadata when FMOD plugin is present.")
        elif coverage == "registry":
            actions.append("Use unreal_rag_search mode=material_analysis or registry path; graph lookup will not find this type yet.")
        elif coverage == "not_exported_yet":
            actions.append("This asset family is not in the Editor export pipeline yet.")
    elif asset_path:
        actions.append(f"No taxonomy mapping for asset class '{asset_class or 'unknown'}'. Check unreal_asset_registry in RAG.")

    actions.append("See RAG_Project_Guidelines/Unreal_Programming/22_Unreal_Asset_Taxonomy_For_Production_Work.md")
    return actions


def work_domain_label(domain_id: str) -> str:
    for row in load_taxonomy().get("work_domains") or []:
        if str(row.get("id") or "") == domain_id:
            return str(row.get("label_ko") or domain_id)
    return domain_id
